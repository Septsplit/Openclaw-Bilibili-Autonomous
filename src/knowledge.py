"""src/knowledge.py — v5.5 知识库系统 (归档自动分类).

Data/knowledge/<category>/<bvid>.md
  category 来自理解结果 tags 里第一个 (OpenClaw LLM 决定, skill 不知道具体分类)
  tags 里没有 category 就放 "其他"

OpenClaw 在 HEARTBEAT 看完视频后调:
  save_knowledge(bvid, title, up, body_md, tags=[...], score=N)

skill 自己不生成内容, 只管存储/索引/读取.

安全 (v5.7 Hermes 修复): frontmatter 用 yaml.safe_dump 转义,
body_md 截断 + 移除首个 "---" 分隔符防注入.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML — 用于安全序列化 frontmatter
except ImportError:
    yaml = None  # 缺失时降级到手工转义

from . import config as cfg


def knowledge_root() -> Path:
    return cfg.ACTIONS_DIR / "understandings"  # 沿用 v4 目录结构
    # v5.5 实际归档目录: Data/actions/highlights/<category>/*.md
    # 但 highlights 主要是 quality>=7.5 的高分内容
    # 知识库归档实际是 "全部 understand 过的" 视频
    # 所以用一个独立目录更清楚:
    # Data/knowledge/<category>/<bvid>.md


def knowledge_dir() -> Path:
    """实际存知识的目录."""
    p = cfg.DATA_DIR / "knowledge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(s: str, max_len: int = 60) -> str:
    """防注入 + 截断."""
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s or "")
    return s[:max_len].strip() or "untitled"


def _sanitize_body(body_md: str, max_len: int = 50000) -> str:
    """v5.7 Hermes 修复: 防 body_md 注入 frontmatter.

    1. 截断到 max_len 防 DoS
    2. 移除首个 "---" 行（无论前后空行）防 frontmatter 截断
    3. 移除控制字符 (除 \\n \\t) 防终端/解析器异常
    """
    if not body_md:
        return ""
    # 截断
    text = str(body_md)[:max_len]
    # 移除可能的 "---" 分隔符行（独立一行）
    text = re.sub(r"(?m)^\s*---\s*$", "(分隔符已过滤)", text)
    # 移除控制字符 (除 \n \t)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text


def _dump_frontmatter(meta: dict) -> str:
    """v5.7 Hermes 修复: 用 yaml.safe_dump 安全序列化 frontmatter.

    - PyYAML 默认会用 quoting/escaping 处理双引号/特殊字符
    - tags 强制转 list（safe_dump 对 None 友好）
    - 没 yaml 时降级到手工最小转义
    """
    if yaml is not None:
        try:
            return yaml.safe_dump(meta, allow_unicode=True,
                                  default_flow_style=False, sort_keys=False)
        except yaml.YAMLError:
            pass  # 降级
    # 手工 fallback（仍然比直接 f-string 安全）
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, str):
            # 双重转义反斜杠和双引号
            esc = v.replace("\\", "\\\\").replace('"', '\\"')
            esc = esc.replace("\n", " ").replace("\r", " ")
            lines.append(f'{k}: "{esc}"')
        elif isinstance(v, (list, tuple)):
            inner = ", ".join(
                f'"{str(x).replace(chr(34), chr(92) + chr(34))}"'
                for x in v
            )
            lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def save_knowledge(bvid: str, title: str, up: str, body_md: str,
                   tags: list[str] | None = None,
                   score: float | None = None) -> dict:
    """v5.5 关键入口: OpenClaw 看完视频后调, 把知识归档.

    Returns: {path, category}

    v5.7 安全加固:
    - title/up 限制长度
    - frontmatter 用 yaml.safe_dump
    - body_md 在第二个 --- 后单独一节，且先过滤可能的 --- 行
    """
    tags = tags or []
    # title/up 限制长度防 title "..."#hash" 攻击
    safe_title = _safe_name(title, max_len=80)
    safe_up = _safe_name(up, max_len=40)

    category = _safe_name(tags[0] if tags else "其他", max_len=20)
    cat_dir = knowledge_dir() / category
    cat_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{bvid}-{safe_title}.md"
    path = cat_dir / filename

    meta = {
        "ts": datetime.now().isoformat(),
        "bvid": str(bvid),
        "title": str(title),
        "up": str(up),
        "category": category,
        "tags": list(tags),
        "score": score,
    }

    fm_block = _dump_frontmatter(meta)
    safe_body = _sanitize_body(body_md)

    lines = [fm_block, "", f"# 《{safe_title}》 — by {safe_up}", "", safe_body]

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        return {"error": str(e)}
    return {"path": str(path.relative_to(cfg.SKILL_DIR)),
            "category": category, "bvid": bvid}


def list_categories() -> list[dict]:
    """列所有分类 + 文件数."""
    root = knowledge_dir()
    out = []
    if not root.exists():
        return out
    for cat in sorted(root.iterdir()):
        if not cat.is_dir():
            continue
        files = list(cat.glob("*.md"))
        if files:
            out.append({
                "category": cat.name,
                "count": len(files),
                "latest": max(f.stat().st_mtime for f in files),
            })
    out.sort(key=lambda x: -x["latest"])
    return out


def list_in_category(category: str, limit: int = 20) -> list[dict]:
    root = knowledge_dir() / _safe_name(category)
    if not root.exists():
        return []
    out = []
    for f in sorted(root.glob("*.md"), key=lambda x: -x.stat().st_mtime)[:limit]:
        # 解析 frontmatter 提 title/up
        title, up, score = "", "", None
        try:
            text = f.read_text(encoding="utf-8")
            if text.startswith("---"):
                fm_end = text.find("---", 3)
                if fm_end > 0:
                    for line in text[3:fm_end].splitlines():
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("up:"):
                            up = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("score:"):
                            try:
                                score = float(line.split(":", 1)[1].strip())
                            except (ValueError, IndexError):
                                pass
        except (OSError, UnicodeDecodeError):
            pass
        out.append({
            "filename": f.name,
            "title": title,
            "up": up,
            "score": score,
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return out


def read_article(category: str, filename: str) -> str | None:
    path = knowledge_dir() / _safe_name(category) / _safe_name(filename)
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
