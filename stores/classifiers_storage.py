import sqlite3
from pathlib import Path
from typing import Any, Literal

TableName = Literal["responsibles", "quality_items", "services"]


class ClassifiersStorage:
    """Управляет таблицами классификаторов в SQLite."""

    TABLE_RESPONSIBLES: TableName = "responsibles"
    TABLE_QUALITY_ITEMS: TableName = "quality_items"
    TABLE_SERVICES: TableName = "services"

    _TABLES: dict[str, dict[str, tuple[str, ...]]] = {
        "responsibles": {
            "columns": ("name", "fio"),
            "export_columns": ("id", "name"),
        },
        "quality_items": {
            "columns": ("name", "importance", "responsible_id"),
            "export_columns": ("id", "name"),
        },
        "services": {
            "columns": ("name", "responsible_id"),
            "export_columns": ("id", "name"),
        },
    }

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            self._ensure_tables(connection)

    def clear(self, table: TableName) -> None:
        """Удаляет все строки из указанной таблицы."""
        table_name = self._validate_table(table)
        with self._connect() as connection:
            connection.execute(f"DELETE FROM {table_name}")

    def upsert(self, table: TableName, values: dict[str, Any]) -> bool:
        """
        Добавляет или обновляет запись в указанной таблице.

        Returns:
            True — запись добавлена, False — существующая запись обновлена.
        """
        table_name = self._validate_table(table)
        if not values:
            raise ValueError("values не может быть пустым")

        table_meta = self._TABLES[table_name]
        allowed_fields = set(table_meta["columns"])
        unknown_fields = set(values) - allowed_fields - {"id"}
        if unknown_fields:
            raise ValueError(
                f"Недопустимые поля для таблицы {table_name!r}: "
                f"{', '.join(sorted(unknown_fields))}"
            )

        record_id = values.get("id")
        field_names = [name for name in table_meta["columns"] if name in values]
        if not field_names and record_id is None:
            raise ValueError("values должны содержать id и/или поля записи")

        with self._connect() as connection:
            exists = False
            if record_id is not None:
                cursor = connection.execute(
                    f"SELECT 1 FROM {table_name} WHERE id = ?",
                    (record_id,),
                )
                exists = cursor.fetchone() is not None

            if exists:
                if field_names:
                    assignments = ", ".join(f"{name} = ?" for name in field_names)
                    params = [values[name] for name in field_names] + [record_id]
                    connection.execute(
                        f"UPDATE {table_name} SET {assignments} WHERE id = ?",
                        params,
                    )
                return False

            if record_id is not None:
                columns = ["id", *field_names]
                placeholders = ", ".join("?" for _ in columns)
                params = [record_id, *[values[name] for name in field_names]]
            else:
                columns = field_names
                placeholders = ", ".join("?" for _ in columns)
                params = [values[name] for name in field_names]

            columns_sql = ", ".join(columns)
            connection.execute(
                f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})",
                params,
            )
            return True

    def delete(self, table: TableName, id: int) -> None:
        """Удаляет запись с указанным id из таблицы."""
        table_name = self._validate_table(table)
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM {table_name} WHERE id = ?",
                (id,),
            )

    def count(self, table: TableName) -> int:
        """Возвращает количество строк в указанной таблице."""
        table_name = self._validate_table(table)
        with self._connect() as connection:
            cursor = connection.execute(f"SELECT COUNT(*) FROM {table_name}")
            return int(cursor.fetchone()[0])

    def get(self, table: TableName, id: int) -> dict[str, Any] | None:
        """Возвращает запись с указанным id или None, если запись не найдена."""
        table_name = self._validate_table(table)
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                f"SELECT * FROM {table_name} WHERE id = ?",
                (id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def export(self, table: TableName) -> list[dict[str, Any]]:
        """Возвращает записи таблицы (поля id и name) в виде списка словарей."""
        table_name = self._validate_table(table)
        export_columns = self._TABLES[table_name]["export_columns"]
        columns_sql = ", ".join(export_columns)

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                f"SELECT {columns_sql} FROM {table_name} ORDER BY id"
            )
            return [dict(row) for row in cursor.fetchall()]

    def _validate_table(self, table: str) -> str:
        if table not in self._TABLES:
            raise ValueError(
                f"Недопустимая таблица: {table!r}. "
                f"Допустимые значения: {', '.join(self._TABLES)}"
            )
        return table

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_tables(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS responsibles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                fio TEXT
            );

            CREATE TABLE IF NOT EXISTS quality_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                importance INTEGER,
                responsible_id INTEGER,
                FOREIGN KEY (responsible_id) REFERENCES responsibles(id)
            );

            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                responsible_id INTEGER,
                FOREIGN KEY (responsible_id) REFERENCES responsibles(id)
            );
            """
        )
