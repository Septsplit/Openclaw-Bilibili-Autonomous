"""src/energy_schedule.py — v5.2 精力时段系统 (扩展 src/energy.py).

Data/energy_schedule.json
{
  "active_hours": [
    {"start": "20:00", "end": "23:00", "bonus_max": 10}
  ],
  "low_hours": [
    {"start": "01:00", "end": "05:00", "penalty_max": 10}
  ],
  "default_bonus": 5,    # active 时段不显式配置时用
  "default_penalty": 5   # low 时段不显式配置时用
}

逻辑:
- 在 active_hours 时段里 cooldown 后 → 恢复到 max + bonus (bonus ≤ bonus_max, ≤ 10)
- 在 low_hours 时段里 cooldown 后 → 恢复到 max - penalty (penalty ≤ penalty_max, ≤ 10)
- 都不在 → 恢复到 max (默认)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any

from . import config as cfg


def _schedule_path() -> Path:
    return cfg.DATA_DIR / "energy_schedule.json"


def _load() -> dict:
    if not _schedule_path().exists():
        return _fresh()
    try:
        with open(_schedule_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return _fresh()


def _save(st: dict) -> None:
    cfg.ensure_dirs()
    tmp = _schedule_path().with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
            f.flush()
            import os
            os.fsync(f.fileno())
        os.replace(tmp, _schedule_path())
    except OSError as e:
        print(f"[WARN] energy_schedule save failed: {e}", file=sys.stderr)


def _fresh() -> dict:
    return {
        "active_hours": [],
        "low_hours": [],
        "default_bonus": 5,
        "default_penalty": 5,
    }


def _parse_hhmm(s: str) -> time | None:
    """严格验证 HH:MM 格式 (v5.7 Hermes 修复).

    - 必须严格 5 字符: HH:MM
    - 小时 0-23, 分钟 0-59
    - 失败返回 None
    """
    if not isinstance(s, str) or len(s) != 5 or s[2] != ":":
        return None
    h_str, m_str = s[:2], s[3:]
    if not (h_str.isdigit() and m_str.isdigit()):
        return None
    h, m = int(h_str), int(m_str)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    try:
        return time(h, m)
    except ValueError:
        return None


def _validate_hhmm(s: str, label: str) -> str:
    """v5.7 Hermes: add_active_hours/add_low_hours 入口验证, 错误抛 ValueError."""
    if _parse_hhmm(s) is None:
        raise ValueError(
            f"{label} 必须是 HH:MM 格式 (00-23:00-59), 收到 {s!r}. "
            f"示例: '20:00', '01:30', '09:05'"
        )
    return s


def _in_range(now_t: time, start_s: str, end_s: str) -> bool:
    s = _parse_hhmm(start_s)
    e = _parse_hhmm(end_s)
    if not s or not e:
        return False
    # 支持跨午夜 (e.g. 22:00-02:00)
    if s <= e:
        return s <= now_t < e
    return now_t >= s or now_t < e


def current_bonus_penalty() -> dict:
    """按当前时间查 active/low 时段, 返回 {bonus, penalty}."""
    st = _load()
    now_t = datetime.now().time()
    bonus, penalty = 0, 0
    for slot in st.get("active_hours", []):
        if _in_range(now_t, slot.get("start", ""), slot.get("end", "")):
            bonus = max(bonus, min(int(slot.get("bonus_max", st.get("default_bonus", 5))), 10))
    for slot in st.get("low_hours", []):
        if _in_range(now_t, slot.get("start", ""), slot.get("end", "")):
            penalty = max(penalty, min(int(slot.get("penalty_max", st.get("default_penalty", 5))), 10))
    return {"bonus": bonus, "penalty": penalty, "now": now_t.strftime("%H:%M")}


def status() -> dict:
    return {
        "active_hours": _load().get("active_hours", []),
        "low_hours": _load().get("low_hours", []),
        "default_bonus": _load().get("default_bonus", 5),
        "default_penalty": _load().get("default_penalty", 5),
        "current": current_bonus_penalty(),
    }


def add_active_hours(start: str, end: str, bonus_max: int = 5) -> dict:
    """加 active_hours 时段 (心情活跃, 恢复时多 1~10 精力).

    v5.7 Hermes: start/end 入口严格验证 HH:MM 格式.
    v5.8 Hermes-2: (start, end) 已存在则跳过（去重），bonus_max 不同时更新第一个.
    """
    if bonus_max < 1 or bonus_max > 10:
        raise ValueError(f"bonus_max 1..10, 收到 {bonus_max}")
    start = _validate_hhmm(start, "active_hours.start")
    end = _validate_hhmm(end, "active_hours.end")
    st = _load()
    slots = st.setdefault("active_hours", [])
    # 去重: 已存在 (start, end) 跳过
    for ex in slots:
        if ex.get("start") == start and ex.get("end") == end:
            # bonus_max 不同就更新第一个, 让最新配置生效
            ex["bonus_max"] = bonus_max
            _save(st)
            return status()
    slots.append({"start": start, "end": end, "bonus_max": bonus_max})
    _save(st)
    return status()


def add_low_hours(start: str, end: str, penalty_max: int = 5) -> dict:
    """加 low_hours 时段 (精力缺乏, 恢复时少 1~10 精力).

    v5.7 Hermes: start/end 入口严格验证 HH:MM 格式.
    v5.8 Hermes-2: (start, end) 已存在则跳过（去重），penalty_max 不同时更新第一个.
    """
    if penalty_max < 1 or penalty_max > 10:
        raise ValueError(f"penalty_max 1..10, 收到 {penalty_max}")
    start = _validate_hhmm(start, "low_hours.start")
    end = _validate_hhmm(end, "low_hours.end")
    st = _load()
    slots = st.setdefault("low_hours", [])
    # 去重: 已存在 (start, end) 跳过
    for ex in slots:
        if ex.get("start") == start and ex.get("end") == end:
            ex["penalty_max"] = penalty_max
            _save(st)
            return status()
    slots.append({"start": start, "end": end, "penalty_max": penalty_max})
    _save(st)
    return status()


def clear() -> dict:
    """清空所有时段."""
    st = _load()
    st["active_hours"] = []
    st["low_hours"] = []
    _save(st)
    return status()
