from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict
from typing import List, Optional
from asyncio import Lock


@dataclass
class StoredConfig:
    chat_id: Optional[int]
    required_channels: List[str]


class ConfigStore:
    """Простое файловое хранилище настроек (JSON).

    Потокобезопасные операции чтения/записи через `asyncio.Lock`.
    Формат файла: {"chat_id": int | null, "required_channels": [str, ...]}.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = Lock()
        # Убедимся, что каталог существует
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    async def _load(self) -> StoredConfig:
        if not os.path.exists(self.path):
            return StoredConfig(chat_id=None, required_channels=[])
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return StoredConfig(
            chat_id=data.get("chat_id"),
            required_channels=list(data.get("required_channels", [])),
        )

    async def _save(self, cfg: StoredConfig) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="cfg_", suffix=".json", dir=os.path.dirname(self.path))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def get_chat_id(self) -> Optional[int]:
        async with self._lock:
            return (await self._load()).chat_id

    async def set_chat_id(self, chat_id: int) -> None:
        async with self._lock:
            cfg = await self._load()
            cfg.chat_id = int(chat_id)
            await self._save(cfg)

    async def list_channels(self) -> List[str]:
        async with self._lock:
            return list((await self._load()).required_channels)

    async def add_channel(self, channel: str) -> bool:
        """Добавить канал. Возвращает True, если добавлен (не было дубликата)."""
        norm = channel.strip()
        if not norm:
            return False
        if not norm.lstrip("-").isdigit() and not norm.startswith("@"):
            norm = f"@{norm}"
        async with self._lock:
            cfg = await self._load()
            if norm in cfg.required_channels:
                return False
            cfg.required_channels.append(norm)
            await self._save(cfg)
            return True

    async def remove_channel(self, value: str) -> bool:
        """Удалить канал по точному значению. Возвращает True, если был удалён."""
        async with self._lock:
            cfg = await self._load()
            if value in cfg.required_channels:
                cfg.required_channels.remove(value)
                await self._save(cfg)
                return True
            return False
import json
from asyncio import Lock
from pathlib import Path
from typing import List, Optional


def _normalize_identifier(value: str) -> str:
    """Нормализует идентификатор канала/чата: @username или числовой ID."""
    value = value.strip()
    if value.startswith("https://t.me/"):
        value = value.split("https://t.me/")[-1]
    if value.lstrip("-").isdigit():
        return value
    if not value.startswith("@"):
        return f"@{value}"
    return value


class StateService:
    """Хранение настроек (целевой чат и список обязательных каналов) в JSON.

    Потокобезопасно для одного процесса бота.
    """

    def __init__(self, path: str, initial_chat_id: Optional[int], initial_channels: List[str]) -> None:
        self._path = Path(path)
        self._lock = Lock()
        self._state = {
            "chat_id": initial_chat_id,
            "required_channels": [
                _normalize_identifier(v) for v in (initial_channels or [])
            ],
        }
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "chat_id" in data:
                        self._state["chat_id"] = data.get("chat_id")
                    if "required_channels" in data and isinstance(data["required_channels"], list):
                        self._state["required_channels"] = [
                            _normalize_identifier(v) for v in data["required_channels"]
                        ]
            except Exception:
                # Поврежденный файл — перезапишем текущим состоянием
                pass
        self._path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _save(self) -> None:
        self._path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    async def get_chat_id(self) -> Optional[int]:
        async with self._lock:
            return self._state.get("chat_id")

    async def set_chat_id(self, chat_id: int) -> None:
        async with self._lock:
            self._state["chat_id"] = int(chat_id)
            await self._save()

    async def get_required_channels(self) -> List[str]:
        async with self._lock:
            return list(self._state.get("required_channels", []))

    async def add_required_channel(self, identifier: str) -> None:
        ident = _normalize_identifier(identifier)
        async with self._lock:
            channels: List[str] = self._state.setdefault("required_channels", [])
            if ident not in channels:
                channels.append(ident)
                await self._save()

    async def remove_required_channel(self, identifier: str) -> bool:
        ident = _normalize_identifier(identifier)
        async with self._lock:
            channels: List[str] = self._state.setdefault("required_channels", [])
            before = len(channels)
            self._state["required_channels"] = [c for c in channels if c != ident]
            changed = len(self._state["required_channels"]) != before
            if changed:
                await self._save()
            return changed


