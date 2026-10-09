import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, Union

import config
from ai_gigachat_processor.gigachat_client import GigaChatClient
from ai_openai_processor.openai_client import OpenAIClient
#from stores.feedbacks_storage import FeedbacksStorage

logger = logging.getLogger(__name__)

AiModel = Union[OpenAIClient, GigaChatClient]

FEEDBACK_INPUT_FIELDS = (
    "feedback_date",
    "client_id",
    "client_name",
    "feedback_text",
    "responsible_id",
    "actions_taken",
)


class FeedbackInput(TypedDict):
    feedback_date: str
    client_id: int
    client_name: str
    feedback_text: str
    responsible_id: int
    actions_taken: str


def empty_feedback_record() -> FeedbackInput:
    return {
        "feedback_date": "",
        "client_id": 0,
        "client_name": "",
        "feedback_text": "",
        "responsible_id": 0,
        "actions_taken": "",
    }


def _parse_int_field(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return int(str(value).strip())


def normalize_feedback_record(raw: dict[str, Any]) -> FeedbackInput:
    record = empty_feedback_record()
    if "feedback_date" in raw and raw["feedback_date"] is not None:
        record["feedback_date"] = str(raw["feedback_date"])
    if "client_name" in raw and raw["client_name"] is not None:
        record["client_name"] = str(raw["client_name"])
    if "feedback_text" in raw and raw["feedback_text"] is not None:
        record["feedback_text"] = str(raw["feedback_text"])
    if "actions_taken" in raw and raw["actions_taken"] is not None:
        record["actions_taken"] = str(raw["actions_taken"])
    if "client_id" in raw:
        record["client_id"] = _parse_int_field(raw["client_id"])
    if "responsible_id" in raw:
        record["responsible_id"] = _parse_int_field(raw["responsible_id"])
    return record


class _PromptSection(NamedTuple):
    title: str
    csv_path: Path
    full_rows: bool = True
    name_fields: tuple[str, ...] = ("name",)


def _build_prompt_sections(data_dir: Path) -> tuple[tuple[_PromptSection, ...], tuple[_PromptSection, ...]]:
    parse_sections = (
        _PromptSection(
            "Справочник параметров качества (quality_items)",
            data_dir / "items_quality.csv",
            full_rows=False,
        ),
        _PromptSection(
            "Справочник типовых услуг (services)",
            data_dir / "items_services.csv",
            full_rows=False,
        ),
        _PromptSection(
            "Шкала настроения (mood)",
            data_dir / "items_mood.csv",
            full_rows=False,
            name_fields=("mood",),
        ),
    )
    response_sections = (
        _PromptSection(
            "Шаблоны вступления (templates_intro)",
            data_dir / "templates_intro.csv",
        ),
        _PromptSection(
            "Шаблоны заключения (templates_final)",
            data_dir / "templates_final.csv",
        ),
        _PromptSection(
            "Шаблоны ответов на позитивные оценки (templates_reply_positive_feedback)",
            data_dir / "templates_reply_positive_feedback.csv",
        ),
        _PromptSection(
            "Шаблоны ответов на негативные оценки (templates_reply_negative_feedback)",
            data_dir / "templates_reply_negative_feedback.csv",
        ),
        _PromptSection(
            "Справочник подразделений (items_responsibles)",
            data_dir / "items_responsibles.csv",
        ),
        _PromptSection(
            "Справочник зависимости подразделений от критериев качества (items_quality)",
            data_dir / "items_quality.csv",
        ),
    )
    return parse_sections, response_sections


class ResponseProcessor:
    """Преобразует отзыв клиента в JSON-структуру и генерирует ответ."""

    def __init__(
        self,
        ai_model: AiModel,
        #feedback_storage: FeedbacksStorage,
        data_dir: str | Path | None = None,
    ) -> None:
        self.ai_model = ai_model
        #self.feedback_storage = feedback_storage
        self.parsed_feedback: str = ""
        self.data_dir = Path(data_dir or config.DATA_DIR)

        self.base_parse_prompt = config.BASE_PARSE_PROMPT
        self.base_response_prompt = config.BASE_RESPONSE_PROMPT

        parse_sections, response_sections = _build_prompt_sections(self.data_dir)
        self.parse_prompt = self._build_prompt(self.base_parse_prompt, parse_sections)
        self.response_prompt = self._build_prompt(
            self.base_response_prompt,
            response_sections,
        )

        logger.info("ResponseProcessor инициализирован")

    def execute(self, feedback: str | dict[str, Any]) -> dict[str, Any]:
        """
        Преобразует отзыв в JSON, генерирует ответ и сохраняет результат в БД.

        Args:
            feedback: JSON-объект отзыва или его JSON-представление в виде строки.

        Returns:
            Итоговая JSON-структура, записанная в базу данных.
        """
        feedback_record = self._normalize_feedback_input(feedback)
        feedback_text = feedback_record["feedback_text"]
        if not feedback_text.strip():
            raise ValueError("feedback_text не может быть пустым")

        feedback_payload = json.dumps(feedback_record, ensure_ascii=False)
        parse_prompt = f"{self.parse_prompt}\n##Отзыв клиента:\n{feedback_payload}"
        self.parsed_feedback = self._call_ai_model(parse_prompt)

        response_prompt = (
            f"{self.response_prompt}\n##Структурированный анализ отзыва:\n"
            f"{self.parsed_feedback}"
        )
        response = self._call_ai_model(response_prompt)

        parsed_data = self._parse_json_response(self.parsed_feedback)
        parsed_data.update(feedback_record)
        parsed_data["response_text"] = response
        #parsed_data["feedback"] = feedback_text
        #parsed_data["response"] = response

        #self.feedback_storage.upsert(parsed_data)
        return parsed_data

    @staticmethod
    def _normalize_feedback_input(feedback: str | dict[str, Any]) -> FeedbackInput:
        if isinstance(feedback, str):
            text = feedback.strip()
            if not text:
                raise ValueError("feedback не может быть пустым")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                record = empty_feedback_record()
                record["feedback_text"] = text
                return record
            if not isinstance(parsed, dict):
                raise ValueError("JSON-отзыв должен быть объектом")
            source = parsed
        else:
            source = feedback

        return normalize_feedback_record(source)

    def _build_prompt(
        self,
        base_prompt: str,
        sections: tuple[_PromptSection, ...],
    ) -> str:
        prompt = base_prompt
        for section in sections:
            if section.full_rows:
                section_json = self._load_csv_rows_json(section.csv_path)
            else:
                section_json = self._load_csv_json(
                    section.csv_path,
                    section.name_fields,
                )
            if section_json:
                prompt += f"\n##{section.title}:\n{section_json}"
        return prompt

    @staticmethod
    def _load_csv_rows_json(csv_path: Path) -> str:
        if not csv_path.exists():
            logger.warning("CSV-файл не найден: %s", csv_path)
            return ""

        rows: list[dict[str, str]] = []
        with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file, delimiter=";")
            for row in reader:
                cleaned = {
                    key: value.strip()
                    for key, value in row.items()
                    if key and value is not None and value.strip()
                }
                if cleaned:
                    rows.append(cleaned)

        if not rows:
            return ""

        return json.dumps(rows, ensure_ascii=False)

    @staticmethod
    def _load_csv_json(
        csv_path: Path,
        name_fields: tuple[str, ...] = ("name",),
    ) -> str:
        if not csv_path.exists():
            logger.warning("CSV-файл не найден: %s", csv_path)
            return ""

        rows: list[dict[str, int | str]] = []
        with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file, delimiter=";")
            for row in reader:
                if not row.get("id"):
                    continue

                name = next(
                    (row[field].strip() for field in name_fields if row.get(field)),
                    "",
                )
                rows.append({"id": int(row["id"]), "name": name})

        if not rows:
            return ""

        return json.dumps(rows, ensure_ascii=False)

    def _call_ai_model(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self.ai_model.generate_response(messages)

    @staticmethod
    def _parse_json_response(raw_response: str) -> dict[str, Any]:
        text = raw_response.strip()

        fenced_match = re.search(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced_match:
            text = fenced_match.group(1).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Модель вернула некорректный JSON при разборе отзыва"
            ) from error

        if not isinstance(parsed, dict):
            raise ValueError("Результат разбора отзыва должен быть JSON-объектом")

        return parsed
