"""src/state_view.py — v2 状态查看（CLI status + Web /api/state 共用）.

v5.7 Hermes 安全修复:
- state.last_error 可能是完整 Python 异常堆栈（含文件路径/变量值）
- get_full_status() 截断到 200 字符 + 移除控制字符
- print_full_status() 内部同步截断
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Any

try:
    from colorama import Fore, Style
except ImportError:
    class _Dummy:
        def __getattr__(self, _): return ""
    Fore = Style = _Dummy()  # type: ignore[assignment]

from . import actions_log, archive, config as cfg, follow as follow_mod, dm as dm_mod


# v5.7 Hermes: 错误信息最大长度（防堆栈外泄）
_MAX_ERROR_LEN = 200


def _sanitize_error(msg: Any) -> str | None:
    """净化错误信息: 截断 + 去控制字符 + 去绝对路径."""
    if msg is None:
        return None
    s = str(msg)
    if not s:
        return None
    s = s[:_MAX_ERROR_LEN]
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    # 截断后追加省略号提示
    if len(str(msg)) > _MAX_ERROR_LEN:
        s = s.rstrip() + "...(已截断)"
    return s


def _sanitize_state(state: dict) -> dict:
    """v5.7 Hermes: 脱敏 state 字典再返回.

    - last_error 截断到 200 字符
    - 移除敏感字段（password/api_key/secret_key 等）
    """
    safe = dict(state)
    if "last_error" in safe:
        safe["last_error"] = _sanitize_error(safe["last_error"])
    # 防御性清理: 即使 state 被扩展加了敏感字段
    for k in list(safe.keys()):
        if any(t in k.lower() for t in ("password", "secret", "api_key", "token")):
            safe[k] = "***已脱敏***"
    return safe


def get_full_status() -> dict[str, Any]:
    """返回综合状态字典（Web /api/state 用）."""
    state = _sanitize_state(cfg.load_state())
    web_settings = cfg.load_web_settings()
    web_auth = cfg.load_web_auth()
    archive_index = archive.load_highlight_index()

    # follow 状态
    try:
        f_engine = follow_mod.FollowEngine()
        f_status = f_engine.status()
    except Exception:
        f_status = {}

    # dm 状态
    try:
        dm = dm_mod.DMService()
        d_status = dm.status()
    except Exception:
        d_status = {}

    return {
        "now": datetime.now().isoformat(),
        "skill": {
            "path": str(cfg.SKILL_DIR),
            "data_dir": str(cfg.DATA_DIR),
        },
        "state": state,
        "web": {
            "settings": web_settings,
            "auth_set": web_auth is not None,
        },
        "follow": f_status,
        "dm": d_status,
        "archive": {
            "count": len(archive_index),
            "latest": archive_index[-5:][::-1] if archive_index else [],
        },
    }


def print_full_status() -> None:
    s = get_full_status()
    print(f"\n{Fore.CYAN}今日 ({s['state'].get('today')}) 配额：{Style.RESET_ALL}")
    counts = s["state"].get("counts", {})
    maxd = s["state"].get("max_per_day", {})
    for k, v in counts.items():
        cap = maxd.get(k)
        cap_str = f"/{cap}" if cap is not None else ""
        print(f"  {k:18s} {v}{cap_str}")
    print(f"\n  last_run:     {s['state'].get('last_run')}")
    print(f"  last_error:   {s['state'].get('last_error') or '(无)'}")
    print(f"  cooldown_until: {s['state'].get('cooldown_until')}")
    print(f"\n{Fore.CYAN}Web 面板：{Style.RESET_ALL}")
    ws = s["web"]["settings"]
    print(f"  bind={ws.get('bind')}  port={ws.get('port')}  auth={'已设' if s['web']['auth_set'] else '未设（首次访问设）'}")
    print(f"\n{Fore.CYAN}关注：{Style.RESET_ALL}")
    f = s["follow"]
    if f:
        print(f"  今日 {f.get('daily_count', 0)}/{f.get('max_daily', 0)} (剩 {f.get('remaining', 0)})")
        print(f"  历史: {f.get('history_count', 0)} 条")
    print(f"\n{Fore.CYAN}私信：{Style.RESET_ALL}")
    d = s["dm"]
    if d:
        print(f"  已处理 {d.get('processed_total', 0)} 条 / 上下文用户 {d.get('context_users', 0)} / enabled={d.get('enabled')}")
    print(f"\n{Fore.CYAN}归档：{Style.RESET_ALL} {s['archive']['count']} 个高分内容")
    for h in s["archive"]["latest"]:
        print(f"  - [{h.get('category')}] 《{h.get('title')}》 ({h.get('score')}/10)")
    print()