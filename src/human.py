"""src/human.py — 真人行为模拟.

按 OpenClaw 需求：
- 每次 run 刷 3-5 个视频
- 每个视频做 1-2 个互动操作
- 视频间 5-15s 随机间隔
- 操作类型随机选（like/coin/comment/danmaku/favorite）
- 不要每次都全部做

概率参考 DEFAULT_PROBS（见下）：
- like: 0.5
- coin: 0.25（受 max_coins_daily 限制）
- comment: 0.15（受 max comments/天 + LLM 配额限制）
- danmaku: 0.03（受 max_daily_send 限制）
- favorite: 0.10
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bapi import BiliAPI, RecommendItem


class ActionType(str, Enum):
    LIKE = "like"
    COIN = "coin"
    COMMENT = "comment"
    DANMAKU = "danmaku"
    FAVORITE = "favorite"


# 基础概率（可被 state 配额自动降低）
# 概率在 0-1 之间，每个视频独立抽签：抽中即加入候选池，再从中随机选 1-2 个执行
# 喜欢 0.5：每 2 个视频约 1 个点赞
# 投币 0.25：每 4 个视频约 1 个投币（受 max_coins_daily=2 限制）
# 评论 0.15：每 7 个视频约 1 个评论（受 max comments/天 + LLM 配额限制）
# 弹幕 0.03：每 30 个视频约 1 个弹幕（受 max_daily_send=2 限制）
# 收藏 0.10：每 10 个视频约 1 个收藏（无日上限）
DEFAULT_PROBS: dict[ActionType, float] = {
    ActionType.LIKE: 0.50,
    ActionType.COIN: 0.25,
    ActionType.COMMENT: 0.15,
    ActionType.DANMAKU: 0.03,
    ActionType.FAVORITE: 0.10,
}


@dataclass
class RoundConfig:
    watch_min: int = 3
    watch_max: int = 5
    actions_per_video_min: int = 1
    actions_per_video_max: int = 2
    interval_min: float = 5.0
    interval_max: float = 15.0
    enable_comment: bool = True
    enable_danmaku: bool = True
    enable_favorite: bool = True


def pick_watch_count(cfg: RoundConfig) -> int:
    return random.randint(cfg.watch_min, cfg.watch_max)


def pick_actions_count(cfg: RoundConfig) -> int:
    return random.randint(cfg.actions_per_video_min, cfg.actions_per_video_max)


def pick_action_types(count: int, cfg: RoundConfig,
                      remaining: dict[ActionType, int],
                      prob_overrides: dict[ActionType, float] | None = None) -> list[ActionType]:
    """随机选 count 个不重复的操作类型。

    配额为 0 的操作不会进入候选。返回的可能少于 count（当配额全空时）。

    prob_overrides（v2.2 审核新增）：调用方传入从 config 读出的概率覆盖
    （如 interaction.prob_coin / prob_favorite 等）。不传则用 DEFAULT_PROBS。
    """
    pool: list[ActionType] = []
    probs = prob_overrides or DEFAULT_PROBS
    for at, p in probs.items():
        # 已耗尽配额的类型不进池
        if remaining.get(at, 0) <= 0:
            continue
        # 用户在 cfg 里关掉的类型不进池
        if at == ActionType.COMMENT and not cfg.enable_comment:
            continue
        if at == ActionType.DANMAKU and not cfg.enable_danmaku:
            continue
        if at == ActionType.FAVORITE and not cfg.enable_favorite:
            continue
        # 按概率决定是否入池
        if random.random() < p:
            pool.append(at)

    if not pool:
        return []
    if len(pool) <= count:
        random.shuffle(pool)
        return pool
    return random.sample(pool, count)


async def human_sleep(cfg: RoundConfig) -> None:
    """视频间的真人节奏延迟。"""
    secs = random.uniform(cfg.interval_min, cfg.interval_max)
    print(f"   [HUMAN] 视频间隔 {secs:.1f}s（模拟真人节奏）")
    await asyncio.sleep(secs)


async def micro_sleep(min_s: float = 0.5, max_s: float = 2.0) -> None:
    """同一视频多次操作之间的微延迟（比视频间短）。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


def remaining_quotas(state: dict, autonomous_cfg: dict) -> dict[ActionType, int]:
    """根据 state 里的 today 计数和 max_per_day，算出每种操作剩余配额。"""
    counts = state.get("counts", {}) or {}
    limits = state.get("max_per_day", {}) or {}
    # 一些操作没有日上限（like/favorite），给一个相对大的值
    return {
        ActionType.LIKE: 999,
        ActionType.FAVORITE: 999,
        ActionType.COIN: max(0, int(limits.get("coins", autonomous_cfg.get("max_coins_daily", 2))) - int(counts.get("coins", 0))),
        ActionType.DANMAKU: max(0, int(limits.get("danmaku", 2)) - int(counts.get("danmaku", 0))),
        ActionType.COMMENT: max(0, int(limits.get("comments", 5)) - int(counts.get("comments", 0))),
    }


async def execute_action(at: ActionType, item: "RecommendItem",
                         bapi: "BiliAPI", text: str | None = None,
                         safety=None) -> tuple[bool, str | None]:
    """v3 重构：text 由 OpenClaw 在调用前生成（评论/弹幕需要文本），

    返回 (ok, comment_text_if_any).
    skip-if-no-text 行为：COMMENT/DANMAKU 没文本就跳过。
    """
    if at == ActionType.LIKE:
        ok = await bapi.like_video(item)
        return ok, None
    if at == ActionType.COIN:
        ok = await bapi.coin_video(item, num=1)
        return ok, None
    if at == ActionType.FAVORITE:
        ok = await bapi.favorite_video(item)
        return ok, None
    if at == ActionType.COMMENT:
        if not text:
            return False, None
        if safety and safety.should_block(text):
            print(f"   [SAFETY] 评论命中敏感词，已拦截: {text[:30]}...")
            return False, None
        ok = await bapi.send_comment(item, text)
        return ok, text
    if at == ActionType.DANMAKU:
        if not text:
            return False, None
        text = text[:20]
        if safety and safety.should_block(text):
            return False, None
        ok = await bapi.send_danmaku(item, text)
        return ok, text
    return False, None
