"""src/mood.py — v5.1 心情系统 (参考 bilibili_learning_bot/xingye_bot/state.py).

Data/mood_state.json
{
  "current": "平静",          # 13 种之一
  "energy": 70,              # 0-100 (跟 v4 精力的 0-20 不同, 这是 mood 自己的 0-100)
  "last_event": "",
  "last_changed": "<iso>",
  "history": [{"ts": "<iso>", "mood": "平静", "event": "..."}],
  "auto_change": true,        # OpenClaw 可关自动变化, 改手动
  "change_interval_minutes": 60
}

心情影响评论/弹幕/私信:
- low (0-30)  → 短, 疲惫, 1-15 字
- mid (31-70) → 中等, 自然, 15-40 字
- high (71-100) → 长, 活跃, 40-100 字

每次让 OpenClaw 写消息时传 mood 进去 (mood_str + energy_int).
"""
from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import actions_log, config as cfg


MOODS: tuple[str, ...] = (
    "兴奋", "愉快", "平静", "好奇", "慵懒",
    "深沉", "调皮", "温柔", "毒舌", "学究",
    "中二", "佛系", "热血",
)


# v5.7 Hermes 安全修复
_MAX_EVENT_LEN = 200


def _sanitize_event(event: Any, default: str = "") -> str:
    """净化用户传入的 event 字符串.

    - 转成 str, 限制长度
    - 移除控制字符 (除 \\n \\t)
    - 移除可能的 "---" 行防 JSON 字段污染
    - 空字符串回落到 default
    """
    if event is None:
        return default
    s = str(event)
    s = s[:_MAX_EVENT_LEN]
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    s = re.sub(r"(?m)^\s*---\s*$", "(分隔符)", s)
    return s.strip() or default


def _mood_path() -> Path:
    return cfg.DATA_DIR / "mood_state.json"


def _load() -> dict:
    if not _mood_path().exists():
        return _fresh()
    try:
        with open(_mood_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return _fresh()


def _save(st: dict) -> None:
    cfg.ensure_dirs()
    tmp = _mood_path().with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
            f.flush()
            import os
            os.fsync(f.fileno())
        os.replace(tmp, _mood_path())
    except OSError as e:
        print(f"[WARN] mood save 失败: {e}", file=sys.stderr)


def _fresh() -> dict:
    return {
        "current": "平静",
        "energy": 70,
        "last_event": "",
        "last_changed": datetime.now().isoformat(),
        "history": [],
        "auto_change": True,
        "change_interval_minutes": 60,
    }


def status() -> dict:
    """读当前心情 + 顺便自动衰减 (auto_change=True 时)."""
    st = _load()
    if st.get("auto_change", True):
        st = _maybe_auto_change(st)
        _save(st)
    e = int(st.get("energy", 70))
    return {
        "current": st.get("current", "平静"),
        "energy": e,
        "level": _level(e),
        "style_modifier": _style_modifier(e, st.get("current", "平静")),
        "auto_change": st.get("auto_change", True),
        "last_event": st.get("last_event", ""),
        "last_changed": st.get("last_changed"),
        "history_count": len(st.get("history", [])),
    }


def _level(energy: int) -> str:
    if energy <= 30:
        return "low"
    if energy <= 70:
        return "mid"
    return "high"


def _style_modifier(energy: int, mood: str) -> str:
    """给 OpenClaw 看的写作风格建议."""
    length_map = {
        "low": "1-15 字，疲惫，简短，少用表情",
        "mid": "15-40 字，自然，不夸张",
        "high": "40-100 字，活跃，热情，可用 [doge][笑哭] 等表情",
    }
    mood_extra = {
        "兴奋": "语气非常兴奋，多用感叹号",
        "愉快": "语气轻松愉快，带微笑",
        "平静": "语气平稳、理性",
        "好奇": "充满好奇心，多提问",
        "慵懒": "慵懒随意，不正经",
        "深沉": "深沉有哲理",
        "调皮": "调皮爱开玩笑",
        "温柔": "温柔亲切",
        "毒舌": "犀利幽默，吐槽",
        "学究": "喜欢引经据典",
        "中二": "热血夸张",
        "佛系": "随缘淡然",
        "热血": "充满激情",
    }
    length = length_map.get(_level(energy), "15-40")
    extra = mood_extra.get(mood, "")
    return f"{length}。{extra}".strip("。")


def _maybe_auto_change(st: dict) -> dict:
    """每隔 N 分钟自动随机切换心情 (轻波动)."""
    last = st.get("last_changed")
    if not last:
        return st
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return st
    interval = int(st.get("change_interval_minutes", 60))
    if datetime.now() - last_dt < timedelta(minutes=interval):
        return st
    # 触发自动变: 50% 概率换心情, 50% 概率只调能量
    if random.random() < 0.5:
        new_mood = random.choice(MOODS)
        delta = random.randint(-10, 10)
        st["current"] = new_mood
        st["energy"] = max(0, min(100, int(st.get("energy", 70)) + delta))
        st["last_event"] = _sanitize_event(
            f"auto_change → {new_mood}", default=f"auto_change_{new_mood}")
    else:
        delta = random.randint(-15, 15)
        st["energy"] = max(0, min(100, int(st.get("energy", 70)) + delta))
        st["last_event"] = _sanitize_event(
            f"auto_energy {delta:+d}", default=f"auto_energy_{delta:+d}")
    st.setdefault("history", []).append({
        "ts": datetime.now().isoformat(),
        "mood": st["current"],
        "energy": st["energy"],
        "event": st["last_event"],
    })
    st["last_changed"] = datetime.now().isoformat()
    return st


def set_mood(mood: str, energy: int | None = None, event: str = "") -> dict:
    """手动设置心情 (CLI mood set <name> [--energy N])."""
    if mood not in MOODS:
        raise ValueError(f"mood 必须是 {MOODS} 之一, 收到 {mood!r}")
    st = _load()
    st["current"] = mood
    if energy is not None:
        st["energy"] = max(0, min(100, int(energy)))
    st["last_event"] = _sanitize_event(
        event, default=f"manual_set_{mood}")
    st.setdefault("history", []).append({
        "ts": datetime.now().isoformat(),
        "mood": mood, "energy": st["energy"],
        "event": st["last_event"],
    })
    st["last_changed"] = datetime.now().isoformat()
    _save(st)
    return status()


def nudge(energy_delta: int, event: str = "") -> dict:
    """调能量 (如跑了一次 watch 减 5, 用户点赞加 3)."""
    st = _load()
    st["energy"] = max(0, min(100, int(st.get("energy", 70)) + energy_delta))
    st["last_event"] = _sanitize_event(
        event, default=f"nudge_{energy_delta:+d}")
    st.setdefault("history", []).append({
        "ts": datetime.now().isoformat(),
        "mood": st["current"], "energy": st["energy"],
        "event": st["last_event"],
    })
    st["last_changed"] = datetime.now().isoformat()
    _save(st)
    return status()


def set_auto(auto: bool, interval_minutes: int | None = None) -> dict:
    """开/关自动变化 + 设置间隔."""
    st = _load()
    st["auto_change"] = bool(auto)
    if interval_minutes is not None:
        st["change_interval_minutes"] = int(interval_minutes)
    _save(st)
    return status()


def prompt_block() -> str:
    """v5.1 关键: 每次让 OpenClaw 写消息时, 把当前心情传给它."""
    s = status()
    return (
        f"## 你当前的心情状态\n"
        f"- 心情: {s['current']}\n"
        f"- 精力: {s['energy']}/100 ({s['level']} 水平)\n"
        f"- 风格: {s['style_modifier']}\n"
    )
