"""src/archive.py — v2.5 高分内容归档到 Data/actions/highlights/<category>/.

流程：
1. score_video() 得到 {score, reason, tags}
2. 如果 score >= archive_min：
   - category = tags[0] 或 "其他"
   - 复制 understanding markdown 到 Data/actions/highlights/<category>/
   - 追加到 highlight_index.json
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import actions_log, config as cfg


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_highlight_index() -> list[dict]:
    if not cfg.HIGHLIGHT_INDEX_FILE.exists():
        return []
    try:
        with open(cfg.HIGHLIGHT_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_highlight_index(items: list[dict]) -> bool:
    try:
        with open(cfg.HIGHLIGHT_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        print(f"[WARN] save_highlight_index 失败: {e}", file=sys.stderr)
        return False


def archive_highlight(bvid: str, title: str, up: str, score: float,
                      reason: str, tags: list[str], source_md_path: Path | None = None
                      ) -> Path | None:
    """归档一个高分视频.

    Args:
        source_md_path: 原 understanding markdown 路径（如果是 None 就直接构造）

    Returns:
        新归档文件的 Path 或 None（分数不够/不安全/失败）。
    """
    app_cfg = cfg.load_app_config()
    threshold = app_cfg.get("scoring", {}).get("archive_min", 7.5)
    if score < threshold:
        return None

    # 安全检查：涉政视频不归档（与 reply_safety 一致）
    safety_cfg = app_cfg.get("reply_safety", {})
    political = safety_cfg.get("political_video_keywords") or []
    if political and any(kw in title for kw in political):
        return None
    if political and any(kw in up for kw in political):
        return None

    cfg.ensure_dirs()

    # 选 category（第一个 tag，没有就 "其他"）
    safe_tags = [t for t in tags if t and isinstance(t, str)]
    category = cfg.safe_filename(safe_tags[0] if safe_tags else "其他", max_len=20)

    target_dir = cfg.ACTIONS_DIR / "highlights" / category
    target_dir.mkdir(parents=True, exist_ok=True)

    score_str = f"{score:.1f}".replace(".", "_")
    filename = f"{_today()}_{bvid}-{cfg.safe_filename(title, max_len=40)}-S{score_str}"
    target_path = target_dir / f"{filename}.md"

    if source_md_path and source_md_path.exists():
        try:
            shutil.copy2(source_md_path, target_path)
        except OSError as e:
            print(f"[WARN] archive_highlight copy 失败: {e}", file=sys.stderr)
            return None
    else:
        # 没有源 md，构造一个最小的
        body = (
            f"---\nts: {datetime.now().isoformat()}\n"
            f"bvid: {bvid}\ntitle: \"{title}\"\nup: \"{up}\"\n"
            f"action: highlight\nscore: {score:.1f}\ntags: {json.dumps(tags, ensure_ascii=False)}\n---\n\n"
            f"# {title}\n\n- UP: {up}\n- 评分: {score:.1f}\n- 理由: {reason}\n"
        )
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(body)
        except OSError as e:
            print(f"[WARN] archive_highlight write 失败: {e}", file=sys.stderr)
            return None

    # 更新索引
    index = load_highlight_index()
    index.append({
        "ts": datetime.now().isoformat(),
        "bvid": bvid,
        "title": title,
        "up": up,
        "score": round(score, 1),
        "reason": reason[:200],
        "tags": tags,
        "category": category,
        "path": str(target_path.relative_to(cfg.SKILL_DIR)),
    })
    save_highlight_index(index)
    actions_log.append_operation_log({
        "action": "archive",
        "bvid": bvid,
        "title": title,
        "score": round(score, 1),
        "category": category,
        "path": str(target_path.relative_to(cfg.SKILL_DIR)),
    })
    return target_path


def list_highlights(category: str | None = None, limit: int = 100) -> list[dict]:
    """列出归档."""
    base = cfg.ACTIONS_DIR / "highlights"
    if not base.exists():
        return []
    out = []
    cats = [category] if category else sorted([d.name for d in base.iterdir() if d.is_dir()])
    for c in cats:
        cdir = base / c
        if not cdir.exists():
            continue
        for f in sorted(cdir.glob("*.md"), reverse=True)[:limit]:
            stat = f.stat()
            out.append({
                "category": c,
                "filename": f.name,
                "path": str(f.relative_to(cfg.SKILL_DIR)),
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return out