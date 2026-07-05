"""src/keywords.py — v5.3 关键词系统升级 (同义词/排除词/心理学 hook).

Config (Data/config.json):
{
  "keyword_system": {
    "favorite": {
      "include": ["AI", "机器学习", "Python"],
      "synonyms": {             # 词组 → 展开后的关键词列表 (任一命中)
        "AI": ["AI", "A.I.", "人工智能", "artificial intelligence", "GPT", "LLM"],
        "Python": ["Python", "py", "pythonic"]
      },
      "exclude": ["卖课", "广告"],  # 排除词 (任一命中 → 跳过)
      "match_mode": "any"        # any | all (展开后)
    },
    "video_filter": {  # v4.8 视频关键词过滤也升级
      "include": ["AI"],
      "synonyms": {"AI": ["AI", "GPT", "LLM"]},
      "exclude": ["卖课"],
      "match_mode": "any"
    }
  }
}

OpenClaw 心理学 hook:
  ~/.openclaw/memory/psychology.md  ← OpenClaw 自己写, 描述用户兴趣/性格
  本 skill 在 should_process_video() 后调用 load_psychology() 返回内容,
  OpenClaw 收到 video_filter 输出后参考 psychology 调整决策.
  skill 不读 psychology 内容 (那是 OpenClaw 的事); 只提供路径.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import config as cfg


PSYCHOLOGY_PATH = Path.home() / ".openclaw" / "memory" / "psychology.md"

# v5.7 Hermes 安全修复:
# - 关键词最大长度（防 ReDoS 回溯爆炸 + 防内存膨胀）
_MAX_KW_LEN = 64
# - 每个 section 最大 include 数
_MAX_INCLUDES = 100


def _validate_keyword(kw: Any, kind: str = "keyword") -> str:
    """v5.7 Hermes: 净化单个关键词.

    - 转 str + 截断到 _MAX_KW_LEN
    - 移除控制字符 + 移除正则元字符（用 in 而非 re 匹配, 防 ReDoS）
    """
    if kw is None:
        return ""
    s = str(kw).strip()
    if not s:
        return ""
    s = s[:_MAX_KW_LEN]
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    return s


def _load_section(section: str) -> dict:
    """读 config.keyword_system.<section>, 返回该 section 配置."""
    full = cfg.load_app_config()
    return full.get("keyword_system", {}).get(section, {})


def _expand_keywords(section: dict) -> tuple[list[str], list[str]]:
    """把 include + synonyms 展开成实际关键词列表 (去重保序).

    Returns: (include_list, exclude_list)

    v5.7 Hermes:
    - include/exclude 都做 _validate_keyword 防超长/控制字符
    - include 数量限制 _MAX_INCLUDES
    """
    raw_includes = list(section.get("include", []) or [])
    syns = section.get("synonyms", {}) or {}
    # 净化
    includes: list[str] = []
    seen = set()
    for kw in raw_includes:
        clean = _validate_keyword(kw)
        if clean and clean not in seen:
            includes.append(clean)
            seen.add(clean)
    for key, expansions in syns.items():
        clean_key = _validate_keyword(key)
        if not clean_key or clean_key not in includes:
            continue
        for e in expansions:
            clean_e = _validate_keyword(e)
            if clean_e and clean_e not in seen:
                includes.append(clean_e)
                seen.add(clean_e)
            if len(includes) >= _MAX_INCLUDES:
                break
        if len(includes) >= _MAX_INCLUDES:
            break
    excludes: list[str] = []
    for kw in (section.get("exclude", []) or []):
        clean = _validate_keyword(kw)
        if clean:
            excludes.append(clean)
    return includes, excludes


def match_keywords(text: str, section: dict) -> tuple[bool, list[str]]:
    """通用关键词匹配: 展开 include + 检查 exclude.

    Returns: (matched_includes, matched_keywords)

    v5.7 Hermes 修复:
    - match_mode="all" 现在真正要求每个原始 include 词至少命中 1 个 synonym
      (而不是与 any 等同)
    - include/exclude 已经在 _expand_keywords 中净化 + 截断

    语义:
      any → 任一 include 命中就算匹配
      all → 每个原始 include 都至少命中 1 个 synonym
    """
    if not text or not section:
        return False, []
    includes, excludes = _expand_keywords(section)
    if not includes:
        return False, []
    mode = section.get("match_mode", "any")
    haystack = str(text).lower()
    matched: list[str] = []
    for kw in includes:
        if kw and kw.lower() in haystack:
            matched.append(kw)
    # exclude: 任一命中 → 整体不算匹配
    for ex in excludes:
        if ex and ex.lower() in haystack:
            return False, matched
    if mode == "all":
        # 真正的 all 语义: 检查每个原始 include 词 (展开前)
        # 是否至少有 1 个 synonym 命中
        raw_includes = [
            _validate_keyword(x) for x in (section.get("include", []) or [])
        ]
        raw_includes = [x for x in raw_includes if x]
        if not raw_includes:
            return len(matched) > 0, matched
        syns = section.get("synonyms", {}) or {}
        missing: list[str] = []
        matched_set = {m.lower() for m in matched}
        for raw in raw_includes:
            raw_low = raw.lower()
            expansions = [_validate_keyword(x).lower() for x in syns.get(raw, []) or []]
            expansions = [x for x in expansions if x]
            # 必须命中 raw 本身 OR 它的任一 synonym
            if raw_low not in matched_set and not any(e in matched_set for e in expansions):
                missing.append(raw)
        if missing:
            return False, matched
        return True, matched
    return len(matched) > 0, matched


def should_favorite(title: str, desc: str = "", up_name: str = "",
                    config: dict | None = None) -> tuple[bool, list[str], str]:
    """v5.3: 收藏关键词匹配 (含 synonyms + exclude)."""
    section = _load_section("favorite") if config is None else config
    text = f"{title or ''}\n{up_name or ''}\n{desc or ''}"
    return _match_with_reason(text, section, "favorite")


def should_process_video(title: str, up_name: str = "",
                         desc: str = "",
                         config: dict | None = None) -> tuple[bool, str]:
    """v5.3: 视频关键词过滤 (含 synonyms + exclude).

    Returns: (matched, reason_str)
    """
    section = _load_section("video_filter") if config is None else config
    text = f"{title or ''}\n{up_name or ''}\n{desc or ''}"
    matched, _kws, reason = _match_with_reason(text, section, "video_filter")
    return matched, reason


def _match_with_reason(text: str, section: dict, name: str) -> tuple[bool, list[str], str]:
    if "enabled" in section and not section.get("enabled", True):
        return True, [], f"{name} disabled"
    matched, kws = match_keywords(text, section)
    if matched:
        return True, kws, f"matched={kws}"
    includes, _ = _expand_keywords(section)
    reason_str = f"no_match (need {includes})" if includes else f"no_keywords ({name})"
    return False, kws, reason_str


def load_psychology_path() -> str:
    """OpenClaw 心理学 hook: 返回 psychology.md 路径 (不读内容, 让 OpenClaw 读)."""
    return str(PSYCHOLOGY_PATH)


# 兼容 v4.2 的 should_favorite_by_keywords (单关键词字符串)
def should_favorite_by_keywords(title: str, desc: str = "",
                                 up_name: str = "",
                                 config: dict | None = None) -> tuple[bool, list[str]]:
    """v4.2 兼容接口 — 不带 synonyms/exclude (向后兼容老代码)."""
    if config is None:
        fav_section = _load_section("favorite")
        if fav_section.get("include"):
            config = {"favorite": {
                "enabled": fav_section.get("enabled", True),
                "keywords": fav_section.get("include", []),
                "match_mode": fav_section.get("match_mode", "any"),
            }}
        else:
            config = {"favorite": {"enabled": True, "keywords": [], "match_mode": "any"}}
    matched, kws, _ = should_favorite(title, desc, up_name, config)
    return matched, kws
