from __future__ import annotations

from aiogram import F, Router, Bot
from aiogram.enums import ChatType
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, ChatPermissions
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
"""Обработчики сообщений и событий для обязательной подписки.

Удаляем сообщения нарушителей, отправляем напоминание с кнопками.
Автоматически ограничиваем/разрешаем отправку сообщений при выходе/возврате
в обязательные каналы и очищаем напоминание при повторной подписке.
"""

from .config import Settings
from .subscription import SubscriptionService
from .keyboards import subscription_keyboard
from .cache import TTLMemoryCache, TTLKVCache
from .storage import ConfigStore
import logging
import asyncio
import html


router = Router(name="mandatory-subscription")
_notice_cache = TTLMemoryCache()
_last_notice_message = TTLKVCache()
_welcomed_cache = TTLMemoryCache()
logger = logging.getLogger("handlers")


def setup_handlers(settings: Settings, subs: SubscriptionService) -> Router:
    store = ConfigStore(settings.config_store_path)
    
    def _is_target_chat(current_chat_id: int, target_chat_id: int | None) -> bool:
        """Сопоставляет текущий чат с целевым, учитывая варианты ID супергруппы (-id и -100id)."""
        if target_chat_id is None:
            return True
        if current_chat_id == target_chat_id:
            return True
        # Нормализуем к абсолютным строкам без знака
        try:
            abs_target = str(abs(int(target_chat_id)))
            abs_current = str(abs(int(current_chat_id)))
        except Exception:
            return False
        # В конфиге -id, фактически -100id
        if not abs_target.startswith("100") and abs_current == ("100" + abs_target):
            return True
        # В конфиге -100id, фактически -id
        if abs_target.startswith("100") and abs_target[3:] == abs_current:
            return True
        return False
    
    async def _delete_message_later(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 20) -> None:
        await asyncio.sleep(delay_seconds)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
    # Обрабатываем все сообщения и сверяемся с выбранным чатом динамически
    @router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def guard_message(message: Message) -> None:
        # Игнорируем собственные сообщения и сервисные
        if message.from_user is None or message.from_user.is_bot:
            return
        # Игнорируем сервисные события (вступление/выход и т.п.) — для них есть отдельные хендлеры
        if getattr(message, "new_chat_members", None) or getattr(message, "left_chat_member", None):
            return
        target_chat_id = await store.get_chat_id()
        # Чат ещё не выбран через меню — не вмешиваемся
        if target_chat_id is None:
            logger.debug("guard_message: target_chat_id not set; skip")
            return
        # Не целевой чат — пропускаем
        if not _is_target_chat(message.chat.id, target_chat_id):
            return
        user_id = message.from_user.id
        # Резервное приветствие на первый пользовательский месседж (если join-события скрыты)
        welcome_key = f"welcomed:{message.chat.id}:{user_id}"
        if not await _welcomed_cache.contains(welcome_key):
            user_name = html.escape(getattr(message.from_user, "full_name", None) or getattr(message.from_user, "first_name", None) or "участник")
            mention = f'<a href="tg://user?id={message.from_user.id}">{user_name}</a>'
            greet_text = mention + ": Привет 🦊\u202FДелай взаимку тут, и актив тебе обеспечен! Давай работать вместе! 🚀"
            try:
                sent_greet = await message.answer(greet_text)
                asyncio.create_task(_delete_message_later(message.bot, message.chat.id, sent_greet.message_id, 20))
                await _welcomed_cache.set_until(welcome_key, 604800)  # 7 дней
                logger.info("guard_message: fallback greeting sent to user %s in chat %s", user_id, message.chat.id)
            except Exception:
                pass
        if await subs.is_fully_subscribed(user_id):
            logger.debug("guard_message: user %s is subscribed", user_id)
            # Пользователь подписан — пробуем удалить прошлое напоминание, если оно было
            key = f"notice:{message.chat.id}:{user_id}"
            msg_id = await _last_notice_message.get(key)
            if msg_id:
                try:
                    await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except Exception:
                    pass
                await _last_notice_message.delete(key)
            return
        try:
            await message.delete()
        except Exception:
            # Если не хватает прав — всё равно отправим напоминание
            pass

        # Антиспам на напоминание для одного пользователя в рамках чата
        key = f"notice:{message.chat.id}:{user_id}"
        if await _notice_cache.contains(key):
            return

        channels_values = await store.list_channels() or settings.required_channels
        # Строим человекочитаемые упоминания и URL для кнопок
        readable: list[str] = []
        urls: list[str] = []
        for val in channels_values:
            if val.lstrip("-").isdigit():
                try:
                    chat = await message.bot.get_chat(int(val))
                    if getattr(chat, "username", None):
                        readable.append(f"<a href=\"https://t.me/{chat.username}\">@{chat.username}</a>")
                        urls.append(f"https://t.me/{chat.username}")
                    else:
                        # Для приватных каналов/чатов без username создаём инвайт‑ссылку (без t.me/c fallback)
                        title = getattr(chat, "title", None) or "канал"
                        invite_url = None
                        try:
                            invite = await message.bot.create_chat_invite_link(chat_id=chat.id)
                            invite_url = getattr(invite, "invite_link", None)
                        except Exception:
                            invite_url = None
                        if not invite_url:
                            try:
                                export_url = await message.bot.export_chat_invite_link(chat_id=chat.id)
                            except Exception:
                                export_url = None
                            invite_url = export_url
                        if invite_url:
                            readable.append(f"<a href=\"{invite_url}\">{html.escape(title)}</a>")
                            urls.append(invite_url)
                        else:
                            readable.append(html.escape(title))
                except Exception:
                    # Если не удалось получить информацию — создаём/экспортируем инвайт; без t.me/c
                    invite_url = None
                    try:
                        invite = await message.bot.create_chat_invite_link(chat_id=int(val))
                        invite_url = getattr(invite, "invite_link", None)
                    except Exception:
                        invite_url = None
                    if not invite_url:
                        try:
                            export_url = await message.bot.export_chat_invite_link(chat_id=int(val))
                        except Exception:
                            export_url = None
                        invite_url = export_url
                    if invite_url:
                        readable.append(f"<a href=\"{invite_url}\">канал</a>")
                        urls.append(invite_url)
                    else:
                        readable.append("канал")
            else:
                username = val[1:] if val.startswith("@") else val
                readable.append(f"<a href=\"https://t.me/{username}\">@{username}</a>")
                urls.append(f"https://t.me/{username}")

        # Упоминание пользователя, чтобы пришло уведомление
        user_name = html.escape(getattr(message.from_user, "full_name", None) or getattr(message.from_user, "first_name", None) or "пользователь")
        mention = f'<a href="tg://user?id={message.from_user.id}">{user_name}</a>'
        text = (
            f"{mention}, чтобы писать в чат, необходимо подписаться на канал(ы):\n"
            + " | ".join(readable)
        )
        # Отправляем напоминание
        reminder = await message.answer(
            text=text,
            reply_markup=subscription_keyboard(urls),
            disable_web_page_preview=True,
        )
        await _notice_cache.set_until(key, settings.notify_ttl_seconds)
        # Запоминаем id напоминания, чтобы удалить при повторной подписке (храним 1 час)
        await _last_notice_message.set(key, reminder.message_id, 3600)
        logger.info("notice sent to user %s in chat %s", user_id, message.chat.id)
        # Автоудаление напоминания через ~20 секунд
        asyncio.create_task(_delete_message_later(message.bot, message.chat.id, reminder.message_id, 20))

    # Мгновенно ограничиваем отправку сообщений при выходе из обязательного канала
    @router.chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
    async def on_leave_required_channel(event: ChatMemberUpdated, bot: Bot) -> None:
        chat = event.chat
        # Каналы сверяем динамически из хранилища
        channels = await store.list_channels() or settings.required_channels
        required_usernames = {c.lower() for c in channels if not c.lstrip("-").isdigit()}
        required_ids = {int(c) for c in channels if c.lstrip("-").isdigit()}
        username = ("@" + chat.username.lower()) if getattr(chat, "username", None) else None
        if (username and username in required_usernames) or (chat.id in required_ids):
            user_id = event.new_chat_member.user.id
            # Больше не ограничиваем отправку сообщений — будем удалять сообщения и напоминать

            # Отправляем напоминание в целевой чат с антиспамом и кнопками
            try:
                target_chat_id = await store.get_chat_id()
                if target_chat_id is None:
                    return
                key = f"notice:{target_chat_id}:{user_id}"
                if await _notice_cache.contains(key):
                    return

                channels_values = await store.list_channels() or settings.required_channels
                readable: list[str] = []
                urls: list[str] = []
                for val in channels_values:
                    if val.lstrip("-").isdigit():
                        try:
                            ch = await bot.get_chat(int(val))
                            if getattr(ch, "username", None):
                                readable.append(f"<a href=\"https://t.me/{ch.username}\">@{ch.username}</a>")
                                urls.append(f"https://t.me/{ch.username}")
                            else:
                                title = getattr(ch, "title", None) or "канал"
                                invite_url = None
                                try:
                                    invite = await bot.create_chat_invite_link(chat_id=ch.id)
                                    invite_url = getattr(invite, "invite_link", None)
                                except Exception:
                                    invite_url = None
                                if not invite_url:
                                    try:
                                        export_url = await bot.export_chat_invite_link(chat_id=ch.id)
                                    except Exception:
                                        export_url = None
                                    invite_url = export_url
                                if invite_url:
                                    readable.append(f"<a href=\"{invite_url}\">{html.escape(title)}</a>")
                                    urls.append(invite_url)
                                else:
                                    readable.append(html.escape(title))
                        except Exception:
                            invite_url = None
                            try:
                                invite = await bot.create_chat_invite_link(chat_id=int(val))
                                invite_url = getattr(invite, "invite_link", None)
                            except Exception:
                                invite_url = None
                            if not invite_url:
                                try:
                                    export_url = await bot.export_chat_invite_link(chat_id=int(val))
                                except Exception:
                                    export_url = None
                                invite_url = export_url
                            if invite_url:
                                readable.append(f"<a href=\"{invite_url}\">канал</a>")
                                urls.append(invite_url)
                            else:
                                readable.append("канал")
                    else:
                        un = val[1:] if val.startswith("@") else val
                        readable.append(f"<a href=\"https://t.me/{un}\">@{un}</a>")
                        urls.append(f"https://t.me/{un}")

                user = event.new_chat_member.user
                user_name = html.escape(getattr(user, "full_name", None) or getattr(user, "first_name", None) or "пользователь")
                mention = f'<a href="tg://user?id={user.id}">{user_name}</a>'
                text = (
                    f"{mention}, чтобы писать в чат, необходимо подписаться на канал(ы):\n"
                    + " | ".join(readable)
                )
                reminder = await bot.send_message(
                    chat_id=target_chat_id,
                    text=text,
                    reply_markup=subscription_keyboard(urls),
                    disable_web_page_preview=True,
                )
                await _notice_cache.set_until(key, settings.notify_ttl_seconds)
                await _last_notice_message.set(key, reminder.message_id, 3600)
                logger.info("notice sent (leave event) to user %s in chat %s", user_id, target_chat_id)
                # Автоудаление напоминания через ~20 секунд
                asyncio.create_task(_delete_message_later(bot, target_chat_id, reminder.message_id, 20))
            except Exception:
                # Не блокируем основной поток при ошибке отправки напоминания
                pass

    # Снимаем ограничение и удаляем напоминание при повторной подписке
    @router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
    async def on_join_required_channel(event: ChatMemberUpdated, bot: Bot) -> None:
        chat = event.chat
        channels = await store.list_channels() or settings.required_channels
        required_usernames = {c.lower() for c in channels if not c.lstrip("-").isdigit()}
        required_ids = {int(c) for c in channels if c.lstrip("-").isdigit()}
        username = ("@" + chat.username.lower()) if getattr(chat, "username", None) else None
        if (username and username in required_usernames) or (chat.id in required_ids):
            user_id = event.new_chat_member.user.id
            if await subs.is_fully_subscribed(user_id):
                # Снятие ограничений не требуется, так как мы их не накладываем
                # Try to delete last reminder in the chat to keep it clean
                target_chat_id = await store.get_chat_id()
                if target_chat_id is None:
                    return
                key = f"notice:{target_chat_id}:{user_id}"
                msg_id = await _last_notice_message.get(key)
                if msg_id:
                    try:
                        await bot.delete_message(chat_id=target_chat_id, message_id=msg_id)
                    except Exception:
                        pass
                    await _last_notice_message.delete(key)

    # Кнопки «Проверить подписку» нет — автоочистка работает по событию и при первом корректном сообщении

    # Приветствие новых участников целевого чата
    @router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}) & F.new_chat_members)
    async def welcome_new_members(message: Message) -> None:
        target_chat_id = await store.get_chat_id()
        # Если целевой чат назначен — приветствуем только там; иначе — во всех группах
        if target_chat_id is not None and not _is_target_chat(message.chat.id, target_chat_id):
            return
        logger.info("welcome_new_members: trigger in chat %s, target=%s", message.chat.id, target_chat_id)
        members = message.new_chat_members or []
        mentions: list[str] = []
        for m in members:
            # Не приветствуем ботов
            if getattr(m, "is_bot", False):
                continue
            user_name = html.escape(getattr(m, "full_name", None) or getattr(m, "first_name", None) or "участник")
            mentions.append(f'<a href="tg://user?id={m.id}">{user_name}</a>')
        if not mentions:
            return
        text = ", ".join(mentions) + ": Привет 🦊\u202FДелай взаимку тут, и актив тебе обеспечен! Давай работать вместе! 🚀"
        try:
            sent = await message.answer(text)
            # Автоудаление приветствия через ~20 секунд
            asyncio.create_task(_delete_message_later(message.bot, message.chat.id, sent.message_id, 20))
            logger.info("welcome_new_members: sent greeting to %s in chat %s", mentions, message.chat.id)
        except Exception:
            pass
        # Помечаем пользователей как уже поприветствованных
        for m in members:
            try:
                await _welcomed_cache.set_until(f"welcomed:{message.chat.id}:{m.id}", 604800)
            except Exception:
                pass

    # Резервное приветствие по событию изменения статуса участника (если сервисное сообщение не пришло)
    @router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
    async def welcome_on_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
        chat = event.chat
        if getattr(chat, "type", None) not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return
        target_chat_id = await store.get_chat_id()
        if target_chat_id is not None and not _is_target_chat(chat.id, target_chat_id):
            return
        logger.info("welcome_on_chat_member: trigger in chat %s, target=%s", chat.id, target_chat_id)
        user = event.new_chat_member.user
        if getattr(user, "is_bot", False):
            return
        user_name = html.escape(getattr(user, "full_name", None) or getattr(user, "first_name", None) or "участник")
        mention = f'<a href="tg://user?id={user.id}">{user_name}</a>'
        text = mention + ": Привет 🦊\u202FДелай взаимку тут, и актив тебе обеспечен! Давай работать вместе! 🚀"
        try:
            sent = await bot.send_message(chat_id=chat.id, text=text)
            asyncio.create_task(_delete_message_later(bot, chat.id, sent.message_id, 20))
            logger.info("welcome_on_chat_member: sent greeting to user %s in chat %s", user.id, chat.id)
        except Exception:
            pass
        # Помечаем как поприветствованного
        try:
            await _welcomed_cache.set_until(f"welcomed:{chat.id}:{user.id}", 604800)
        except Exception:
            pass

    # Удаляем также отредактированные сообщения от неподписанных пользователей
    @router.edited_message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def guard_edited_message(message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        target_chat_id = await store.get_chat_id()
        if target_chat_id is None:
            return
        if not _is_target_chat(message.chat.id, target_chat_id):
            return
        user_id = message.from_user.id
        if await subs.is_fully_subscribed(user_id):
            return
        try:
            await message.delete()
        except Exception:
            pass

    return router


