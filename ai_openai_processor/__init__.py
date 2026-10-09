"""Модуль обработки запросов через ИИ."""

from .openai_client import OpenAIClient
from .response_generator import ResponseGenerator

__all__ = ["OpenAIClient", "ResponseGenerator"]

