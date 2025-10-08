import time
from typing import Dict, Optional, Any, Tuple
from asyncio import Lock


class TTLMemoryCache:
    """Маленький TTL-кэш в памяти процесса.

    Достаточно для одного процесса бота. Для кластера замените на Redis
    с тем же интерфейсом.
    """

    def __init__(self) -> None:
        self._data: Dict[str, float] = {}
        self._lock = Lock()

    async def set_until(self, key: str, ttl_seconds: int) -> None:
        async with self._lock:
            self._data[key] = time.monotonic() + float(ttl_seconds)

    async def contains(self, key: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            exp = self._data.get(key)
            if exp is None:
                return False
            if exp < now:
                # Просроченные записи удаляем лениво
                self._data.pop(key, None)
                return False
            return True

    async def get_remaining(self, key: str) -> Optional[float]:
        async with self._lock:
            exp = self._data.get(key)
            if exp is None:
                return None
            remaining = exp - time.monotonic()
            if remaining <= 0:
                self._data.pop(key, None)
                return None
            return remaining


class TTLKVCache:
    """Простой TTL-кэш ключ→значение в памяти процесса.

    Хранит произвольные значения до истечения срока. Предназначен
    для эфемерного использования во время работы процесса.
    """

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = Lock()

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        async with self._lock:
            self._data[key] = (time.monotonic() + float(ttl_seconds), value)

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            exp, value = item
            if exp < time.monotonic():
                self._data.pop(key, None)
                return None
            return value

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)


