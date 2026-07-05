"""src/scorer.py — v3 阈值门控（不计算 score）.

v3 重构：score 由 OpenClaw 自己计算（LLM/规则都行）。
本 skill 只暴露**阈值检查**：给一个 score，问"该不该 coin / favorite / archive"。

OpenClaw 调用模式：
    from src.scorer import Thresholds, gate
    g = Thresholds.from_config()
    if g.should_coin(score=8.5):
        # 调用 src.main 的原子动作 ...
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Thresholds:
    """多阈值门控配置（从 config.py:DEFAULTS['scoring'] 读）."""
    coin: float = 8.0
    favorite: float = 8.5
    comment: float = 6.5
    follow: float = 7.0
    archive: float = 7.5
    understand: float = 6.0
    # follow_exceptional 是 follow 决策的豁免阈值
    follow_exceptional: float = 8.5
    follow_min_impressions: int = 2

    @classmethod
    def from_config(cls) -> "Thresholds":
        """从 config.json:scoring 读阈值."""
        from . import config as cfg
        s = cfg.load_app_config().get("scoring", {})
        return cls(
            coin=float(s.get("coin_min", 8.0)),
            favorite=float(s.get("favorite_min", 8.5)),
            comment=float(s.get("comment_min", 6.5)),
            follow=float(s.get("follow_min", 7.0)),
            archive=float(s.get("archive_min", 7.5)),
            understand=float(s.get("understand_min", 6.0)),
            follow_exceptional=float(s.get("follow_exceptional", 8.5)),
            follow_min_impressions=int(s.get("follow_min_impressions", 2)),
        )


def gate(score: float, action: str, thresholds: Thresholds) -> bool:
    """统一阈值检查入口.

    Actions: "coin" | "favorite" | "comment" | "follow" | "archive" | "understand"
    Returns: True 表示分数通过阈值，可以执行该动作。
    """
    if action == "coin":
        return score >= thresholds.coin
    if action == "favorite":
        return score >= thresholds.favorite
    if action == "comment":
        return score >= thresholds.comment
    if action == "follow":
        return score >= thresholds.follow
    if action == "archive":
        return score >= thresholds.archive
    if action == "understand":
        return score >= thresholds.understand
    return False


# 兼容 v2 的接口（Scorer 类的旧 API 仍可用，避免破坏已部署的脚本）
class Scorer:
    """v2 兼容层：保留旧 API，但 score() 方法删除（OpenClaw 自己算）.

    仅保留阈值门控方法。
    """
    def __init__(self, thresholds: Thresholds | None = None):
        self.thresholds = thresholds or Thresholds.from_config()

    # v2 的 score() / ask_for_score() 方法已删除
    # OpenClaw 自己计算 score 后调用下面的 should_* 方法

    def should_coin(self, score: float) -> bool:
        return self.thresholds.coin <= score

    def should_favorite(self, score: float) -> bool:
        return self.thresholds.favorite <= score

    def should_comment(self, score: float) -> bool:
        return self.thresholds.comment <= score

    def should_follow(self, score: float, impressions: int = 0) -> bool:
        if score < self.thresholds.follow:
            return False
        if score >= self.thresholds.follow_exceptional:
            return True
        return impressions >= self.thresholds.follow_min_impressions

    def should_archive(self, score: float) -> bool:
        return self.thresholds.archive <= score

    def should_understand(self, score: float) -> bool:
        return self.thresholds.understand <= score
