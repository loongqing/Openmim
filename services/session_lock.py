"""通用键控会话锁。

并发更新开启后，同一会话（聊天 / business 私聊）必须串行处理，
避免上下文与回复乱序。不同 key 之间互不阻塞，key 空闲后自动回收。
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class SessionLockManager:
    """按 key 串行化的锁管理器。"""

    def __init__(self) -> None:
        self._locks: dict[Any, _LockEntry] = {}

    @asynccontextmanager
    async def acquire(self, key: Any) -> AsyncIterator[None]:
        entry = self._locks.get(key)
        if entry is None:
            entry = _LockEntry()
            self._locks[key] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0 and self._locks.get(key) is entry:
                self._locks.pop(key, None)
