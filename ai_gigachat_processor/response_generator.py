"""
Генератор ответов.
Формирует финальные ответы на основе контекста и запроса с использованием GigaChat.
"""

import logging
from typing import Dict, List

import config

from .gigachat_client import GigaChatClient

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """Генератор ответов с использованием GigaChat."""

    DEFAULT_SYSTEM_PROMPT = config.DEFAULT_SYSTEM_PROMPT
    
    def __init__(
        self,
        gigachat_client: GigaChatClient,
        system_prompt: str = None
    ):
        """
        Инициализирует генератор ответов.
        
        Args:
            gigachat_client: Клиент GigaChat
            system_prompt: Системный промпт (опционально)
        """
        self.gigachat_client = gigachat_client
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        
        logger.info("ResponseGenerator (GigaChat) инициализирован")
    
    def generate(
        self,
        query: str,
        context_documents: List[Dict],
        conversation_history: List[Dict] = None
    ) -> str:
        """
        Генерирует ответ на запрос с учетом контекста.
        
        Args:
            query: Вопрос пользователя
            context_documents: Документы из базы знаний
            conversation_history: История диалога (опционально)
            
        Returns:
            Сгенерированный ответ
        """
        # Формируем контекст из документов
        if not context_documents:
            context = "Контекст отсутствует."
        else:
            context_parts = []
            for i, doc in enumerate(context_documents, 1):
                relevance = doc.get('relevance', 0)
                source = doc.get('source', 'unknown')
                text = doc.get('text', '')
                
                context_parts.append(
                    f"Документ {i} (Источник: {source}, Релевантность: {relevance:.2f}):\n{text}\n"
                )
            context = "\n---\n".join(context_parts)
        
        # Формируем промпт для пользователя
        user_prompt = f"""Контекст из базы знаний:

{context}

---

Вопрос пользователя: {query}

Ответь на вопрос, используя информацию из предоставленного контекста."""
        
        # Формируем список сообщений
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        # Добавляем историю диалога если есть
        if conversation_history:
            messages.extend(conversation_history)
        
        # Добавляем текущий запрос
        messages.append({"role": "user", "content": user_prompt})
        
        # Генерируем ответ
        try:
            answer = self.gigachat_client.generate_response(messages)
            return answer
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            return f"Извините, произошла ошибка при генерации ответа: {str(e)}"
    
    def format_response_with_sources(
        self,
        answer: str,
        sources: List[str]
    ) -> str:
        """
        Форматирует ответ с добавлением источников.
        
        Args:
            answer: Сгенерированный ответ
            sources: Список источников
            
        Returns:
            Отформатированный ответ
        """
        if not sources:
            return answer
        
        # Удаляем дубликаты
        unique_sources = list(set(sources))
        
        # Добавляем информацию об источниках
        sources_text = "\n\n📚 Источники:\n" + "\n".join([
            f"• {src}" for src in unique_sources
        ])
        
        return answer + sources_text

