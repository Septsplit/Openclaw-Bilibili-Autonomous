"""src/throttle.py — B 站 API 全局限流 + 指数退避重试.

模式来自原 bilibili_agent/services/interaction_service.py 的 _api_with_retry (L124-151)：
- 每次调用前 await throttle()，确保最小间隔
- -799 / "请求过于频繁" → 指数退避 + 触发全局冷却
- 全局冷却期内所有 API 调用直接 sleep
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

# 错误关键字
RATE_LIMIT_MARKERS = ("-799", "请求过于频繁", "rate limit", "too many requests")


@dataclass
class Throttle:
    """全局节流器，所有 API 调用共用一个实例。"""

    min_interval: float = 0.5          # 两次调用最小间隔（秒）
    cooldown_seconds: float = 30.0     # 全局冷却时长（秒）
    last_call_at: float = 0.0
    _cooldown_until: float = 0.0
    _logged_cooldown: bool = field(default=False, repr=False)

    async def wait(self) -> None:
        """调用 API 前的节流等待。"""
        now = time.monotonic()
        # 1. 冷却中 → sleep 到冷却结束
        if now < self._cooldown_until:
            if not self._logged_cooldown:
                self._logged_cooldown = True
            await asyncio.sleep(self._cooldown_until - now)
        # 2. 距上次调用太近 → 补到 min_interval
        elapsed = time.monotonic() - self.last_call_at
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed + random.uniform(0, 0.2))
        self.last_call_at = time.monotonic()

    def trigger_cooldown(self, seconds: float | None = None) -> None:
        """触发全局冷却。"""
        secs = seconds if seconds is not None else self.cooldown_seconds
        self._cooldown_until = time.monotonic() + secs
        self._logged_cooldown = False

    @property
    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until


# 全局单例
_GLOBAL = Throttle()


def get_throttle() -> Throttle:
    return _GLOBAL


async def call(api: Callable[[], Awaitable[T]], name: str = "api",
               max_retries: int = 5) -> T:
    """带指数退避的通用调用包装。

    用法：
        result = await throttle.call(
            lambda: video.Video(aid=aid, credential=cred).like(),
            name="video.like",
        )
    """
    throttle = get_throttle()
    logged = False
    for attempt in range(max_retries):
        try:
            await throttle.wait()
            return await api()
        except Exception as e:
            msg = str(e)
            if any(m in msg for m in RATE_LIMIT_MARKERS):
                throttle.trigger_cooldown()
                if attempt < max_retries - 1:
                    # 指数退避：2^(attempt+1) * [2, 3.5] 秒
                    base = 2 ** (attempt + 1)
                    wait_s = base * random.uniform(2.0, 3.5)
                    if not logged:
                        logged = True
                    await asyncio.sleep(wait_s)
                    continue
                # 末次仍限流 → 抛
                raise
            # 非限流错误：直接抛
            raise
