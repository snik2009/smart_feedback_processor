import json
import sqlite3
from pathlib import Path
from typing import Any


class FeedbacksStorage:
    """Управляет таблицами отзывов клиентов в SQLite."""

    _FEEDBACK_FIELDS = (
        "feedback",
        "parsed_feedback",
        "response",
        "total_elements",
        "total_positives",
        "total_negatives",
        "tone",
        "form",
        "style",
        "total_rating",
    )

    _SCALAR_JSON_FIELDS = (
        "total_elements",
        "total_positives",
        "total_negatives",
        "tone",
        "form",
        "style",
        "total_rating",
    )

    _CHILD_TABLES = (
        "feedback_quality_items",
        "feedback_adv_quality_items",
        "requests",
        "adv_requests",
        "positives",
        "negatives",
    )

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            self._ensure_tables(connection)

    def clear(self) -> None:
        """Удаляет все строки из всех таблиц отзывов."""
        with self._connect() as connection:
            for table in (*self._CHILD_TABLES, "feedbacks"):
                connection.execute(f"DELETE FROM {table}")

    def upsert(self, values: dict[str, Any]) -> bool:
        """
        Добавляет или обновляет отзыв и связанные записи.

        Returns:
            True — отзыв добавлен, False — существующий отзыв обновлён.
        """
        if not values:
            raise ValueError("values не может быть пустым")
        if "feedback" not in values:
            raise ValueError("values должны содержать поле 'feedback'")

        feedback_id = values.get("id")
        feedback_row = self._build_feedback_row(values)

        with self._connect() as connection:
            exists = False
            if feedback_id is not None:
                cursor = connection.execute(
                    "SELECT 1 FROM feedbacks WHERE id = ?",
                    (feedback_id,),
                )
                exists = cursor.fetchone() is not None

            if exists:
                assignments = ", ".join(f"{name} = ?" for name in feedback_row)
                params = list(feedback_row.values()) + [feedback_id]
                connection.execute(
                    f"UPDATE feedbacks SET {assignments} WHERE id = ?",
                    params,
                )
                self._delete_children(connection, feedback_id)
                self._insert_children(connection, feedback_id, values)
                return False

            columns = list(feedback_row.keys())
            placeholders = ", ".join("?" for _ in columns)
            params = list(feedback_row.values())

            if feedback_id is not None:
                columns = ["id", *columns]
                placeholders = ", ".join("?" for _ in columns)
                params = [feedback_id, *params]

            cursor = connection.execute(
                f"INSERT INTO feedbacks ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                params,
            )
            new_id = feedback_id if feedback_id is not None else int(cursor.lastrowid)
            self._insert_children(connection, new_id, values)
            return True

    def delete(self, id: int) -> None:
        """Удаляет отзыв и все связанные с ним записи."""
        with self._connect() as connection:
            connection.execute("DELETE FROM feedbacks WHERE id = ?", (id,))

    def count(self) -> int:
        """Возвращает количество отзывов в таблице feedbacks."""
        with self._connect() as connection:
            cursor = connection.execute("SELECT COUNT(*) FROM feedbacks")
            return int(cursor.fetchone()[0])

    def get(self, id: int) -> dict[str, Any] | None:
        """Возвращает отзыв и связанные записи в формате JSON-структуры."""
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                "SELECT * FROM feedbacks WHERE id = ?",
                (id,),
            )
            feedback_row = cursor.fetchone()
            if feedback_row is None:
                return None

            result: dict[str, Any] = {
                "id": feedback_row["id"],
                "feedback": feedback_row["feedback"],
                "response": feedback_row["response"],
            }
            for field in self._SCALAR_JSON_FIELDS:
                result[field] = feedback_row[field]

            result["positives"] = self._fetch_positives_negatives(
                connection, "positives", id
            )
            result["negatives"] = self._fetch_positives_negatives(
                connection, "negatives", id
            )
            result["quality_items"] = self._fetch_quality_items(connection, id)
            result["adv_quality_items"] = self._fetch_adv_quality_items(connection, id)
            result["request"] = self._fetch_requests(connection, id)
            result["adv_request"] = self._fetch_adv_requests(connection, id)
            return result

    def _build_feedback_row(self, values: dict[str, Any]) -> dict[str, Any]:
        parsed_feedback = values.get("parsed_feedback")
        if parsed_feedback is None:
            parsed_payload = {
                key: values[key]
                for key in values
                if key
                not in (
                    "id",
                    "feedback",
                    "response",
                    "parsed_feedback",
                )
            }
            parsed_feedback = json.dumps(parsed_payload, ensure_ascii=False)
        elif not isinstance(parsed_feedback, str):
            parsed_feedback = json.dumps(parsed_feedback, ensure_ascii=False)

        row = {
            "feedback": values["feedback"],
            "parsed_feedback": parsed_feedback,
            "response": values.get("response"),
        }
        for field in self._SCALAR_JSON_FIELDS:
            row[field] = values.get(field)
        return row

    def _insert_children(
        self,
        connection: sqlite3.Connection,
        feedback_id: int,
        values: dict[str, Any],
    ) -> None:
        for item in values.get("positives", []):
            connection.execute(
                """
                INSERT INTO positives (feedback_id, name, vector, vector_name)
                VALUES (?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    item.get("name"),
                    self._serialize_vector(item.get("vector")),
                    item.get("vector_name"),
                ),
            )

        for item in values.get("negatives", []):
            connection.execute(
                """
                INSERT INTO negatives (feedback_id, name, vector, vector_name)
                VALUES (?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    item.get("name"),
                    self._serialize_vector(item.get("vector")),
                    item.get("vector_name"),
                ),
            )

        for item in values.get("quality_items", []):
            connection.execute(
                """
                INSERT INTO feedback_quality_items
                    (feedback_id, quality_item_id, rating)
                VALUES (?, ?, ?)
                """,
                (feedback_id, item["id"], item.get("rating")),
            )

        for item in values.get("adv_quality_items", []):
            connection.execute(
                """
                INSERT INTO feedback_adv_quality_items (feedback_id, name)
                VALUES (?, ?)
                """,
                (feedback_id, item.get("name")),
            )

        for item in values.get("request", []):
            connection.execute(
                """
                INSERT INTO requests (feedback_id, request_item_id, task)
                VALUES (?, ?, ?)
                """,
                (feedback_id, item["id"], item.get("task")),
            )

        for item in values.get("adv_request", []):
            connection.execute(
                """
                INSERT INTO adv_requests (feedback_id, task)
                VALUES (?, ?)
                """,
                (feedback_id, item.get("task")),
            )

    def _delete_children(
        self,
        connection: sqlite3.Connection,
        feedback_id: int,
    ) -> None:
        for table in self._CHILD_TABLES:
            connection.execute(
                f"DELETE FROM {table} WHERE feedback_id = ?",
                (feedback_id,),
            )

    def _fetch_positives_negatives(
        self,
        connection: sqlite3.Connection,
        table: str,
        feedback_id: int,
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(
            f"""
            SELECT name, vector, vector_name
            FROM {table}
            WHERE feedback_id = ?
            ORDER BY id
            """,
            (feedback_id,),
        )
        items: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            vector = row["vector"]
            if vector is not None:
                try:
                    vector = json.loads(vector)
                except json.JSONDecodeError:
                    pass
            items.append(
                {
                    "name": row["name"],
                    "vector": vector,
                    "vector_name": row["vector_name"],
                }
            )
        return items

    def _fetch_quality_items(
        self,
        connection: sqlite3.Connection,
        feedback_id: int,
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(
            """
            SELECT
                fqi.quality_item_id AS id,
                qi.name AS name,
                fqi.rating AS rating
            FROM feedback_quality_items fqi
            LEFT JOIN quality_items qi ON qi.id = fqi.quality_item_id
            WHERE fqi.feedback_id = ?
            ORDER BY fqi.id
            """,
            (feedback_id,),
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "rating": row["rating"],
            }
            for row in cursor.fetchall()
        ]

    def _fetch_adv_quality_items(
        self,
        connection: sqlite3.Connection,
        feedback_id: int,
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(
            """
            SELECT name
            FROM feedback_adv_quality_items
            WHERE feedback_id = ?
            ORDER BY id
            """,
            (feedback_id,),
        )
        return [{"name": row["name"]} for row in cursor.fetchall()]

    def _fetch_requests(
        self,
        connection: sqlite3.Connection,
        feedback_id: int,
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(
            """
            SELECT
                r.request_item_id AS id,
                qi.name AS name,
                r.task AS task
            FROM requests r
            LEFT JOIN quality_items qi ON qi.id = r.request_item_id
            WHERE r.feedback_id = ?
            ORDER BY r.id
            """,
            (feedback_id,),
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "task": row["task"],
            }
            for row in cursor.fetchall()
        ]

    def _fetch_adv_requests(
        self,
        connection: sqlite3.Connection,
        feedback_id: int,
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(
            """
            SELECT task
            FROM adv_requests
            WHERE feedback_id = ?
            ORDER BY id
            """,
            (feedback_id,),
        )
        return [
            {"name": "Прочее задание", "task": row["task"]}
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _serialize_vector(vector: Any) -> str | None:
        if vector is None:
            return None
        if isinstance(vector, str):
            return vector
        return json.dumps(vector, ensure_ascii=False)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_tables(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS feedbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback TEXT,
                parsed_feedback TEXT,
                response TEXT,
                total_elements INTEGER,
                total_positives INTEGER,
                total_negatives INTEGER,
                tone INTEGER,
                form INTEGER,
                style INTEGER,
                total_rating INTEGER
            );

            CREATE TABLE IF NOT EXISTS feedback_quality_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                quality_item_id INTEGER NOT NULL,
                rating INTEGER,
                FOREIGN KEY (feedback_id) REFERENCES feedbacks(id) ON DELETE CASCADE,
                FOREIGN KEY (quality_item_id) REFERENCES quality_items(id)
            );

            CREATE TABLE IF NOT EXISTS feedback_adv_quality_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                name TEXT,
                FOREIGN KEY (feedback_id) REFERENCES feedbacks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                request_item_id INTEGER NOT NULL,
                task TEXT,
                FOREIGN KEY (feedback_id) REFERENCES feedbacks(id) ON DELETE CASCADE,
                FOREIGN KEY (request_item_id) REFERENCES quality_items(id)
            );

            CREATE TABLE IF NOT EXISTS adv_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                task TEXT,
                FOREIGN KEY (feedback_id) REFERENCES feedbacks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS positives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                name TEXT,
                vector TEXT,
                vector_name TEXT,
                FOREIGN KEY (feedback_id) REFERENCES feedbacks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS negatives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                name TEXT,
                vector TEXT,
                vector_name TEXT,
                FOREIGN KEY (feedback_id) REFERENCES feedbacks(id) ON DELETE CASCADE
            );
            """
        )
