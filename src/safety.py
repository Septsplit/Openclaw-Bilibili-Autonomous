"""src/safety.py — 敏感词 / 内容安全过滤.

精简自原 bilibili_agent/services/reply_safety.py（98 行 → 60 行）：
- 保留 should_block / is_video_comment_safe
- 去掉 reply_safety.py 里管理来信/回信双向审查的 review / find_hits（v1 不做私信回复）
- 直接消费 src/config.py 加载的 reply_safety 配置（已通过 DEFAULTS 兜底）

v1.1（2026-07-04 审核修复）：
- 移除了硬编码的 POLITICAL_VIDEO_KEYWORDS（之前在源码里直接写"台湾"等政治词）
- 现在从 config.reply_safety.political_video_keywords 读
- 关键词可以在 Data/config.json 里改，不用动源码
"""
from __future__ import annotations

import re
import sys
from typing import Any


class ReplySafetyGuard:
    """评论/弹幕/收藏前的内容安全审查。"""

    def __init__(self, cfg: dict[str, Any] | None = None):
        cfg = cfg or {}
        safety = cfg.get("reply_safety", {})
        self.enabled: bool = bool(safety.get("enabled", True))
        self.block_on_outgoing: bool = bool(safety.get("block_on_outgoing", True))
        self.blocked_keywords: list[str] = list(safety.get("blocked_keywords", []))
        # 评论文字用的正则
        self._regex = self._build_regex(self.blocked_keywords)
        # 视频内容审查用的政治类关键词（从 config 读，不再硬编码）
        self.political_video_keywords: tuple[str, ...] = tuple(
            safety.get("political_video_keywords") or []
        )

    @staticmethod
    def _build_regex(keywords: list[str]) -> re.Pattern:
        if not keywords:
            # never-match
            return re.compile(r"(?!x)x")
        pattern = "|".join(re.escape(kw) for kw in keywords)
        return re.compile(pattern, re.IGNORECASE)

    def should_block(self, text: str) -> bool:
        """对要发送的评论/弹幕文字做敏感词检查。命中即 True（拦截）。"""
        if not self.enabled or not text:
            return False
        return bool(self._regex.search(text))

    def is_video_comment_safe(self, title: str, desc: str = "") -> bool:
        """视频本身是否适合被评论/互动（防涉政）。

        关键词从 config.reply_safety.political_video_keywords 读（默认空 → 全部放行）。
        """
        if not self.political_video_keywords:
            return True
        combined = f"{title or ''} {desc or ''}"
        for kw in self.political_video_keywords:
            if kw in combined:
                return False
        return True

    def reload(self, cfg: dict[str, Any]) -> None:
        """热重载关键词（如果 OpenClaw 通过别的方式改了 config.json）。"""
        self.__init__(cfg)
