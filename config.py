"""
Единый файл конфигурации проекта.

Константы загружаются из settings.ini.
Секреты и переопределяемые значения — из .env.
"""

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_INI_PATH = PROJECT_ROOT / "settings.ini"

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


def _getenv(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _getenv_float(name: str, default: float) -> float:
    value = _getenv(name)
    return float(value) if value is not None else default


def _getenv_int(name: str, default: int) -> int:
    value = _getenv(name)
    return int(value) if value is not None else default


def _load_ini() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    if not SETTINGS_INI_PATH.exists():
        raise FileNotFoundError(
            f"Файл конфигурации не найден: {SETTINGS_INI_PATH}"
        )
    parser.read(SETTINGS_INI_PATH, encoding="utf-8")
    return parser


_INI = _load_ini()


def _ini_str(section: str, key: str, fallback: str = "") -> str:
    return _INI.get(section, key, fallback=fallback).strip()


def _ini_int(section: str, key: str, fallback: int = 0) -> int:
    return _INI.getint(section, key, fallback=fallback)


def _ini_float(section: str, key: str, fallback: float = 0.0) -> float:
    return _INI.getfloat(section, key, fallback=fallback)


def _ini_bool(section: str, key: str, fallback: bool = False) -> bool:
    return _INI.getboolean(section, key, fallback=fallback)


# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / _ini_str("paths", "data_dir", "data")
CLASSIFIERS_DB_PATH = DATA_DIR / _ini_str("paths", "classifiers_db", "classifiers.db")
FEEDBACKS_DB_PATH = DATA_DIR / _ini_str("paths", "feedbacks_db", "feedbacks.db")

# ---------------------------------------------------------------------------
# OpenAI / ProxyAPI (.env переопределяет значения по умолчанию из ini)
# ---------------------------------------------------------------------------

OPENAI_API_KEY = _getenv("OPENAI_API_KEY") or _getenv("PROXY_API_KEY")
OPENAI_BASE_URL = _getenv("OPENAI_BASE_URL", _ini_str("openai", "base_url"))
OPENAI_MODEL = _getenv("MODEL_NAME", _ini_str("openai", "model"))
OPENAI_TEMPERATURE = _getenv_float(
    "TEMPERATURE",
    _ini_float("openai", "temperature", 0.7),
)
OPENAI_MAX_TOKENS = _getenv_int(
    "OPENAI_MAX_TOKENS",
    _ini_int("openai", "max_tokens", 1000),
)

# ---------------------------------------------------------------------------
# GigaChat
# ---------------------------------------------------------------------------

GIGACHAT_OAUTH_URL = _ini_str("gigachat", "oauth_url")
GIGACHAT_API_BASE_URL = _ini_str("gigachat", "api_base_url")
GIGACHAT_DEFAULT_SCOPE = _ini_str("gigachat", "default_scope")
GIGACHAT_DEFAULT_MODEL = _ini_str("gigachat", "default_model")
GIGACHAT_DEFAULT_TEMPERATURE = _ini_float("gigachat", "default_temperature", 0.7)
GIGACHAT_DEFAULT_MAX_TOKENS = _ini_int("gigachat", "default_max_tokens", 1000)
GIGACHAT_DEFAULT_TIMEOUT = _ini_int("gigachat", "default_timeout", 30)
GIGACHAT_VERIFY_SSL = _ini_bool("gigachat", "verify_ssl", True)
GIGACHAT_AUTHORIZATION_KEY = _getenv("GIGACHAT_AUTHORIZATION_KEY")
GIGACHAT_PRESET_AUTH_KEY_PLACEHOLDER = _ini_str(
    "gigachat",
    "preset_auth_key_placeholder",
    "YOUR_AUTH_KEY_HERE",
)

# ---------------------------------------------------------------------------
# Промпты ResponseProcessor
# ---------------------------------------------------------------------------

BASE_PARSE_PROMPT = _ini_str("prompts", "base_parse_prompt")
BASE_RESPONSE_PROMPT = _ini_str("prompts", "base_response_prompt")
PARCE_PROMPT_QUALITY_ITEMS_HEADER = _ini_str(
    "prompts",
    "parce_prompt_quality_items_header",
)
PARCE_PROMPT_FEEDBACK_HEADER = _ini_str("prompts", "parce_prompt_feedback_header")

# ---------------------------------------------------------------------------
# Промпты RAG-генераторов
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = _ini_str("rag", "default_system_prompt")

# ---------------------------------------------------------------------------
# Прочие настройки (.env переопределяет значения по умолчанию из ini)
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = _getenv(
    "EMBEDDING_MODEL",
    _ini_str("openai", "embedding_model"),
)
CHROMA_PERSIST_DIR = _getenv(
    "CHROMA_PERSIST_DIR",
    str(PROJECT_ROOT / _ini_str("paths", "chroma_persist_dir", "chroma_db")),
)
CACHE_FILE = _getenv("CACHE_FILE", _ini_str("paths", "cache_file", "cache.json"))
TELEGRAM_BOT_TOKEN = _getenv("TELEGRAM_BOT_TOKEN")


@dataclass
class GigaChatConfig:
    """Конфигурация для GigaChat API."""

    authorization_key: str
    scope: str = GIGACHAT_DEFAULT_SCOPE
    model: str = GIGACHAT_DEFAULT_MODEL
    temperature: float = GIGACHAT_DEFAULT_TEMPERATURE
    max_tokens: int = GIGACHAT_DEFAULT_MAX_TOKENS
    oauth_url: str = GIGACHAT_OAUTH_URL
    api_base_url: str = GIGACHAT_API_BASE_URL
    verify_ssl: bool = GIGACHAT_VERIFY_SSL
    timeout: int = GIGACHAT_DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "GigaChatConfig":
        auth_key = _getenv("GIGACHAT_AUTHORIZATION_KEY")
        if not auth_key:
            raise ValueError("GIGACHAT_AUTHORIZATION_KEY не установлена")

        return cls(
            authorization_key=auth_key,
            model=_getenv("GIGACHAT_MODEL", GIGACHAT_DEFAULT_MODEL),
            temperature=_getenv_float(
                "GIGACHAT_TEMPERATURE",
                GIGACHAT_DEFAULT_TEMPERATURE,
            ),
            max_tokens=_getenv_int(
                "GIGACHAT_MAX_TOKENS",
                GIGACHAT_DEFAULT_MAX_TOKENS,
            ),
        )


def _gigachat_preset(section: str) -> GigaChatConfig:
    return GigaChatConfig(
        authorization_key=GIGACHAT_PRESET_AUTH_KEY_PLACEHOLDER,
        model=_ini_str(section, "model"),
        temperature=_ini_float(section, "temperature"),
        max_tokens=_ini_int(section, "max_tokens"),
    )


GIGACHAT_BASE_CONFIG = _gigachat_preset("gigachat.presets.base")
GIGACHAT_PRO_CONFIG = _gigachat_preset("gigachat.presets.pro")
GIGACHAT_PLUS_CONFIG = _gigachat_preset("gigachat.presets.plus")
GIGACHAT_CREATIVE_CONFIG = _gigachat_preset("gigachat.presets.creative")
GIGACHAT_PRECISE_CONFIG = _gigachat_preset("gigachat.presets.precise")
