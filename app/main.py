import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import logging

from .config import load_settings
from .handlers import setup_handlers
from .subscription import SubscriptionService
from .storage import ConfigStore
from .admin import setup_admin


async def main() -> None:
    """Точка входа: создаём бота/диспетчер и запускаем поллинг."""
    logging.basicConfig(
        level=getattr(logging, (__import__('os').getenv('LOG_LEVEL') or 'INFO').upper(), logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    logger = logging.getLogger("app")
    settings = load_settings()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    store = ConfigStore(settings.config_store_path)

    # Нормализуем ранее сохранённые каналы: заменяем числовые ID на @username, если доступно
    async def _normalize_channels_usernames() -> None:
        channels = await store.list_channels()
        changed = False
        for val in channels:
            if val.lstrip("-").isdigit():
                try:
                    chat = await bot.get_chat(int(val))
                    if getattr(chat, "username", None):
                        await store.remove_channel(val)
                        await store.add_channel(f"@{chat.username}")
                        changed = True
                except Exception:
                    pass
        if changed:
            logger.info("Normalized channels to @usernames where available")

    await _normalize_channels_usernames()
    subs = SubscriptionService(bot=bot, channels=settings.required_channels, ttl_seconds=settings.cache_ttl_seconds, store=store)
    router = setup_handlers(settings, subs)
    dp.include_router(router)

    # Админ-меню: список ID берём из переменной окружения ADMIN_USER_IDS (через запятую)
    import os
    raw_admin = os.getenv("ADMIN_USER_IDS", "")
    admin_ids = {int(x) for x in raw_admin.split(",") if x.strip().lstrip("-").isdigit()}
    logger.info("Admin IDs: %s", sorted(admin_ids) if admin_ids else "<empty>")
    dp.include_router(setup_admin(store, admin_ids))

    logger.info("Starting polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())


