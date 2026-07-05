"""src/energy.py — v4 精力值系统.

状态：Data/energy.json
{
  "max_energy": 20,
  "current_energy": 20,
  "disabled": false,            # true = 无限精力（用户关闭此功能）
  "refill_seconds": 1800,       # 精力耗尽后等多少秒恢复（默认 30 min）
  "last_refill_ts": "<iso>",    # 上次重置时间
  "exhausted_until": "<iso>",   # 精力耗尽时被拒绝，直到这个时间才能恢复
  "total_consumed": 0           # 累计消耗（用户反馈用）
}

OpenClaw 调用模式：
  from src.energy import status, consume, set_max, set_disabled
  s = status()             # → {"current": 18, "max": 20, ...}
  consume(1)                # 消耗 1，可能抛 ExhaustedError
  set_max(30)
  set_disabled(False)

CLI 入口（OpenClaw 也可直接调）：
  bin/bilibili-autonomous energy status|consume [--n N]|set-max N|disabled on|off|refill
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config as cfg


class ExhaustedError(RuntimeError):
    """精力值耗尽时被拒绝."""
    def __init__(self, current: float, until_iso: str):
        self.current = current
        self.until_iso = until_iso
        super().__init__(f"energy exhausted (current={current}); resume at {until_iso}")


def _energy_path() -> Path:
    return cfg.DATA_DIR / "energy.json"


def _load() -> dict:
    if not _energy_path().exists():
        cfg.ensure_dirs()
        return _fresh()
    try:
        with open(_energy_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return _fresh()


def _save(st: dict) -> None:
    cfg.ensure_dirs()
    tmp = _energy_path().with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
            f.flush()
            import os
            os.fsync(f.fileno())
        os.replace(tmp, _energy_path())
    except OSError as e:
        print(f"[WARN] energy save failed: {e}", file=sys.stderr)


def _fresh() -> dict:
    """默认初始值（从 config 读 max_energy 和 disabled）."""
    app_cfg = cfg.load_app_config()
    energy_cfg = app_cfg.get("energy", {})
    max_e = int(energy_cfg.get("max_energy", 20))
    disabled = bool(energy_cfg.get("disabled", False))
    refill = int(energy_cfg.get("refill_seconds", 1800))
    return {
        "max_energy": max_e,
        "current_energy": max_e if not disabled else 999,
        "disabled": disabled,
        "refill_seconds": refill,
        "last_refill_ts": datetime.now().isoformat(),
        "exhausted_until": None,
        "total_consumed": 0,
    }


def _maybe_refill(st: dict) -> dict:
    """如果 cooldown 已过，按 v5.2 时段决定恢复值."""
    if st.get("disabled"):
        return st  # 关闭模式：无限
    eu = st.get("exhausted_until")
    if not eu:
        return st
    try:
        eu_dt = datetime.fromisoformat(eu)
    except (ValueError, TypeError):
        st["exhausted_until"] = None
        return st
    now = datetime.now()
    if now >= eu_dt:
        # 恢复: v5.2 active_hours 加 bonus / low_hours 减 penalty
        max_e = st["max_energy"]
        restore = max_e
        try:
            from . import energy_schedule as es
            sched = es.current_bonus_penalty()
            if sched.get("bonus", 0) > 0:
                restore = max_e + min(int(sched["bonus"]), 10)
            elif sched.get("penalty", 0) > 0:
                restore = max(0, max_e - min(int(sched["penalty"]), 10))
        except Exception:
            pass
        st["current_energy"] = restore
        st["exhausted_until"] = None
        st["last_refill_ts"] = now.isoformat()
    return st


def _from_config_overrides(st: dict) -> dict:
    """sync from config DEFAULTS（config 里没设的字段不动 state）.

    v4.1 修：只 sync 显式出现在 config["energy"] 里的字段。disabled 默认空，
    由 API 单独控制（set_disabled），避免被 config 默认覆盖回 False。
    v5.8 Hermes-2 修：disabled 从 True → False 时把 current_energy 重置回
    max_energy，避免一直显示 999。
    """
    app_cfg = cfg.load_app_config()
    energy_cfg = app_cfg.get("energy", {})
    if not energy_cfg:
        return st  # config 里没 energy 块，不动
    if "max_energy" in energy_cfg:
        st["max_energy"] = int(energy_cfg["max_energy"])
    if "refill_seconds" in energy_cfg:
        st["refill_seconds"] = int(energy_cfg["refill_seconds"])
    if "disabled" in energy_cfg and isinstance(energy_cfg["disabled"], bool):
        new_disabled = energy_cfg["disabled"]
        if new_disabled and not st.get("disabled"):
            # False → True: 切到无限模式
            st["current_energy"] = 999
            st["exhausted_until"] = None
        elif not new_disabled and st.get("disabled"):
            # True → False: 切回正常模式, current 重置回 max_energy
            # 避免一直显示 999
            st["current_energy"] = int(st.get("max_energy", 20))
            st["exhausted_until"] = None
        st["disabled"] = new_disabled
    return st


def status() -> dict[str, Any]:
    """读当前精力状态 + 自动 refill 检查."""
    st = _from_config_overrides(_load())
    st = _maybe_refill(st)
    _save(st)
    current = st["current_energy"]
    out = {
        "current": float(current),
        "max": int(st["max_energy"]),
        "disabled": st.get("disabled", False),
        "last_refill_ts": st.get("last_refill_ts"),
        "exhausted_until": st.get("exhausted_until"),
        "refill_seconds": int(st.get("refill_seconds", 1800)),
        "total_consumed": int(st.get("total_consumed", 0)),
    }
    if out["exhausted_until"]:
        try:
            eu_dt = datetime.fromisoformat(out["exhausted_until"])
            out["seconds_until_resume"] = max(0, int((eu_dt - datetime.now()).total_seconds()))
        except (ValueError, TypeError):
            out["seconds_until_resume"] = 0
    return out


def consume(n: int = 1) -> dict[str, Any]:
    """消耗 n 精力. 若 disabled 模式直接返回; 若 ≤0 抛 ExhaustedError."""
    st = _from_config_overrides(_load())
    st = _maybe_refill(st)
    if st.get("disabled"):
        return status()
    cur = float(st["current_energy"])
    if cur - n < 0:
        # 触发 cooldown
        now = datetime.now()
        refill_s = int(st.get("refill_seconds", 1800))
        until = (now + timedelta(seconds=refill_s)).isoformat()
        st["exhausted_until"] = until
        st["current_energy"] = 0
        _save(st)
        raise ExhaustedError(0.0, until)
    st["current_energy"] = float(cur - n)
    st["total_consumed"] = int(st.get("total_consumed", 0)) + n
    # 如果降到 0 也触发 cooldown
    if st["current_energy"] <= 0:
        now = datetime.now()
        refill_s = int(st.get("refill_seconds", 1800))
        st["exhausted_until"] = (now + timedelta(seconds=refill_s)).isoformat()
    _save(st)
    return status()


def set_max(max_energy: int) -> dict:
    """重置 max_energy（同时 current 上调到新的 max）。v4.1 修复：同步写 config.json."""
    if max_energy < 1 or max_energy > 1000:
        raise ValueError(f"max_energy must be 1..1000, got {max_energy}")
    st = _load()
    st["max_energy"] = int(max_energy)
    st["current_energy"] = int(max_energy)
    st["exhausted_until"] = None
    _save(st)
    # 同步到 config.json (防止 _from_config_overrides 反向覆盖回旧值)
    cfg.write_energy_config(max_energy=int(max_energy))
    return status()


def set_disabled(disabled: bool) -> dict:
    """用户手动开/关精力值功能（关闭 = 无限）。v4.1 修复：同步写 config.json.

    v5.8 Hermes-2 修：disabled True → False 时把 current_energy 重置回
    max_energy，避免一直显示 999 死值。
    """
    st = _load()
    was_disabled = bool(st.get("disabled"))
    st["disabled"] = bool(disabled)
    if disabled:
        st["current_energy"] = 999
        st["exhausted_until"] = None
    elif was_disabled:
        # True → False: 切回正常精力模式, 重置 current 回 max
        st["current_energy"] = int(st.get("max_energy", 20))
        st["exhausted_until"] = None
    _save(st)
    cfg.write_energy_config(disabled=bool(disabled))
    return status()


def set_refill_seconds(seconds: int) -> dict:
    """设置 cooldown 时长（默认 1800 秒 = 30 分钟）."""
    if seconds < 10 or seconds > 86400:
        raise ValueError(f"refill_seconds must be 10..86400, got {seconds}")
    st = _load()
    st["refill_seconds"] = int(seconds)
    _save(st)
    return status()


def force_refill() -> dict:
    """立即重置精力到 max（用于用户手动加 buff）。"""
    st = _load()
    st["current_energy"] = st["max_energy"]
    st["exhausted_until"] = None
    st["last_refill_ts"] = datetime.now().isoformat()
    _save(st)
    return status()
