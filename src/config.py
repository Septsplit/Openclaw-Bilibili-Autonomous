"""src/config.py — 简化版 config loader + v2 状态/日志/归档.

v1: 只加载核心 config + state + heartbeat_log。
v2: 扩展 DEFAULTS（加 dm/follow/scoring/web_panel/video_understanding/persona/mood），
    加 actions_log + archive 的底层工具函数，web_settings.json 读写。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ===== 路径 =====
SKILL_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SKILL_DIR / "Data"
CONFIG_FILE = DATA_DIR / "config.json"
COOKIE_FILE = DATA_DIR / "bilibili_cookies.json"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "heartbeat_log.jsonl"  # v1 兼容
LOGS_DIR = DATA_DIR / "logs"
ACTIONS_DIR = DATA_DIR / "actions"
TEMPLATES_DIR = SKILL_DIR / "templates"

# v2 新增的运行时数据文件
WEB_AUTH_FILE = DATA_DIR / "web_auth.json"
WEB_SETTINGS_FILE = DATA_DIR / "web_settings.json"
PERSONAS_FILE = DATA_DIR / "personas.json"
MOOD_FILE = DATA_DIR / "mood_state.json"
USER_PROFILES_FILE = DATA_DIR / "user_profiles.json"
DM_LOG_FILE = DATA_DIR / "dm_log.json"
DM_CONTEXT_FILE = DATA_DIR / "dm_context.json"
FOLLOW_STATE_FILE = DATA_DIR / "follow_state.json"
HIGHLIGHT_INDEX_FILE = DATA_DIR / "highlight_index.json"

# ===== 默认值（v2 扩展） =====
DEFAULTS: dict[str, Any] = {
    "behavior": {
        "comment_mode": "real",
        "min_reply_delay_seconds": 4,
        "max_reply_delay_seconds": 18,
    },
    "interaction": {
        "max_coins_daily": 2,
        "max_danmaku_daily": 2,
        "max_comments_daily": 5,
        "fav_threshold": 8.5,
    },
    "danmaku": {
        "enabled": True,
        "send_prob": 0.03,
        "max_daily_send": 2,
    },
    "reply_safety": {
        "enabled": True,
        "block_on_outgoing": True,
        "blocked_keywords": [],
    },
    "dm": {
        "enabled": True,
        "auto_reply": True,
        "check_interval": 120,
        "max_replies_per_check": 3,
        "only_recent_seconds": 900,
        "private_reply_cooldown_minutes": 3,
        "context_len": 20,
        "proactive_prob": 0.02,
        "proactive_targets": "followings",
    },
    "follow": {
        "enabled": True,
        "auto_follow_prob": 0.08,
        "max_daily_follows": 3,
        "cooldown_minutes": 90,
        "min_score": 7.0,
        "min_impressions": 2,
        "exceptional_score": 8.5,
        "unfollow_inactive_days": 0,
    },
    "scoring": {
        "coin_min": 8.0,
        "favorite_min": 8.5,
        "comment_min": 6.5,
        "follow_min": 7.0,
        "archive_min": 7.5,
        "understand_min": 6.0,
    },
    "energy": {
        "max_energy": 20,
        "refill_seconds": 1800,
        "disabled": False,
    },
    "energy_schedule": {
        "active_hours": [],
        "low_hours": [],
        "default_bonus": 5,
        "default_penalty": 5,
    },
    "mood": {
        "auto_change": True,
        "change_interval_minutes": 60,
    },
    "keyword_system": {
        "favorite": {
            "enabled": True,
            "include": [],
            "synonyms": {},
            "exclude": [],
            "match_mode": "any",
        },
        "video_filter": {
            "enabled": False,
            "include": [],
            "synonyms": {},
            "exclude": [],
            "match_mode": "any",
        },
    },
    "video_filter": {
        "enabled": False,
        "include_keywords": [],
        "match_mode": "any",
    },
    "favorite": {
        "enabled": True,
        "keyword_enabled": True,
        "keywords": [],
        "match_mode": "any",
        "min_score": 0.0,
        "auto_on_score": True,
    },
    "autonomy": {
        "enabled": True,
        "enable_like": True,
        "prob_like": 0.50,
        "enable_coin": True,
        "prob_coin": 0.25,
        "enable_comment": True,
        "prob_comment": 0.15,
        "enable_danmaku": True,
        "prob_danmaku": 0.03,
        "enable_favorite": True,
        "prob_favorite": 0.10,
        "enable_dm_reply": True,
        "prob_dm_reply": 0.50,
        "enable_proactive_dm": False,
        "prob_proactive_dm": 0.02,
        "enable_high_quality_archive": True,
        "prob_archive": 0.30,
        "enable_proactive_coin_like": True,
        "prob_proactive_coin_like": 0.08,
        "enable_dm_send": True,
        "prob_dm_send": 0.02,
    },
    "web_panel": {
        "bind": "127.0.0.1",
        "port": 8765,
        "secret_key": "",
    },
}

# ===== 工具函数 =====

def ensure_dirs() -> None:
    """确保所有运行时目录存在."""
    for d in [DATA_DIR, LOGS_DIR, ACTIONS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    (ACTIONS_DIR / "comments").mkdir(exist_ok=True)
    (ACTIONS_DIR / "danmaku").mkdir(exist_ok=True)
    (ACTIONS_DIR / "dms").mkdir(exist_ok=True)
    (ACTIONS_DIR / "follows").mkdir(exist_ok=True)
    (ACTIONS_DIR / "understandings").mkdir(exist_ok=True)
    (ACTIONS_DIR / "highlights").mkdir(exist_ok=True)
    (DATA_DIR / "knowledge").mkdir(exist_ok=True)
    (DATA_DIR / "logs").mkdir(exist_ok=True)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_app_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            cfg = _deep_merge(cfg, on_disk)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_app_config(cfg: dict) -> bool:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        return True
    except OSError as e:
        print(f"[WARN] save_app_config 失败: {e}", file=sys.stderr)
        return False


def load_cookies() -> dict:
    if not COOKIE_FILE.exists():
        return {}
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def mask_secret(value: str, show_head: int = 6, show_tail: int = 4) -> str:
    if not value:
        return "(未配置)"
    if len(value) <= show_head + show_tail:
        return "*" * len(value)
    return f"{value[:show_head]}...{value[-show_tail:]}"


# v5.9 Cookie 解析/写入工具（CLI + Web 共用）
# 输入：浏览器 Network → Headers → Cookie 那一整段
# 例: "SESSDATA=abc%2C123; bili_jct=xyz; DedeUserID=12345; ac_time_value=xxx"

_COOKIE_KEYS_ALLOWED = ("SESSDATA", "bili_jct", "DedeUserID", "ac_time_value")


def parse_cookie_header(raw: str) -> dict:
    """从整段 Cookie 字符串里挑出 4 个 B 站凭据字段.

    输入容忍：
      - ';' / ',;' 分隔
      - 'Cookie: xxx' 前缀
      - 大小写不敏感（统一转大写比对）
      - 字段值含 '=' 的（bili_jct 偶尔），按第一个 '=' 切
    没用到的字段不会写入，返回 dict 只含实际解析到的字段。
    """
    if not raw:
        return {}
    s = raw.strip()
    # 去 'Cookie:' 前缀（粘贴时可能连带请求头）
    if s.lower().startswith("cookie:"):
        s = s[len("cookie:"):].strip()
    out: dict = {}
    # 同时支持 '; ' 和 '\n' 分隔
    for part in re.split(r"[;\n]+", s):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if not k or not v:
            continue
        # 大写比对（cookie 名大小写不敏感）
        ku = k.upper()
        for ak in _COOKIE_KEYS_ALLOWED:
            if ku == ak.upper():
                out[ak] = v
                break
    return out


def load_cookies_raw() -> dict:
    """读现有 cookie —— 含 '是 symlink 还是普通文件' 判断.
    如果文件不存在，返回 {}.
    """
    return load_cookies()


def mask_cookies_dict(c: dict) -> dict:
    """给前端 / 日志用，4 个字段都脱敏展示."""
    return {k: (mask_secret(v) if isinstance(v, str) and v else "(未配置)")
            for k, v in c.items()}


def save_cookies(cookies: dict) -> bool:
    """写入 Data/bilibili_cookies.json.

    兼容两种情况：
      - Data/bilibili_cookies.json 是 symlink：直接写 symlink 链目标（不替换 symlink）
      - Data/bilibili_cookies.json 是普通文件：原子写（tmp + replace）
    """
    if not cookies:
        _warn("save_cookies: 空 dict，跳过")
        return False
    target = COOKIE_FILE
    if target.is_symlink():
        # 写入 symlink 指向的真实文件（保留原链接结构，避免破坏 bilibili_agent 共享）
        target = target.resolve()
    elif not target.exists():
        # 确保父目录在
        target.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        return True
    except (OSError, PermissionError) as e:
        print(f"[WARN] save_cookies 失败: {e}", file=sys.stderr)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def cookie_status() -> dict:
    """给 CLI/Web 显示用：当前 4 个字段有没有 + 总览."""
    c = load_cookies()
    return {
        "present": [k for k in _COOKIE_KEYS_ALLOWED if c.get(k)],
        "missing": [k for k in _COOKIE_KEYS_ALLOWED if not c.get(k)],
        "dict": {k: (c[k] if k in c else "") for k in _COOKIE_KEYS_ALLOWED},
        "count": sum(1 for k in _COOKIE_KEYS_ALLOWED if c.get(k)),
        "complete": all(c.get(k) for k in _COOKIE_KEYS_ALLOWED),
    }


# 给 cfg 自己用，避免循环
def _warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


# v5.8 Hermes-2 修复: actions_log.write_action_markdown 调用了
# 但 cfg.safe_filename 不存在 → comment/danmaku/dm 等命令崩溃
_FILENAME_BAD = re.compile(r"[^0-9A-Za-z一-鿿._-]+")
_FILENAME_SEP = re.compile(r"[\\/]+")
_FILENAME_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(name: Any, max_len: int = 80) -> str:
    """把任意字符串变成安全文件名片段.

    - 转 str → 过滤控制字符 → 反斜杠/斜杠替换成 '_' → 去除路径分隔符
    - 非 [0-9A-Za-z中文._-] 字符全部替换成 '_' (避免 shell 注入/特殊字符)
    - 截断到 max_len, 截断后清掉尾部的 _ 和 .
    - 空串 → 'unnamed'
    """
    if name is None:
        return "unnamed"
    s = str(name)
    s = _FILENAME_CTRL.sub("", s)
    s = _FILENAME_SEP.sub("_", s)
    # 防 ../ 路径遍历: 先把 .. 也过滤
    s = s.replace("..", "_")
    s = _FILENAME_BAD.sub("_", s)
    # 去掉前导点 (隐藏文件)
    s = s.lstrip(".")
    s = s[:max_len]
    s = s.rstrip("._-") or "unnamed"
    return s


# ===== State 管理 =====

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return _fresh_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _fresh_state()
    if st.get("today") != _today():
        st["today"] = _today()
        st["counts"] = {k: 0 for k in st.get("counts", {})}
    return st


def save_state(st: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _fresh_state() -> dict:
    return {
        "last_run": None, "last_error": None, "cooldown_until": None,
        "today": _today(),
        "counts": {"videos_watched": 0, "likes": 0, "coins": 0, "comments": 0,
                   "danmaku": 0, "favorites": 0, "dms_sent": 0,
                   "dms_received": 0, "follows": 0, "unfollows": 0,
                   "understandings": 0},
        "max_per_day": {"coins": 2, "danmaku": 2, "comments": 5,
                         "follows": 3, "dms_sent": 10, "understandings": 5},
    }


def append_log(entry: dict) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[WARN] append_log 失败: {e}", file=sys.stderr)


# ===== Web 认证 =====

def load_web_auth() -> dict | None:
    if not WEB_AUTH_FILE.exists():
        return None
    try:
        with open(WEB_AUTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_web_auth(user: str, password_hash: str) -> bool:
    try:
        tmp = WEB_AUTH_FILE.with_suffix(WEB_AUTH_FILE.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"user": user, "hash": password_hash},
                      f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, WEB_AUTH_FILE)
        return True
    except OSError as e:
        print(f"[WARN] save_web_auth 失败: {e}", file=sys.stderr)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def load_web_settings() -> dict:
    if not WEB_SETTINGS_FILE.exists():
        return {"bind": "127.0.0.1", "port": 8765, "secret_key": ""}
    try:
        with open(WEB_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("bind", "127.0.0.1")
        data.setdefault("port", 8765)
        data.setdefault("secret_key", "")
        return data
    except (OSError, json.JSONDecodeError):
        return {"bind": "127.0.0.1", "port": 8765, "secret_key": ""}


def save_web_settings(cfg: dict) -> bool:
    try:
        with open(WEB_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        print(f"[WARN] save_web_settings 失败: {e}", file=sys.stderr)
        return False


# v5 兼容 helpers: write_energy_config 等
def write_energy_config(max_energy=None, disabled=None, refill_seconds=None) -> bool:
    """v4.1: 持久化 energy 配置到 Data/config.json (软链到 bilibili_agent)."""
    if not CONFIG_FILE.exists() and not CONFIG_FILE.is_symlink():
        return False
    try:
        c = load_app_config()
        e = c.setdefault("energy", {})
        if max_energy is not None:
            e["max_energy"] = int(max_energy)
        if disabled is not None:
            e["disabled"] = bool(disabled)
        if refill_seconds is not None:
            e["refill_seconds"] = int(refill_seconds)
        return save_app_config(c)
    except (OSError, ValueError) as exc:
        print(f"[WARN] write_energy_config 失败: {exc}", file=sys.stderr)
        return False


# 调一次 ensure_dirs
try:
    ensure_dirs()
except (OSError, PermissionError):
    pass
