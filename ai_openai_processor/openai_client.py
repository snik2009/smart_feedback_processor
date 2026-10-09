"""
Клиент для работы с OpenAI API через ProxyAPI.
Инкапсулирует логику общения с OpenAI-совместимым API.
"""

import logging
from typing import Dict, List

import config
from openai import OpenAI

logger = logging.getLogger(__name__)


class OpenAIClient:
    """Клиент для OpenAI API через ProxyAPI."""
    
    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None
    ):
        """
        Инициализирует OpenAI клиент.
        
        Args:
            api_key: API ключ ProxyAPI
            base_url: Базовый URL ProxyAPI
            model: Модель для использования
            temperature: Температура генерации
            max_tokens: Максимальное количество токенов
        """
        api_key = api_key or config.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENAI_API_KEY или PROXY_API_KEY не установлены")

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or config.OPENAI_BASE_URL,
        )
        self.model = model or config.OPENAI_MODEL
        self.temperature = (
            temperature if temperature is not None else config.OPENAI_TEMPERATURE
        )
        self.max_tokens = max_tokens or config.OPENAI_MAX_TOKENS
        
        logger.info(
            f"OpenAI клиент инициализирован через ProxyAPI: "
            f"base_url={self.client.base_url}, модель={self.model}"
        )
    
    def generate_response(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None
    ) -> str:
        """
        Генерирует ответ с помощью GPT.
        
        Args:
            messages: Список сообщений для GPT
            temperature: Температура (опционально)
            max_tokens: Макс токены (опционально)
            
        Returns:
            Сгенерированный ответ
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens
            )
            
            answer = response.choices[0].message.content
            logger.info(f"Ответ сгенерирован: {len(answer)} символов")
            
            return answer
        
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            raise
    
    def generate_streaming_response(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None
    ):
        """
        Генерирует ответ с потоковой передачей.
        
        Args:
            messages: Список сообщений для GPT
            temperature: Температура (опционально)
            max_tokens: Макс токены (опционально)
            
        Yields:
            Части ответа
        """
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
                stream=True
            )
            
            for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        
        except Exception as e:
            logger.error(f"Ошибка streaming генерации: {e}")
            raise
