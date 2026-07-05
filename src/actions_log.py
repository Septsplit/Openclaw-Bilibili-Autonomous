"""src/actions_log.py — v2.3 操作日志和动作内容写入.

两个职责：
1. append_operation_log(entry) — 追加一行 JSONL 到 Data/logs/operations-YYYY-MM-DD.jsonl
2. write_action_markdown(category, filename, frontmatter, content) — 写 markdown 到 Data/actions/<category>/YYYY-MM-DD/<file>.md

所有写都 best-effort，失败 stderr 打 warning（v1 append_log 模式）。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def append_operation_log(entry: dict, log_type: str = "operations") -> None:
    """追加一行 JSONL 到 Data/logs/<log_type>-YYYY-MM-DD.jsonl.

    log_type: "operations" | "llm_costs"
    """
    cfg.ensure_dirs()
    path = cfg.LOGS_DIR / f"{log_type}-{_today_str()}.jsonl"
    entry = dict(entry)
    entry.setdefault("ts", datetime.now().isoformat())
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[WARN] append_operation_log({log_type}) 失败: {e}",
              file=sys.stderr, flush=True)


def append_llm_cost(model: str, prompt_tokens: int, completion_tokens: int,
                    purpose: str, cost_cny: float = 0.0) -> None:
    """记录 LLM 调用成本。"""
    append_operation_log({
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "purpose": purpose,
        "cost_cny": cost_cny,
    }, log_type="llm_costs")


def write_action_markdown(category: str, filename: str,
                          frontmatter: dict[str, Any], body: str) -> Path:
    """写一个 markdown 到 Data/actions/<category>/YYYY-MM-DD/<filename>.md.

    Returns the path. Best-effort: 失败返回 Path（不抛）。
    """
    cfg.ensure_dirs()
    day_dir = cfg.ACTIONS_DIR / category / _today_str()
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{cfg.safe_filename(filename, max_len=80)}.md"

    # 构造 frontmatter（YAML 风格）
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, str) and any(c in v for c in [':', '#', '\n', '"']):
            fm_lines.append(f'{k}: "{v.replace(chr(34), chr(92)+chr(34))}"')
        elif isinstance(v, (int, float, bool)):
            fm_lines.append(f"{k}: {v}")
        elif v is None:
            fm_lines.append(f"{k}: null")
        else:
            fm_lines.append(f"{k}: {v}")
    fm_lines.append("---\n")

    text = "\n".join(fm_lines) + (body or "").rstrip() + "\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        print(f"[WARN] write_action_markdown({category}/{filename}) 失败: {e}",
              file=sys.stderr, flush=True)
    return path


def list_action_files(category: str, date: str | None = None,
                      limit: int = 50) -> list[dict]:
    """列出某 category 下某天（或全部）的 markdown 文件元信息."""
    base = cfg.ACTIONS_DIR / category
    if not base.exists():
        return []
    days = [date] if date else sorted([d.name for d in base.iterdir() if d.is_dir()],
                                      reverse=True)
    out = []
    for d in days:
        day_dir = base / d
        if not day_dir.exists():
            continue
        for f in sorted(day_dir.glob("*.md"), reverse=True)[:limit]:
            stat = f.stat()
            out.append({
                "category": category,
                "date": d,
                "filename": f.name,
                "path": str(f.relative_to(cfg.SKILL_DIR)),
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return out


def tail_operations(lines: int = 100, date: str | None = None) -> list[dict]:
    """读最新 N 行 operations JSONL（默认今天）。"""
    path = cfg.LOGS_DIR / f"operations-{(date or _today_str())}.jsonl"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return [json.loads(line) for line in tail if line.strip()]
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] tail_operations 失败: {e}", file=sys.stderr)
        return []