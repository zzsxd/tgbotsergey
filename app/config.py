import os
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv

DEFAULT_STORE_PATH = os.getenv("CONFIG_STORE_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "config.json"))

@dataclass
class Settings:
    """Настройки приложения, загружаемые из переменных окружения.

    REQUIRED_CHANNELS — список через запятую: @username каналов/чатов либо их ID.
    CHAT_ID — целевой чат (группа/супергруппа), где действует модерация.
    """

    bot_token: str
    required_channels: List[str]
    chat_id: Optional[int]
    cache_ttl_seconds: int = 10
    notify_ttl_seconds: int = 10
    config_store_path: str = DEFAULT_STORE_PATH


def _parse_required_channels(env_value: str) -> List[str]:
    if not env_value:
        return []
    parts = [p.strip() for p in env_value.split(",") if p.strip()]
    # Нормализация: числовые ID оставляем как есть, для username добавляем '@'
    normalized: List[str] = []
    for part in parts:
        if part.lstrip("-").isdigit():
            normalized.append(part)
        else:
            username = part
            if username.startswith("https://t.me/"):
                username = username.split("https://t.me/")[-1]
            if not username.startswith("@"):
                username = f"@{username}"
            normalized.append(username)
    return normalized


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан в окружении")

    channels_env = os.getenv("REQUIRED_CHANNELS", "").strip()
    if not channels_env:
        # Backward compatibility: allow single key name
        channels_env = os.getenv("REQUIRED_CHANNEL", "").strip()
    channels = _parse_required_channels(channels_env)

    chat_id_str = os.getenv("CHAT_ID", "").strip()
    chat_id_val: Optional[int] = None
    if chat_id_str and chat_id_str.lstrip("-").isdigit():
        chat_id_val = int(chat_id_str)

    cache_ttl = int(os.getenv("SUB_CHECK_CACHE_TTL", "10"))
    notify_ttl = int(os.getenv("NOTICE_REPEAT_TTL", "10"))

    return Settings(
        bot_token=bot_token,
        required_channels=channels,
        chat_id=chat_id_val,
        cache_ttl_seconds=cache_ttl,
        notify_ttl_seconds=notify_ttl,
        config_store_path=os.path.abspath(DEFAULT_STORE_PATH),
    )


