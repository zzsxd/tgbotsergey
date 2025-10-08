from __future__ import annotations

from typing import Iterable, Optional, List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatMember

from .cache import TTLMemoryCache
from .storage import ConfigStore
import logging


class SubscriptionService:
    """Сервис проверки подписки пользователя на все обязательные каналы.

    Использует TTL-кэш в памяти, чтобы сократить число запросов к API.
    """

    def __init__(self, bot: Bot, channels: Iterable[str], ttl_seconds: int, store: Optional[ConfigStore] = None) -> None:
        self.bot = bot
        self.channels = list(channels)
        self.cache = TTLMemoryCache()
        self.ttl_seconds = ttl_seconds
        self.store = store
        self.logger = logging.getLogger("subscription")

    def _cache_key(self, user_id: int) -> str:
        return f"subscribed:{user_id}"

    async def is_fully_subscribed(self, user_id: int) -> bool:
        key = self._cache_key(user_id)
        if await self.cache.contains(key):
            return True

        # Берём актуальные каналы из хранилища (если оно подключено)
        channels: List[str]
        if self.store is not None:
            channels = await self.store.list_channels()
            if not channels:
                channels = self.channels
        else:
            channels = self.channels

        for ch in channels:
            try:
                member: ChatMember = await self.bot.get_chat_member(chat_id=ch, user_id=user_id)
            except (TelegramBadRequest, TelegramForbiddenError):
                # Канал приватный или бот не админ — считаем, что подписки нет
                self.logger.debug("get_chat_member failed for %s user %s", ch, user_id)
                return False

            status = getattr(member, "status", None)
            is_member_attr = getattr(member, "is_member", None)
            if status in {"creator", "administrator", "member"}:
                pass
            elif status == "restricted" and bool(is_member_attr):
                pass
            else:
                return False

        # Успех кэшируем, чтобы реже ходить в API
        await self.cache.set_until(key, self.ttl_seconds)
        return True


