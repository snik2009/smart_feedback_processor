import re
import sqlite3
from pathlib import Path


class ItemsStore:
    """Управляет таблицами SQLite для хранения элементов аналитики."""

    _TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def __init__(self, db_path: str, table_name: str) -> None:
        if not table_name:
            raise ValueError("table_name обязателен и не может быть пустым")
        if not self._TABLE_NAME_PATTERN.match(table_name):
            raise ValueError(
                f"Недопустимое имя таблицы: {table_name!r}. "
                "Допустимы буквы, цифры и символ подчёркивания; "
                "имя должно начинаться с буквы или _"
            )

        self.db_path = db_path
        self.table_name = table_name

        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            self._ensure_table(connection)

    def clear(self) -> None:
        """Удаляет все строки из таблицы."""
        with self._connect() as connection:
            connection.execute(f"DELETE FROM {self.table_name}")

    def upsert(self, id: int, name: str) -> bool:
        """
        Добавляет или обновляет запись.

        Returns:
            True — запись добавлена, False — существующая запись обновлена.
        """
        if not name:
            raise ValueError("name обязателен и не может быть пустым")

        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT 1 FROM {self.table_name} WHERE id = ?",
                (id,),
            )
            exists = cursor.fetchone() is not None

            if exists:
                connection.execute(
                    f"UPDATE {self.table_name} SET name = ? WHERE id = ?",
                    (name, id),
                )
                return False

            connection.execute(
                f"INSERT INTO {self.table_name} (id, name) VALUES (?, ?)",
                (id, name),
            )
            return True

    def delete(self, id: int) -> None:
        """Удаляет запись с указанным id."""
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM {self.table_name} WHERE id = ?",
                (id,),
            )

    def count(self) -> int:
        """Возвращает количество строк в таблице."""
        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT COUNT(*) FROM {self.table_name}"
            )
            return int(cursor.fetchone()[0])

    def get(self, id: int) -> str | None:
        """Возвращает name записи с указанным id или None, если запись не найдена."""
        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT name FROM {self.table_name} WHERE id = ?",
                (id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def export(self) -> list[dict[str, int | str]]:
        """Возвращает все записи таблицы в виде списка словарей."""
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                f"SELECT id, name FROM {self.table_name} ORDER BY id"
            )
            return [dict(row) for row in cursor.fetchall()]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id INTEGER NOT NULL PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
