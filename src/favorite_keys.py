"""src/favorite_keys.py — v4.2 关键词收藏 + v4.8 视频关键词过滤.

OpenClaw 调用 `main.py favorite <bvid> --auto-check` 时：
  1. skill 读 video 元信息
  2. 检查 title/up/desc 是否含 favorite.keywords
  3. 匹配规则 match_mode = "any"（默认含任意一个）或 "all"（全部）
  4. min_score 检查（如果 OpenClaw 提供了 score）
  5. 匹配 → 自动调用 bapi.favorite_video

v4.8 视频关键词过滤（video_filter）：
- should_process_video(item, config) → bool：是否应该处理这个视频
"""
from __future__ import annotations

from typing import Any


def should_favorite_by_keywords(title: str, desc: str = "",
                                up_name: str = "",
                                config: dict[str, Any] = None) -> tuple[bool, list[str]]:
    """检查视频是否匹配关键词。返回 (matched, matched_keywords).

    config: config["favorite"] 块。空 / 没 keywords → (False, [])
    """
    cfg = config or {}
    fav_cfg = cfg.get("favorite", {}) if "favorite" in cfg else cfg
    if not fav_cfg.get("enabled", True):
        return False, []
    keywords = fav_cfg.get("keywords") or []
    if not keywords:
        return False, []
    mode = fav_cfg.get("match_mode", "any")
    haystack = f"{title or ''}\n{up_name or ''}\n{desc or ''}"
    matched: list[str] = []
    for kw in keywords:
        if not kw:
            continue
        if kw in haystack:
            matched.append(kw)
    if mode == "all":
        ok = len(matched) == len(keywords) and len(matched) > 0
    else:  # "any"
        ok = len(matched) > 0
    return ok, matched


def should_favorite_by_score(score: float, config: dict[str, Any] = None) -> bool:
    """评分超过 archive_min 也自动收藏."""
    cfg = config or {}
    fav_cfg = cfg.get("favorite", {}) if "favorite" in cfg else cfg
    if not fav_cfg.get("auto_on_score", True):
        return False
    scoring = cfg.get("scoring", {}) if "scoring" in cfg else {}
    archive_min = float(scoring.get("archive_min", 7.5))
    return score >= archive_min


def should_process_video(title: str, up_name: str = "",
                         desc: str = "", config: dict = None) -> tuple[bool, str]:
    """v4.8 视频关键词过滤：判断是否要处理这个视频.

    如果 video_filter.enabled=False 或 include_keywords=[]，返回 (True, "filter_off").
    否则检查 title/up/desc 是否含 include_keywords 之一/全部（match_mode）。
    返回 (matched, reason): True 表示要处理，False 表示跳过。
    """
    cfg = config or {}
    vf = cfg.get("video_filter", {}) if "video_filter" in cfg else {}
    if not vf.get("enabled", False):
        return True, "filter_off"
    kws = vf.get("include_keywords") or []
    if not kws:
        return True, "no_keywords"
    mode = vf.get("match_mode", "any")
    haystack = f"{title or ''}\n{up_name or ''}\n{desc or ''}"
    matched = [kw for kw in kws if kw and kw in haystack]
    if mode == "all":
        ok = len(matched) == len(kws) and len(matched) > 0
    else:  # "any"
        ok = len(matched) > 0
    reason = f"matched={matched}" if ok else f"no_match (need {[k for k in kws if k]})"
    return ok, reason
