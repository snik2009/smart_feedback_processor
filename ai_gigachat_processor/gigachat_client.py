"""
Клиент для работы с GigaChat API.
Инкапсулирует логику общения с GigaChat.
"""

import logging
import time
import uuid
from typing import Dict, List, Optional, Union

import config as app_config
import requests
import urllib3

from .config import GigaChatConfig

logger = logging.getLogger(__name__)

# Отключаем предупреждения о небезопасных запросах (для самоподписанных сертификатов)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class GigaChatClient:
    """Клиент для GigaChat API."""

    OAUTH_URL = app_config.GIGACHAT_OAUTH_URL
    API_BASE_URL = app_config.GIGACHAT_API_BASE_URL
    
    def __init__(
        self,
        authorization_key: str = None,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        scope: str = None,
        gigachat_config: GigaChatConfig = None,
    ):
        """
        Инициализирует GigaChat клиент.
        
        Args:
            authorization_key: Authorization key в формате Basic <key>
            model: Модель для использования (GigaChat, GigaChat-Pro, GigaChat-Plus)
            temperature: Температура генерации (0.0 - 2.0)
            max_tokens: Максимальное количество токенов
            scope: Область доступа API
            gigachat_config: Объект конфигурации (опционально, переопределяет другие параметры)
        """
        if gigachat_config:
            self.authorization_key = gigachat_config.authorization_key
            self.model = gigachat_config.model
            self.temperature = gigachat_config.temperature
            self.max_tokens = gigachat_config.max_tokens
            self.scope = gigachat_config.scope
            self.oauth_url = gigachat_config.oauth_url
            self.api_base_url = gigachat_config.api_base_url
            self.verify_ssl = gigachat_config.verify_ssl
            self.timeout = gigachat_config.timeout
        else:
            authorization_key = authorization_key or app_config.GIGACHAT_AUTHORIZATION_KEY
            if not authorization_key:
                raise ValueError(
                    "authorization_key, gigachat_config или GIGACHAT_AUTHORIZATION_KEY "
                    "должны быть указаны"
                )
            self.authorization_key = authorization_key
            self.model = model or app_config.GIGACHAT_DEFAULT_MODEL
            self.temperature = (
                temperature
                if temperature is not None
                else app_config.GIGACHAT_DEFAULT_TEMPERATURE
            )
            self.max_tokens = max_tokens or app_config.GIGACHAT_DEFAULT_MAX_TOKENS
            self.scope = scope or app_config.GIGACHAT_DEFAULT_SCOPE
            self.oauth_url = self.OAUTH_URL
            self.api_base_url = self.API_BASE_URL
            self.verify_ssl = app_config.GIGACHAT_VERIFY_SSL
            self.timeout = app_config.GIGACHAT_DEFAULT_TIMEOUT
        
        # Access token будет получен при первом запросе
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        
        logger.info(f"GigaChat клиент инициализирован: модель={self.model}")
    
    def _get_access_token(self) -> str:
        """
        Получает access token для авторизации запросов.
        Кеширует токен и обновляет его при необходимости.
        
        Returns:
            Access token
        """
        # Проверяем, есть ли валидный токен
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        
        # Получаем новый токен
        try:
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'RqUID': str(uuid.uuid4()),
                'Authorization': f'Basic {self.authorization_key}'
            }
            
            payload = {
                'scope': self.scope
            }
            
            response = requests.post(
                self.oauth_url,
                headers=headers,
                data=payload,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            token_data = response.json()
            self._access_token = token_data['access_token']
            
            # Устанавливаем время истечения токена (обычно 30 минут)
            # Вычитаем 5 минут для запаса
            expires_in = token_data.get('expires_at', 1800) - 300
            self._token_expires_at = time.time() + expires_in
            
            logger.info("Access token успешно получен")
            return self._access_token
            
        except Exception as e:
            logger.error(f"Ошибка получения access token: {e}")
            raise
    
    def generate_response(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None
    ) -> str:
        """
        Генерирует ответ с помощью GigaChat.
        
        Args:
            messages: Список сообщений для GigaChat (формат: [{"role": "user/assistant/system", "content": "text"}])
            temperature: Температура (опционально)
            max_tokens: Макс токены (опционально)
            
        Returns:
            Сгенерированный ответ
        """
        try:
            access_token = self._get_access_token()
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature if temperature is not None else self.temperature,
                "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            }
            
            response = requests.post(
                f"{self.api_base_url}/chat/completions",
                headers=headers,
                json=payload,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            result = response.json()
            answer = result['choices'][0]['message']['content']
            
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
            messages: Список сообщений для GigaChat
            temperature: Температура (опционально)
            max_tokens: Макс токены (опционально)
            
        Yields:
            Части ответа
        """
        try:
            access_token = self._get_access_token()
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature if temperature is not None else self.temperature,
                "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
                "stream": True
            }
            
            response = requests.post(
                f"{self.api_base_url}/chat/completions",
                headers=headers,
                json=payload,
                verify=self.verify_ssl,
                timeout=self.timeout,
                stream=True
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if line:
                    line_text = line.decode('utf-8')
                    if line_text.startswith('data: '):
                        data_text = line_text[6:]  # Убираем 'data: '
                        if data_text == '[DONE]':
                            break
                        
                        try:
                            import json
                            chunk_data = json.loads(data_text)
                            if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                                delta = chunk_data['choices'][0].get('delta', {})
                                content = delta.get('content')
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            continue
        
        except Exception as e:
            logger.error(f"Ошибка streaming генерации: {e}")
            raise
    
    def get_models(self) -> List[str]:
        """
        Получает список доступных моделей.
        
        Returns:
            Список названий моделей
        """
        try:
            access_token = self._get_access_token()
            
            headers = {
                'Accept': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }
            
            response = requests.get(
                f"{self.api_base_url}/models",
                headers=headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            models_data = response.json()
            models = [model['id'] for model in models_data.get('data', [])]
            
            logger.info(f"Получены модели: {models}")
            return models
            
        except Exception as e:
            logger.error(f"Ошибка получения списка моделей: {e}")
            raise

