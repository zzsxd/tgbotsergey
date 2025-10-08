from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command

from .storage import ConfigStore
import logging


router = Router(name="admin-settings")
logger = logging.getLogger("admin")


def settings_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📡 Выбрать канал",
                    request_chat={"request_id": 43, "chat_is_channel": True, "bot_is_member": True},
                ),
                KeyboardButton(
                    text="🗑 Удалить канал",
                    request_chat={"request_id": 44, "chat_is_channel": True, "bot_is_member": True},
                ),
            ],
            [
                KeyboardButton(
                    text="👥 Выбрать чат",
                    request_chat={"request_id": 45, "chat_is_channel": False, "bot_is_member": False},
                ),
                KeyboardButton(
                    text="🗑 Удалить чат",
                    request_chat={"request_id": 46, "chat_is_channel": False, "bot_is_member": False},
                ),
            ],
            [
                KeyboardButton(text="📋 Список обязательных подписок"),
                KeyboardButton(
                    text="💬 Назначить чат",
                    request_chat={"request_id": 42, "chat_is_channel": False, "bot_is_member": False},
                ),
            ],
            [KeyboardButton(text="✖ Закрыть меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выберите действие",
    )


def setup_admin(store: ConfigStore, admin_user_ids: set[int]) -> Router:
    # Если список админов пуст, разрешаем действия любому пользователю (для первичной настройки)
    admins = set(admin_user_ids or [])

    def is_not_authorized(user_id: int | None) -> bool:
        if user_id is None:
            return True
        # Если админов не задано — разрешаем всем
        if not admins:
            return False
        return user_id not in admins
    @router.message(Command("start"))
    async def start_menu(message: Message) -> None:
        # Только админы видят меню; для остальных — тишина
        if is_not_authorized(message.from_user.id if message.from_user else None):
            return
        await message.answer("Настройки бота", reply_markup=settings_keyboard())
        logger.debug("menu opened by %s", message.from_user.id if message.from_user else None)

    @router.message(Command("settings"))
    async def show_menu(message: Message) -> None:
        # Только для админов
        if is_not_authorized(message.from_user.id if message.from_user else None):
            return
        await message.answer("Настройки бота", reply_markup=settings_keyboard())

    @router.message(F.text.func(lambda t: (t or "").strip().lower().endswith("закрыть меню")))
    async def close_menu(message: Message) -> None:
        if is_not_authorized(message.from_user.id if message.from_user else None):
            return
        await message.answer("Меню закрыто", reply_markup=ReplyKeyboardRemove())

    @router.message(F.text.func(lambda t: (t or "").strip().lower().endswith("список обязательных подписок") or (t or "").strip().lower().endswith("список каналов")))
    async def list_channels(message: Message, bot: Bot) -> None:
        if is_not_authorized(message.from_user.id if message.from_user else None):
            return
        channels = await store.list_channels()
        if not channels:
            await message.answer("Пока нет обязательных подписок")
            return
        lines = []
        for ch in channels:
            try:
                chat = await bot.get_chat(ch)
                title = getattr(chat, "title", None) or getattr(chat, "username", None) or "канал"
                username_tag = f" (@{chat.username})" if getattr(chat, "username", None) else ""
                lines.append(f"• {title}{username_tag}")
            except Exception:
                # Не раскрываем сырой ID
                if isinstance(ch, str) and ch.startswith("@"):
                    lines.append(f"• {ch}")
                else:
                    lines.append("• канал")
        await message.answer("Список обязательных подписок:\n" + "\n".join(lines))
        logger.debug("channels listed: %s", lines)

    # Ручной способ добавления/удаления убран; используем только системный выбор

    # Обработка выбора чата через request_chat
    @router.message(F.chat_shared)
    async def on_chat_shared(message: Message, bot: Bot) -> None:
        if is_not_authorized(message.from_user.id if message.from_user else None):
            return
        shared = message.chat_shared
        if shared is None:
            return
        if shared.request_id == 42:
            await store.set_chat_id(shared.chat_id)
            await message.answer(f"Целевой чат назначен: {shared.chat_id}")
            logger.info("target chat set to %s", shared.chat_id)
        elif shared.request_id == 43:
            # Пытаемся сохранить @username, если у канала он есть; иначе сохраняем числовой ID
            identifier = str(shared.chat_id)
            try:
                chat = await bot.get_chat(shared.chat_id)
                if getattr(chat, "username", None):
                    identifier = f"@{chat.username}"
            except Exception:
                pass

            added = await store.add_channel(identifier)
            if added:
                pretty = identifier if identifier.startswith("@") else (getattr(locals().get("chat", None), "title", None) or "канал")
                await message.answer(f"Канал добавлен: {pretty}")
            else:
                await message.answer("Такой канал уже есть")
            logger.info("channel added by pick: %s -> %s (added=%s)", shared.chat_id, identifier, added)
        elif shared.request_id == 45:
            # Добавление группы/супергруппы в список обязательных
            identifier = str(shared.chat_id)
            try:
                chat = await bot.get_chat(shared.chat_id)
                if getattr(chat, "username", None):
                    identifier = f"@{chat.username}"
            except Exception:
                pass
            added = await store.add_channel(identifier)
            if added:
                pretty = identifier if identifier.startswith("@") else (getattr(locals().get("chat", None), "title", None) or "чат")
                await message.answer(f"Чат добавлен: {pretty}")
            else:
                await message.answer("Такой чат уже есть")
            logger.info("group added by pick: %s -> %s (added=%s)", shared.chat_id, identifier, added)
        elif shared.request_id == 44:
            # Удаляем по chat_id; если канал был добавлен как @username, попробуем получить username и удалить его
            removed = await store.remove_channel(str(shared.chat_id))
            if not removed:
                try:
                    chat = await bot.get_chat(shared.chat_id)
                    if getattr(chat, "username", None):
                        removed = await store.remove_channel(f"@{chat.username}")
                except Exception:
                    pass
            if removed:
                await message.answer("Канал удалён")
            else:
                await message.answer("Такого канала нет в списке")
            logger.info("channel removed by pick: %s (removed=%s)", shared.chat_id, removed)
        elif shared.request_id == 46:
            # Удаление группы/супергруппы
            removed = await store.remove_channel(str(shared.chat_id))
            if not removed:
                try:
                    chat = await bot.get_chat(shared.chat_id)
                    if getattr(chat, "username", None):
                        removed = await store.remove_channel(f"@{chat.username}")
                except Exception:
                    pass
            if removed:
                await message.answer("Чат удалён")
            else:
                await message.answer("Такого чата нет в списке")
            logger.info("group removed by pick: %s (removed=%s)", shared.chat_id, removed)

    return router


