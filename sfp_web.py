#!/usr/bin/env python3
"""
Smart Feedback Processor — web-интерфейс для обработки отзывов клиентов.

Функционал:
  1. Загрузка отзывов из CSV в локальную базу данных SQLite.
  2. Обработка отзывов консольным приложением sfp_console.py и запись
     результата анализа и подготовленного ответа в ту же базу данных.
  3. Выгрузка отзывов вместе с результатами обработки и ответами в CSV.

Запуск:
    python sfp_web.py
    python sfp_web.py host=0.0.0.0 port=8080 settings_dir=data
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import logging
import sqlite3
import subprocess
import sys
import tempfile
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import analytics
import config
import dashboard
import dashboard_export
import pdfwriter

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
CONSOLE_SCRIPT = PROJECT_ROOT / "sfp_console.py"
TABLE_NAME = "web_feedbacks"
CSV_DELIMITER = ";"

STATUS_NEW = "new"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"

STATUS_LABELS = {
    STATUS_NEW: "Новый",
    STATUS_PROCESSING: "В обработке",
    STATUS_DONE: "Обработан",
    STATUS_ERROR: "Ошибка",
}

# Поля отзыва, которые понимает sfp_console.py (см. handlers/response_processor.py).
FEEDBACK_FIELDS = (
    "feedback_date",
    "client_id",
    "client_name",
    "feedback_text",
    "responsible_id",
    "corrective_actions",
)

# Скалярные показатели анализа, которые раскладываются по отдельным колонкам.
ANALYSIS_SCALARS = (
    "total_elements",
    "total_positives",
    "total_negatives",
    "tone",
    "form",
    "style",
    "mood",
    "total_rating",
)

# Списочные разделы анализа — хранятся и выгружаются как JSON.
ANALYSIS_LISTS = (
    "positives",
    "negatives",
    "questions",
    "quality_items",
    "adv_quality_items",
    "task_plan",
    "client_request",
)

PARAM_ALIASES = {
    "host": "host",
    "port": "port",
    "settings_dir": "settings_dir",
    "settings": "settings_dir",
    "db": "db_path",
    "db_path": "db_path",
    "open_browser": "open_browser",
    "browser": "open_browser",
}


# ---------------------------------------------------------------------------
# Хранилище
# ---------------------------------------------------------------------------


class WebFeedbacksStorage:
    """Таблица отзывов web-приложения в локальной базе SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uploaded_at TEXT NOT NULL,
                    source_file TEXT,
                    feedback_date TEXT,
                    client_id INTEGER,
                    client_name TEXT,
                    feedback_text TEXT NOT NULL,
                    responsible_id INTEGER,
                    corrective_actions TEXT,
                    status TEXT NOT NULL DEFAULT '{STATUS_NEW}',
                    processed_at TEXT,
                    analysis_json TEXT,
                    response_text TEXT,
                    error TEXT,
                    total_elements INTEGER,
                    total_positives INTEGER,
                    total_negatives INTEGER,
                    tone INTEGER,
                    form INTEGER,
                    style INTEGER,
                    mood INTEGER,
                    total_rating INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_status
                    ON {TABLE_NAME} (status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def add_many(self, records: list[dict[str, Any]], source_file: str) -> int:
        """Добавляет отзывы со статусом «Новый». Возвращает количество строк."""
        if not records:
            return 0

        uploaded_at = _now()
        columns = ("uploaded_at", "source_file", *FEEDBACK_FIELDS)
        placeholders = ", ".join("?" for _ in columns)
        rows = [
            (uploaded_at, source_file, *(record.get(name) for name in FEEDBACK_FIELDS))
            for record in records
        ]

        with self._connect() as connection:
            connection.executemany(
                f"INSERT INTO {TABLE_NAME} ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                rows,
            )
        return len(rows)

    def list_all(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT * FROM {TABLE_NAME} ORDER BY id"
            )
            return cursor.fetchall()

    def get(self, feedback_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT * FROM {TABLE_NAME} WHERE id = ?",
                (feedback_id,),
            )
            return cursor.fetchone()

    def pending_ids(self) -> list[int]:
        """Идентификаторы отзывов, ожидающих обработки (новые и с ошибкой)."""
        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT id FROM {TABLE_NAME} WHERE status IN (?, ?, ?) ORDER BY id",
                (STATUS_NEW, STATUS_ERROR, STATUS_PROCESSING),
            )
            return [int(row["id"]) for row in cursor.fetchall()]

    def counts_by_status(self) -> dict[str, int]:
        with self._connect() as connection:
            cursor = connection.execute(
                f"SELECT status, COUNT(*) AS amount FROM {TABLE_NAME} GROUP BY status"
            )
            counts = {status: 0 for status in STATUS_LABELS}
            for row in cursor.fetchall():
                counts[str(row["status"])] = int(row["amount"])
            return counts

    def mark_processing(self, feedback_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                f"UPDATE {TABLE_NAME} SET status = ?, error = NULL WHERE id = ?",
                (STATUS_PROCESSING, feedback_id),
            )

    def save_result(self, feedback_id: int, analysis: dict[str, Any]) -> None:
        """Записывает результат анализа отзыва и подготовленный ответ."""
        assignments = {
            "status": STATUS_DONE,
            "processed_at": _now(),
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "response_text": analysis.get("response_text") or "",
            "error": None,
        }
        for name in ANALYSIS_SCALARS:
            assignments[name] = _as_int_or_none(analysis.get(name))

        self._update(feedback_id, assignments)

    def save_error(self, feedback_id: int, message: str) -> None:
        self._update(
            feedback_id,
            {
                "status": STATUS_ERROR,
                "processed_at": _now(),
                "error": message,
            },
        )

    def clear(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(f"DELETE FROM {TABLE_NAME}")
            return int(cursor.rowcount)

    def _update(self, feedback_id: int, assignments: dict[str, Any]) -> None:
        columns = ", ".join(f"{name} = ?" for name in assignments)
        params = [*assignments.values(), feedback_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE {TABLE_NAME} SET {columns} WHERE id = ?",
                params,
            )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Загрузка CSV
# ---------------------------------------------------------------------------


def parse_feedbacks_csv(raw: bytes) -> list[dict[str, Any]]:
    """
    Разбирает загруженный CSV-файл с отзывами.

    Ожидаются колонки feedback_date, client_id, client_name, feedback_text,
    responsible_id, corrective_actions. Обязательна только feedback_text.
    """
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise ValueError("Файл пустой")

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=_detect_delimiter(text))
    if not reader.fieldnames:
        raise ValueError("Не удалось прочитать заголовок CSV-файла")

    headers = {(name or "").strip().lower(): name for name in reader.fieldnames}
    if "feedback_text" not in headers:
        raise ValueError(
            "В файле нет обязательной колонки feedback_text. "
            f"Найдены колонки: {', '.join(name for name in reader.fieldnames if name)}"
        )

    records: list[dict[str, Any]] = []
    for row in reader:
        record: dict[str, Any] = {}
        for field in FEEDBACK_FIELDS:
            source_key = headers.get(field)
            value = row.get(source_key) if source_key else None
            record[field] = (value or "").strip()

        if not record["feedback_text"]:
            continue

        record["client_id"] = _as_int_or_none(record["client_id"])
        record["responsible_id"] = _as_int_or_none(record["responsible_id"])
        records.append(record)

    if not records:
        raise ValueError("В файле не найдено ни одного непустого отзыва")

    return records


def _detect_delimiter(text: str) -> str:
    """Определяет разделитель по первой строке; по умолчанию — точка с запятой."""
    header = text.splitlines()[0] if text.splitlines() else ""
    counts = {delimiter: header.count(delimiter) for delimiter in (";", ",", "\t")}
    best = max(counts, key=lambda delimiter: counts[delimiter])
    return best if counts[best] > 0 else CSV_DELIMITER


# ---------------------------------------------------------------------------
# Обработка через консольное приложение sfp_console.py
# ---------------------------------------------------------------------------


class ConsoleProcessingError(RuntimeError):
    """Консольное приложение не смогло обработать отзыв."""


def process_feedback_with_console(
    feedback: dict[str, Any],
    settings_dir: Path,
    timeout: int = 600,
) -> dict[str, Any]:
    """
    Обрабатывает один отзыв консольным приложением sfp_console.py.

    Отзыв выкладывается во временный каталог в виде CSV, запускается
    sfp_console.py, после чего читается подготовленный им JSON-результат.
    """
    if not CONSOLE_SCRIPT.exists():
        raise ConsoleProcessingError(f"Не найдено консольное приложение {CONSOLE_SCRIPT}")

    with tempfile.TemporaryDirectory(prefix="sfp_web_") as temp_root:
        temp_path = Path(temp_root)
        input_dir = temp_path / "input"
        output_dir = temp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        input_file = input_dir / "feedback.csv"
        _write_console_input(input_file, feedback)

        command = [
            sys.executable,
            str(CONSOLE_SCRIPT),
            f"input_dir={input_dir}",
            f"settings_dir={settings_dir}",
            "format=json",
            f"output_dir={output_dir}",
        ]

        try:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise ConsoleProcessingError(
                f"Консольное приложение не ответило за {timeout} с"
            ) from error

        result_file = output_dir / "feedback.json"
        if completed.returncode != 0 or not result_file.exists():
            raise ConsoleProcessingError(_console_error_message(completed))

        try:
            results = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConsoleProcessingError(
                "Консольное приложение вернуло некорректный JSON"
            ) from error

        if not isinstance(results, list) or not results:
            raise ConsoleProcessingError("Консольное приложение вернуло пустой результат")

        return results[0]


def _write_console_input(input_file: Path, feedback: dict[str, Any]) -> None:
    with input_file.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=list(FEEDBACK_FIELDS),
            delimiter=CSV_DELIMITER,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                name: "" if feedback.get(name) is None else str(feedback[name])
                for name in FEEDBACK_FIELDS
            }
        )


def _console_error_message(completed: subprocess.CompletedProcess[str]) -> str:
    """Собирает читаемое сообщение об ошибке из вывода консольного приложения."""
    for stream in (completed.stderr, completed.stdout):
        lines = [line.strip() for line in (stream or "").splitlines() if line.strip()]
        meaningful = [line for line in lines if "[ERROR]" in line] or lines
        if meaningful:
            return "\n".join(meaningful[-8:])
    return f"sfp_console.py завершился с кодом {completed.returncode}"


# ---------------------------------------------------------------------------
# Фоновая обработка
# ---------------------------------------------------------------------------


class ProcessingJob:
    """Состояние фоновой обработки отзывов (обработка идёт не мгновенно)."""

    def __init__(self, storage: WebFeedbacksStorage, settings_dir: Path) -> None:
        self.storage = storage
        self.settings_dir = settings_dir
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.total = 0
        self.done = 0
        self.failed = 0
        self.message = ""

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            return {
                "running": running,
                "total": self.total,
                "done": self.done,
                "failed": self.failed,
                "message": self.message,
            }

    def start(self) -> str:
        """Запускает обработку всех необработанных отзывов."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return "Обработка уже выполняется"

            feedback_ids = self.storage.pending_ids()
            if not feedback_ids:
                self.message = "Нет отзывов, ожидающих обработки"
                return self.message

            self.total = len(feedback_ids)
            self.done = 0
            self.failed = 0
            self.message = f"Обработка запущена: отзывов — {self.total}"
            self._thread = threading.Thread(
                target=self._run,
                args=(feedback_ids,),
                daemon=True,
            )
            self._thread.start()
            return self.message

    def _run(self, feedback_ids: list[int]) -> None:
        for feedback_id in feedback_ids:
            row = self.storage.get(feedback_id)
            if row is None:
                continue

            self.storage.mark_processing(feedback_id)
            feedback = {name: row[name] for name in FEEDBACK_FIELDS}

            try:
                analysis = process_feedback_with_console(feedback, self.settings_dir)
            except Exception as error:  # noqa: BLE001 — любую ошибку показываем в UI
                logger.error("Отзыв %s не обработан: %s", feedback_id, error)
                self.storage.save_error(feedback_id, str(error))
                with self._lock:
                    self.failed += 1
            else:
                self.storage.save_result(feedback_id, analysis)
                logger.info("Отзыв %s обработан", feedback_id)

            with self._lock:
                self.done += 1
                self.message = f"Обработано {self.done} из {self.total}"

        with self._lock:
            self.message = (
                f"Обработка завершена: {self.done - self.failed} успешно, "
                f"{self.failed} с ошибкой"
            )


# ---------------------------------------------------------------------------
# Выгрузка результатов в CSV
# ---------------------------------------------------------------------------

EXPORT_COLUMNS = (
    "id",
    *FEEDBACK_FIELDS,
    "status",
    "uploaded_at",
    "processed_at",
    *ANALYSIS_SCALARS,
    *ANALYSIS_LISTS,
    "response_text",
    "error",
)


def build_export_csv(rows: list[sqlite3.Row]) -> bytes:
    """Формирует CSV с отзывами, результатами анализа и ответами."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(EXPORT_COLUMNS),
        delimiter=CSV_DELIMITER,
        lineterminator="\r\n",
    )
    writer.writeheader()

    for row in rows:
        analysis = _load_analysis(row["analysis_json"])
        record: dict[str, str] = {}

        for name in ("id", *FEEDBACK_FIELDS, "uploaded_at", "processed_at"):
            record[name] = "" if row[name] is None else str(row[name])

        record["status"] = STATUS_LABELS.get(str(row["status"]), str(row["status"]))

        for name in ANALYSIS_SCALARS:
            value = row[name] if row[name] is not None else analysis.get(name)
            record[name] = "" if value is None else str(value)

        for name in ANALYSIS_LISTS:
            value = analysis.get(name)
            record[name] = json.dumps(value, ensure_ascii=False) if value else ""

        record["response_text"] = row["response_text"] or ""
        record["error"] = row["error"] or ""
        writer.writerow(record)

    # BOM — чтобы Excel корректно открывал файл в UTF-8.
    return buffer.getvalue().encode("utf-8-sig")


def _load_analysis(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Разбор multipart/form-data
# ---------------------------------------------------------------------------


def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str | None, bytes]]:
    """Минимальный разбор multipart/form-data: имя поля → (имя файла, данные)."""
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("В запросе отсутствует граница multipart-формы")

    boundary = content_type.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
    separator = b"--" + boundary.encode("utf-8")

    fields: dict[str, tuple[str | None, bytes]] = {}
    for chunk in body.split(separator):
        if chunk in (b"", b"--", b"--\r\n", b"\r\n"):
            continue

        chunk = chunk.lstrip(b"\r\n")
        if chunk.startswith(b"--"):
            continue
        if b"\r\n\r\n" not in chunk:
            continue

        raw_headers, _, content = chunk.partition(b"\r\n\r\n")
        disposition = ""
        for header_line in raw_headers.decode("utf-8", errors="replace").splitlines():
            if header_line.lower().startswith("content-disposition:"):
                disposition = header_line
                break
        if not disposition:
            continue

        name = _disposition_value(disposition, "name")
        if name is None:
            continue

        fields[name] = (
            _disposition_value(disposition, "filename"),
            content[:-2] if content.endswith(b"\r\n") else content,
        )

    return fields


def _disposition_value(disposition: str, key: str) -> str | None:
    marker = f"{key}="
    for part in disposition.split(";"):
        part = part.strip()
        if part.startswith(marker):
            return part[len(marker):].strip().strip('"')
    return None


# ---------------------------------------------------------------------------
# HTML-представление
# ---------------------------------------------------------------------------

PAGE_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; padding: 32px; font-family: -apple-system, "Segoe UI", Roboto,
       Helvetica, Arial, sans-serif; background: #f4f6f8; color: #1f2933; }
main { max-width: 1180px; margin: 0 auto; }
h1 { margin: 0 0 4px; font-size: 24px; }
h2 { font-size: 17px; margin: 0 0 12px; }
.subtitle { margin: 0 0 24px; color: #6b7785; font-size: 14px; }
.panels { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 24px; }
.panel { flex: 1 1 280px; background: #fff; border: 1px solid #e1e6eb;
         border-radius: 10px; padding: 18px 20px; }
.counters { display: flex; gap: 20px; flex-wrap: wrap; }
.counter b { display: block; font-size: 22px; }
.counter span { color: #6b7785; font-size: 13px; }
button, .button { display: inline-block; border: 0; border-radius: 7px;
       padding: 9px 16px; font-size: 14px; cursor: pointer; text-decoration: none;
       background: #2f6fed; color: #fff; }
button:hover, .button:hover { background: #1f57c9; }
button.secondary, .button.secondary { background: #e4e8ed; color: #1f2933; }
button.secondary:hover, .button.secondary:hover { background: #d4dae1; }
button.danger { background: #d64545; }
button.danger:hover { background: #b93a3a; }
button:disabled { background: #b8c1cc; cursor: not-allowed; }
input[type=file] { font-size: 14px; margin-bottom: 12px; display: block; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border: 1px solid #e1e6eb; border-radius: 10px; overflow: hidden; }
th, td { padding: 10px 12px; text-align: left; font-size: 13px;
         border-bottom: 1px solid #eef1f4; vertical-align: top; }
th { background: #f8fafc; font-weight: 600; color: #52606d; }
tr:last-child td { border-bottom: 0; }
.tag { display: inline-block; padding: 2px 9px; border-radius: 20px;
       font-size: 12px; white-space: nowrap; }
.tag.new { background: #e6eefc; color: #2f6fed; }
.tag.processing { background: #fdf0d5; color: #9a6700; }
.tag.done { background: #def7e5; color: #1a7f45; }
.tag.error { background: #fbe0e0; color: #c02f2f; }
.flash { padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;
         font-size: 14px; }
.flash.ok { background: #def7e5; color: #1a7f45; }
.flash.err { background: #fbe0e0; color: #c02f2f; }
.progress { height: 8px; background: #e4e8ed; border-radius: 4px; overflow: hidden;
            margin-top: 10px; }
.progress i { display: block; height: 100%; background: #2f6fed; }
.muted { color: #6b7785; }
.empty { background: #fff; border: 1px dashed #cdd5de; border-radius: 10px;
         padding: 40px; text-align: center; color: #6b7785; }
pre { background: #f8fafc; border: 1px solid #e1e6eb; border-radius: 8px;
      padding: 14px; white-space: pre-wrap; word-wrap: break-word;
      font-size: 13px; line-height: 1.5; margin: 0 0 18px; }
a { color: #2f6fed; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
"""


def render_page(
    title: str,
    body: str,
    auto_refresh: bool = False,
    extra_style: str = "",
) -> bytes:
    refresh = '<meta http-equiv="refresh" content="3">' if auto_refresh else ""
    document = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>{html.escape(title)}</title>
<style>{PAGE_STYLE}{extra_style}</style>
</head>
<body><main>{body}</main></body>
</html>"""
    return document.encode("utf-8")


def render_index(
    rows: list[sqlite3.Row],
    counts: dict[str, int],
    job: dict[str, Any],
    flash: tuple[str, str] | None,
) -> bytes:
    parts: list[str] = [
        "<h1>Smart Feedback Processor</h1>",
        '<p class="subtitle">Загрузка отзывов из CSV, обработка через '
        "sfp_console.py и выгрузка результатов. &nbsp;"
        '<a href="/dashboard">Аналитический дашборд →</a></p>',
    ]

    if flash is not None:
        kind, text = flash
        parts.append(f'<div class="flash {kind}">{html.escape(text)}</div>')

    parts.append(_render_panels(counts, job, bool(rows)))
    parts.append("<h2>Отзывы</h2>")
    parts.append(_render_table(rows) if rows else _render_empty())

    return render_page("Smart Feedback Processor", "".join(parts), job["running"])


def _render_panels(counts: dict[str, int], job: dict[str, Any], has_rows: bool) -> str:
    total = sum(counts.values())
    pending = counts[STATUS_NEW] + counts[STATUS_ERROR] + counts[STATUS_PROCESSING]

    upload_panel = """
    <section class="panel">
      <h2>1. Загрузка отзывов</h2>
      <form method="post" action="/upload" enctype="multipart/form-data">
        <input type="file" name="file" accept=".csv,text/csv" required>
        <button type="submit">Загрузить CSV</button>
      </form>
      <p class="muted" style="margin-bottom:0">Разделитель «;» или «,».
      Обязательная колонка — feedback_text.</p>
    </section>
    """

    if job["running"]:
        percent = int(job["done"] / job["total"] * 100) if job["total"] else 0
        process_body = (
            f'<p class="muted">{html.escape(job["message"])}</p>'
            f'<div class="progress"><i style="width:{percent}%"></i></div>'
            '<p class="muted" style="margin-bottom:0">Страница обновляется '
            "автоматически.</p>"
        )
    else:
        disabled = "" if pending else " disabled"
        hint = (
            f"Ожидают обработки: {pending}"
            if pending
            else "Все загруженные отзывы обработаны"
        )
        last = (
            f'<p class="muted" style="margin-bottom:0">{html.escape(job["message"])}</p>'
            if job["message"]
            else ""
        )
        process_body = (
            '<form method="post" action="/process">'
            f"<button type=\"submit\"{disabled}>Обработать отзывы</button>"
            "</form>"
            f'<p class="muted" style="margin-bottom:4px">{html.escape(hint)}</p>{last}'
        )

    export_disabled = "" if has_rows else " secondary"
    export_panel = f"""
    <section class="panel">
      <h2>3. Выгрузка результатов</h2>
      <div class="actions">
        <a class="button{export_disabled}" href="/export.csv">Скачать CSV</a>
        <form method="post" action="/clear"
              onsubmit="return confirm('Удалить все отзывы из базы данных?')">
          <button type="submit" class="danger secondary">Очистить базу</button>
        </form>
      </div>
      <p class="muted" style="margin-bottom:0">Отзывы, результаты анализа
      и подготовленные ответы.</p>
    </section>
    """

    counters = f"""
    <section class="panel">
      <h2>Состояние базы данных</h2>
      <div class="counters">
        <div class="counter"><b>{total}</b><span>всего</span></div>
        <div class="counter"><b>{counts[STATUS_NEW]}</b><span>новых</span></div>
        <div class="counter"><b>{counts[STATUS_DONE]}</b><span>обработано</span></div>
        <div class="counter"><b>{counts[STATUS_ERROR]}</b><span>с ошибкой</span></div>
      </div>
    </section>
    """

    return (
        f'<div class="panels">{upload_panel}'
        f'<section class="panel"><h2>2. Обработка</h2>{process_body}</section>'
        f"{export_panel}</div>"
        f'<div class="panels">{counters}</div>'
    )


def _render_empty() -> str:
    return (
        '<div class="empty">База данных пуста. Загрузите CSV-файл с отзывами, '
        "чтобы начать.</div>"
    )


def _render_table(rows: list[sqlite3.Row]) -> str:
    cells: list[str] = [
        "<table><thead><tr>"
        "<th>#</th><th>Дата</th><th>Клиент</th><th>Отзыв</th>"
        "<th>Статус</th><th>Оценка</th><th>Ответ</th><th></th>"
        "</tr></thead><tbody>"
    ]

    for row in rows:
        status = str(row["status"])
        client = row["client_name"] or (
            f"ID {row['client_id']}" if row["client_id"] is not None else "—"
        )
        rating = "—" if row["total_rating"] is None else str(row["total_rating"])
        if status == STATUS_ERROR:
            response = f'<span class="muted">{html.escape(_shorten(row["error"], 90))}</span>'
        else:
            response = html.escape(_shorten(row["response_text"], 110)) or "—"

        cells.append(
            "<tr>"
            f"<td>{row['id']}</td>"
            f"<td>{html.escape(row['feedback_date'] or '—')}</td>"
            f"<td>{html.escape(str(client))}</td>"
            f"<td>{html.escape(_shorten(row['feedback_text'], 140))}</td>"
            f'<td><span class="tag {status}">'
            f"{html.escape(STATUS_LABELS.get(status, status))}</span></td>"
            f"<td>{rating}</td>"
            f"<td>{response}</td>"
            f"<td><a href=\"/feedback?id={row['id']}\">Открыть</a></td>"
            "</tr>"
        )

    cells.append("</tbody></table>")
    return "".join(cells)


def render_detail(row: sqlite3.Row) -> bytes:
    analysis = _load_analysis(row["analysis_json"])
    status = str(row["status"])

    parts = [
        '<p><a href="/">← К списку отзывов</a></p>',
        f"<h1>Отзыв №{row['id']}</h1>",
        f'<p class="subtitle"><span class="tag {status}">'
        f"{html.escape(STATUS_LABELS.get(status, status))}</span> "
        f"&nbsp;{html.escape(row['feedback_date'] or '')} "
        f"&nbsp;{html.escape(row['client_name'] or '')}</p>",
        "<h2>Текст отзыва</h2>",
        f"<pre>{html.escape(row['feedback_text'] or '')}</pre>",
    ]

    if row["error"]:
        parts.append("<h2>Ошибка обработки</h2>")
        parts.append(f'<pre class="flash err">{html.escape(row["error"])}</pre>')

    if row["response_text"]:
        parts.append("<h2>Подготовленный ответ</h2>")
        parts.append(f"<pre>{html.escape(row['response_text'])}</pre>")

    if analysis:
        parts.append("<h2>Результат анализа</h2>")
        summary = {
            name: analysis.get(name)
            for name in ANALYSIS_SCALARS
            if analysis.get(name) is not None
        }
        if summary:
            parts.append(
                "<pre>"
                + html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
                + "</pre>"
            )
        details = {
            name: analysis[name]
            for name in ANALYSIS_LISTS
            if analysis.get(name)
        }
        if details:
            parts.append(
                "<pre>"
                + html.escape(json.dumps(details, ensure_ascii=False, indent=2))
                + "</pre>"
            )

    return render_page(f"Отзыв №{row['id']}", "".join(parts))


def _shorten(value: str | None, limit: int) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# HTTP-обработчик
# ---------------------------------------------------------------------------


class FeedbackWebHandler(BaseHTTPRequestHandler):
    server_version = "SmartFeedbackProcessor/1.0"
    storage: WebFeedbacksStorage
    job: ProcessingJob
    references: analytics.ReferenceData
    max_upload_bytes = 32 * 1024 * 1024

    def do_GET(self) -> None:  # noqa: N802 — имя задано BaseHTTPRequestHandler
        route = urlparse(self.path)
        query = parse_qs(route.query)

        if route.path == "/":
            self._send_index(query)
        elif route.path == "/feedback":
            self._send_detail(query)
        elif route.path == "/dashboard":
            self._send_dashboard(query)
        elif route.path == "/export.csv":
            self._send_export()
        elif route.path == "/dashboard.csv":
            self._send_dashboard_csv(query)
        elif route.path == "/dashboard.pdf":
            self._send_dashboard_pdf(query)
        elif route.path == "/status.json":
            self._send_json(self.job.snapshot())
        elif route.path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
        else:
            self._send_not_found()

    def do_POST(self) -> None:  # noqa: N802 — имя задано BaseHTTPRequestHandler
        route = urlparse(self.path)

        if route.path == "/upload":
            self._handle_upload()
        elif route.path == "/process":
            self._redirect(f"/?msg={_quote(self.job.start())}&kind=ok")
        elif route.path == "/clear":
            removed = self.storage.clear()
            self._redirect(f"/?msg={_quote(f'Удалено отзывов: {removed}')}&kind=ok")
        else:
            self._send_not_found()

    # -- маршруты ----------------------------------------------------------

    def _send_index(self, query: dict[str, list[str]]) -> None:
        message = query.get("msg", [""])[0]
        kind = "err" if query.get("kind", ["ok"])[0] == "err" else "ok"
        flash = (kind, message) if message else None

        page = render_index(
            self.storage.list_all(),
            self.storage.counts_by_status(),
            self.job.snapshot(),
            flash,
        )
        self._send_bytes(HTTPStatus.OK, page, "text/html; charset=utf-8")

    def _send_detail(self, query: dict[str, list[str]]) -> None:
        feedback_id = _as_int_or_none(query.get("id", [""])[0])
        row = self.storage.get(feedback_id) if feedback_id is not None else None
        if row is None:
            self._send_not_found()
            return
        self._send_bytes(HTTPStatus.OK, render_detail(row), "text/html; charset=utf-8")

    def _dashboard_data(self, query: dict[str, list[str]]) -> analytics.DashboardData:
        preset = query.get("period", ["all"])[0]
        if preset not in {name for name, _ in analytics.PERIOD_PRESETS}:
            preset = "all"

        return analytics.build_dashboard(
            self.storage.list_all(),
            self.references,
            preset=preset,
            date_from=analytics.parse_feedback_date(query.get("from", [""])[0]),
            date_to=analytics.parse_feedback_date(query.get("to", [""])[0]),
        )

    def _send_dashboard(self, query: dict[str, list[str]]) -> None:
        data = self._dashboard_data(query)
        page = render_page(
            "Аналитический дашборд",
            dashboard.render_dashboard(data, query.get("msg", [""])[0]),
            extra_style=dashboard.DASHBOARD_STYLE,
        )
        self._send_bytes(HTTPStatus.OK, page, "text/html; charset=utf-8")

    def _send_dashboard_csv(self, query: dict[str, list[str]]) -> None:
        payload = dashboard_export.build_dashboard_csv(self._dashboard_data(query))
        self._send_download(payload, "text/csv; charset=utf-8", "dashboard", "csv")

    def _send_dashboard_pdf(self, query: dict[str, list[str]]) -> None:
        try:
            payload = dashboard_export.build_dashboard_pdf(self._dashboard_data(query))
        except pdfwriter.FontNotFoundError as error:
            logger.error("PDF не сформирован: %s", error)
            self._redirect(f"/dashboard?msg={_quote(str(error))}")
            return
        self._send_download(payload, "application/pdf", "dashboard", "pdf")

    def _send_download(
        self,
        payload: bytes,
        content_type: str,
        stem: str,
        extension: str,
    ) -> None:
        filename = f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extension}"
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            content_type,
            extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _send_export(self) -> None:
        payload = build_export_csv(self.storage.list_all())
        filename = f"feedbacks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            "text/csv; charset=utf-8",
            extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


    def _handle_upload(self) -> None:
        length = _as_int_or_none(self.headers.get("Content-Length")) or 0
        if length <= 0:
            self._redirect(f"/?msg={_quote('Файл не передан')}&kind=err")
            return
        if length > self.max_upload_bytes:
            self._redirect(f"/?msg={_quote('Файл слишком большой')}&kind=err")
            return

        body = self.rfile.read(length)
        try:
            fields = parse_multipart(body, self.headers.get("Content-Type", ""))
            filename, content = fields.get("file", (None, b""))
            if not filename or not content:
                raise ValueError("Файл не выбран")
            records = parse_feedbacks_csv(content)
        except ValueError as error:
            self._redirect(f"/?msg={_quote(str(error))}&kind=err")
            return

        added = self.storage.add_many(records, filename)
        self._redirect(
            f"/?msg={_quote(f'Загружено отзывов: {added} (файл {filename})')}&kind=ok"
        )

    # -- вспомогательные ---------------------------------------------------

    def _send_not_found(self) -> None:
        page = render_page(
            "Страница не найдена",
            '<h1>404</h1><p>Страница не найдена. <a href="/">На главную</a></p>',
        )
        self._send_bytes(HTTPStatus.NOT_FOUND, page, "text/html; charset=utf-8")

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_bytes(
            HTTPStatus.OK,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("%s - %s", self.address_string(), format % args)


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smart Feedback Processor — web-интерфейс обработки отзывов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python sfp_web.py\n"
            "  python sfp_web.py port=8080\n"
            "  python sfp_web.py host=0.0.0.0 port=8080 settings_dir=data"
        ),
    )
    parser.add_argument(
        "parameters",
        nargs="*",
        metavar="имя=значение",
        help="Именованные параметры: host, port, settings_dir, db, open_browser",
    )
    raw = parser.parse_args().parameters

    values: dict[str, str] = {
        "host": "127.0.0.1",
        "port": "8000",
        "settings_dir": "data",
        "db_path": str(config.FEEDBACKS_DB_PATH),
        "open_browser": "yes",
    }

    for parameter in raw:
        if "=" not in parameter:
            raise SystemExit(
                f'Некорректный параметр "{parameter}". '
                "Используйте формат имя=значение, например: port=8080"
            )
        raw_name, value = parameter.split("=", 1)
        canonical = PARAM_ALIASES.get(raw_name.strip())
        if canonical is None:
            allowed = ", ".join(sorted(set(PARAM_ALIASES)))
            raise SystemExit(
                f'Неизвестный параметр "{raw_name.strip()}". Допустимые имена: {allowed}'
            )
        values[canonical] = value.strip()

    try:
        port = int(values["port"])
    except ValueError:
        raise SystemExit(f'Некорректный port="{values["port"]}"') from None

    return argparse.Namespace(
        host=values["host"],
        port=port,
        settings_dir=values["settings_dir"],
        db_path=values["db_path"],
        open_browser=values["open_browser"].lower() in ("1", "yes", "true", "да"),
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = parse_args()
    settings_dir = Path(args.settings_dir)
    if not settings_dir.is_absolute():
        settings_dir = (PROJECT_ROOT / settings_dir).resolve()

    if not settings_dir.is_dir():
        logger.error("Каталог настроек не найден: %s", settings_dir)
        return 1
    if not CONSOLE_SCRIPT.exists():
        logger.error("Не найдено консольное приложение: %s", CONSOLE_SCRIPT)
        return 1

    storage = WebFeedbacksStorage(args.db_path)
    FeedbackWebHandler.storage = storage
    FeedbackWebHandler.job = ProcessingJob(storage, settings_dir)
    FeedbackWebHandler.references = analytics.load_reference_data(settings_dir)

    try:
        server = ThreadingHTTPServer((args.host, args.port), FeedbackWebHandler)
    except OSError as error:
        logger.error("Не удалось запустить сервер на %s:%s — %s",
                     args.host, args.port, error)
        return 1

    url = f"http://{args.host}:{args.port}/"
    logger.info("База данных: %s", storage.db_path)
    logger.info("Каталог настроек: %s", settings_dir)
    logger.info("Web-интерфейс доступен по адресу %s (Ctrl+C — остановить)", url)

    if args.open_browser:
        threading.Timer(0.7, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка сервера")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
