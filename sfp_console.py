#!/usr/bin/env python3
"""
Smart Feedback Processor — консольное приложение для обработки отзывов клиентов.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import config
from ai_openai_processor.openai_client import OpenAIClient
from handlers.response_processor import ResponseProcessor, normalize_feedback_record
#from stores.feedbacks_storage import FeedbacksStorage

logger = logging.getLogger(__name__)

SUPPORTED_OUTPUT_FORMATS = ("json", "csv", "txt")
FORMAT_EXTENSIONS = {"json": ".json", "csv": ".csv", "txt": ".txt"}
PROCESSED_DIR_NAME = "processed"

PARAM_ALIASES = {
    "input_dir": "input_dir",
    "input": "input_dir",
    "settings_dir": "settings_dir",
    "settings": "settings_dir",
    "output_format": "output_format",
    "format": "output_format",
    "output_dir": "output_dir",
    "output": "output_dir",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smart Feedback Processor — обработка отзывов клиентов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python sfp_console.py input_dir=input\n"
            "  python sfp_console.py format=json input_dir=input settings_dir=data\n"
            "  python sfp_console.py output_dir=output input_dir=input format=csv"
        ),
    )
    parser.add_argument(
        "parameters",
        nargs="*",
        metavar="имя=значение",
        help=(
            "Именованные параметры в формате имя=значение. "
            "Допустимые имена: input_dir, settings_dir, format, output_dir"
        ),
    )
    args = parser.parse_args()
    parsed = _parse_named_parameters(args.parameters)
    return argparse.Namespace(**parsed)


def _parse_named_parameters(parameters: list[str]) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "input_dir": None,
        "settings_dir": "data",
        "output_format": None,
        "output_dir": None,
    }

    for parameter in parameters:
        if "=" not in parameter:
            raise SystemExit(
                f'Некорректный параметр "{parameter}". '
                'Используйте формат имя=значение, например: input_dir=input'
            )

        raw_name, value = parameter.split("=", 1)
        name = raw_name.strip()
        value = value.strip()

        if not name:
            raise SystemExit(f'Пустое имя параметра в "{parameter}"')
        if not value:
            raise SystemExit(f'Пустое значение параметра "{name}"')

        canonical_name = PARAM_ALIASES.get(name)
        if canonical_name is None:
            allowed = ", ".join(sorted(set(PARAM_ALIASES)))
            raise SystemExit(
                f'Неизвестный параметр "{name}". Допустимые имена: {allowed}'
            )

        values[canonical_name] = value

    if not values["input_dir"]:
        raise SystemExit('Обязательный параметр input_dir не указан')

    if values["output_format"] is not None:
        output_format = values["output_format"].lower()
        if output_format not in SUPPORTED_OUTPUT_FORMATS:
            allowed = ", ".join(SUPPORTED_OUTPUT_FORMATS)
            raise SystemExit(
                f'Недопустимый format="{values["output_format"]}". '
                f"Допустимые значения: {allowed}"
            )
        values["output_format"] = output_format

    return values


def detect_input_format(file_path: Path) -> str:
    extension = file_path.suffix.lower().lstrip(".")
    if extension in SUPPORTED_OUTPUT_FORMATS:
        return extension
    raise ValueError(f"Неподдерживаемый формат файла: {file_path.name}")


def resolve_output_format(
    requested_format: str | None,
    input_file: Path,
) -> str:
    if requested_format:
        return requested_format
    return detect_input_format(input_file)


def load_feedbacks_from_json(file_path: Path) -> list[dict[str, Any]]:
    with file_path.open(encoding="utf-8-sig") as json_file:
        payload = json.load(json_file)

    if isinstance(payload, list):
        return [normalize_feedback_record(item) for item in payload]
    if isinstance(payload, dict):
        return [normalize_feedback_record(payload)]
    raise ValueError(f"Некорректный JSON в файле {file_path.name}")


def load_feedbacks_from_csv(file_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with file_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=";")
        for row in reader:
            records.append(normalize_feedback_record(row))
    return records


def load_feedbacks_from_txt(file_path: Path) -> list[dict[str, Any]]:
    text = file_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    record = normalize_feedback_record({"feedback_text": text})
    return [record]


def load_feedbacks(file_path: Path) -> list[dict[str, Any]]:
    file_format = detect_input_format(file_path)
    loaders = {
        "json": load_feedbacks_from_json,
        "csv": load_feedbacks_from_csv,
        "txt": load_feedbacks_from_txt,
    }
    records = loaders[file_format](file_path)
    return [record for record in records if record["feedback_text"].strip()]


def list_input_files(input_dir: Path) -> list[Path]:
    processed_dir = input_dir / PROCESSED_DIR_NAME
    files: list[Path] = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower().lstrip(".") not in SUPPORTED_OUTPUT_FORMATS:
            continue
        files.append(path)
    if processed_dir.exists():
        logger.debug("Обработанные файлы хранятся в %s", processed_dir)
    return files


def flatten_result_row(result: dict[str, Any]) -> dict[str, str]:
    row: dict[str, str] = {}
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            row[key] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            row[key] = ""
        else:
            row[key] = str(value)
    return row


def format_results(results: list[dict[str, Any]], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(results, ensure_ascii=False, indent=2)

    if output_format == "txt":
        responses = [result.get("response_text", "") for result in results]
        return "\n\n".join(response for response in responses if response)

    if not results:
        return ""

    rows = [flatten_result_row(result) for result in results]
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        delimiter=";",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_or_print_output(content: str, output_path: Path | None) -> None:
    if output_path is None:
        if content and not content.endswith("\n"):
            content += "\n"
        sys.stdout.write(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def move_to_processed(file_path: Path, input_dir: Path) -> None:
    processed_dir = input_dir / PROCESSED_DIR_NAME
    processed_dir.mkdir(parents=True, exist_ok=True)
    destination = processed_dir / file_path.name
    if destination.exists():
        destination.unlink()
    shutil.move(str(file_path), str(destination))


def build_output_path(
    input_file: Path,
    output_dir: Path,
    output_format: str,
) -> Path:
    extension = FORMAT_EXTENSIONS[output_format]
    return output_dir / f"{input_file.stem}{extension}"


def create_processor(settings_dir: Path) -> ResponseProcessor:
    settings_dir = settings_dir.resolve()
    if not settings_dir.is_dir():
        raise FileNotFoundError(f"Каталог настроек не найден: {settings_dir}")

    db_path = config.FEEDBACKS_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return ResponseProcessor(
        ai_model=OpenAIClient(),
        #feedback_storage=FeedbacksStorage(str(db_path)),
        data_dir=settings_dir,
    )


def process_file(
    processor: ResponseProcessor,
    file_path: Path,
    output_format: str,
    output_dir: Path | None,
) -> None:
    feedbacks = load_feedbacks(file_path)
    if not feedbacks:
        logger.warning("Файл %s не содержит отзывов", file_path.name)
        move_to_processed(file_path, file_path.parent)
        return

    results: list[dict[str, Any]] = []
    for index, feedback in enumerate(feedbacks, start=1):
        logger.info(
            "Обработка %s: отзыв %s/%s",
            file_path.name,
            index,
            len(feedbacks),
        )
        results.append(processor.execute(feedback))

    content = format_results(results, output_format)
    output_path = (
        build_output_path(file_path, output_dir, output_format)
        if output_dir is not None
        else None
    )
    write_or_print_output(content, output_path)
    move_to_processed(file_path, file_path.parent)

    if output_path is not None:
        logger.info("Результат сохранён в %s", output_path)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = parse_args()
    input_dir = Path(args.input_dir).resolve()
    settings_dir = Path(args.settings_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None

    if not input_dir.is_dir():
        logger.error("Каталог с отзывами не найден: %s", input_dir)
        return 1

    input_files = list_input_files(input_dir)
    if not input_files:
        logger.warning("В каталоге %s нет файлов для обработки", input_dir)
        return 0

    try:
        processor = create_processor(settings_dir)
    except Exception as error:
        logger.error("Ошибка инициализации обработчика: %s", error)
        return 1

    has_errors = False
    for file_path in input_files:
        output_format = resolve_output_format(args.output_format, file_path)
        try:
            process_file(processor, file_path, output_format, output_dir)
        except Exception as error:
            has_errors = True
            logger.error("Ошибка обработки файла %s: %s", file_path.name, error)

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
