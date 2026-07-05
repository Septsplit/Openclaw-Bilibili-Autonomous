"""src/main.py — v3 原子 CLI 工具（OpenClaw AI 调用入口）.

v3 重构：本 skill 是 B 站 API 工具集，**不**生成内容。
AI 决策由 OpenClaw 完成，OpenClaw 调这里执行原子动作。

子命令分类：

📌 **写操作 — B 站 API 原子动作**（OpenClaw 直接调）
  like <bvid>                点赞
  coin <bvid> [--num N]      投币
  favorite <bvid> [--fid N]  收藏
  comment <bvid> --text X    发评论（text 由 OpenClaw 决定）
  danmaku <bvid> --text X    发弹幕
  follow <uid> [--name X]    关注 UP
  unfollow <uid> [--name X]  取关 UP
  dm send <uid> --text X     主动发私信
  dm check --reply-cmd CMD   检查私信（OpenClaw 提供 reply 命令）

📌 **读操作 — 工具查询**（OpenClaw 用来 pre-check）
  feed                        首页推荐流
  video <bvid>                视频元信息
  subtitles <bvid>            B站原生字幕
  user <uid>                  用户信息
  gate <score> <action>       阈值门控检查

📌 **阈值门控 — 工具**
  thresholds                   读所有阈值
  gate <score> <action>       单一阈值检查

📌 **视频理解工具**（OpenClaw 主动调）
  understand <bvid> [--mode subtitle|whisper]  工具调用

📌 **管理**
  status                       综合状态
  actions [list|get X]        操作记录查看
  tools-log [--tail N]         工具调用历史
  configure                    CLI 配置器
  serve [--port N]             Web 面板
  openapi                      OpenAPI 文档 JSON

所有写操作通过 src.throttle.call 走限流重试；
通过 src.safety 做内容安全过滤。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from . import actions_log, bapi, config as cfg, safety as safety_mod, state_view
from .scorer import Thresholds, gate


def _err(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def _ok(summary: dict) -> dict:
    """统一收尾：stdout 输出 JSON 摘要（OpenClaw 解析这一行）。"""
    print("---SUMMARY-JSON---")
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return summary


def _make_bapi(dry_run: bool) -> bapi.BiliAPI:
    cookies = cfg.load_cookies()
    if not cookies.get("SESSDATA"):
        raise SystemExit("[ERROR] 缺少 SESSDATA, 请用原 bilibili_agent 登录后重启")
    return bapi.BiliAPI(bapi.make_credential(cookies), dry_run=dry_run)


def _make_safety() -> safety_mod.ReplySafetyGuard:
    return safety_mod.ReplySafetyGuard(cfg.load_app_config())


def _build_dm_item(bv: bapi.BiliAPI, bvid: str):
    """返回 coroutine，调用方 await. v4.1 修：避免嵌套 asyncio.run."""
    async def _run():
        meta = await bv.get_video_full_meta(bvid)
        item = bapi.RecommendItem(
            bvid=bvid, aid=meta["aid"], title=meta["title"],
            up=meta["up_name"], duration=meta["duration"], desc=meta.get("desc", ""),
        )
        item.cid = meta.get("cid", 0)
        return item
    return _run()


# ===== 原子写操作 =====

async def cmd_like(args):
    """点 like."""
    bv = _make_bapi(args.dry_run)
    item = await _build_dm_item(bv, args.bvid)
    ok = await bv.like_video(item)
    actions_log.append_operation_log({"action": "like", "bvid": args.bvid, "ok": ok})
    return _ok({"action": "like", "bvid": args.bvid, "ok": ok, "dry_run": args.dry_run})


async def cmd_coin(args):
    """投 coin."""
    bv = _make_bapi(args.dry_run)
    item = await _build_dm_item(bv, args.bvid)
    ok = await bv.coin_video(item, num=args.num)
    actions_log.append_operation_log({
        "action": "coin", "bvid": args.bvid, "num": args.num, "ok": ok,
    })
    return _ok({"action": "coin", "bvid": args.bvid, "num": args.num,
               "ok": ok, "dry_run": args.dry_run})


async def cmd_favorite(args):
    """收藏.

    v4.2 新增 --auto-check: 自动按关键词 / 分数匹配决定是否真收藏。
    OpenClaw 推荐用法: favorite <bvid> --auto-check --score X
    """
    bv = _make_bapi(args.dry_run)
    item = await _build_dm_item(bv, args.bvid)
    favorite_cfg = cfg.load_app_config().get("favorite", {})
    # v4.9 收藏总开关
    if not favorite_cfg.get("enabled", True):
        return _ok({"action": "favorite", "bvid": args.bvid, "ok": False,
                   "skipped": True, "reason": "favorite disabled (总开关 off)"})

    matched_kws = []
    matched_reason = "manual"

    # 关键词匹配（v4.9 加 keyword_enabled 开关）
    keyword_on = favorite_cfg.get("keyword_enabled", True)
    has_keywords = bool(favorite_cfg.get("keywords"))
    if args.auto_check and keyword_on and has_keywords:
        from .favorite_keys import should_favorite_by_keywords, should_favorite_by_score
        ok, matched_kws = should_favorite_by_keywords(
            title=item.title, desc=item.desc, up_name=item.up,
            config={"favorite": favorite_cfg},
        )
        if matched_kws:
            matched_reason = f"keywords={matched_kws}"
        elif args.score is not None:
            if should_favorite_by_score(args.score, config={"favorite": favorite_cfg, **cfg.load_app_config()}):
                matched_reason = f"score={args.score}>=archive_min"
                ok = True
            else:
                matched_reason = f"score={args.score}<archive_min (skip)"
                # OpenClaw 给了 score 但没达阈值，跳过
                actions_log.append_operation_log({
                    "action": "favorite_skipped", "bvid": args.bvid,
                    "score": args.score, "reason": "score_below_threshold",
                })
                return _ok({"action": "favorite", "bvid": args.bvid, "ok": False,
                           "skipped": True, "reason": matched_reason})
        elif not args.auto_check:
            # 用户明确指定收藏，直接执行
            matched_reason = "explicit"
            ok = True
    else:
        ok = True

    if not ok and not args.auto_check:
        # fallback: manual 调用应该总是真收藏
        ok = True

    result_ok = await bv.favorite_video(item, fid=args.fid)
    actions_log.append_operation_log({
        "action": "favorite", "bvid": args.bvid, "fid": args.fid,
        "ok": result_ok, "reason": matched_reason,
        "matched_keywords": matched_kws,
    })
    return _ok({"action": "favorite", "bvid": args.bvid, "fid": args.fid,
               "ok": result_ok, "reason": matched_reason,
               "matched_keywords": matched_kws,
               "dry_run": args.dry_run})


async def cmd_comment(args):
    """发评论（text 由 OpenClaw 传入）."""
    if not args.text:
        _err("--text 必填（OpenClaw 提供）")
        return 2
    bv = _make_bapi(args.dry_run)
    item = await _build_dm_item(bv, args.bvid)
    safe = _make_safety()
    text = args.text
    if safe.should_block(text):
        msg = f"内容命中敏感词，已拦截: {text[:30]}..."
        _err(msg)
        actions_log.append_operation_log({
            "action": "comment_blocked", "bvid": args.bvid, "reason": "safety",
        })
        return _ok({"action": "comment", "bvid": args.bvid, "ok": False,
                   "reason": "blocked_by_safety"})
    ok = await bv.send_comment(item, text)
    actions_log.write_action_markdown(
        category="comments",
        filename=f"{args.bvid}-{int(asyncio.get_event_loop().time()*1000)}",
        frontmatter={"ts": cfg._today() if hasattr(cfg, "_today") else None,
                     "bvid": args.bvid, "title": item.title,
                     "action": "comment_sent", "ok": ok},
        body=f"# 评论: 《{item.title}》\n\n**内容**:\n{text}\n",
    )
    actions_log.append_operation_log({
        "action": "comment", "bvid": args.bvid, "ok": ok,
    })
    return _ok({"action": "comment", "bvid": args.bvid, "ok": ok, "dry_run": args.dry_run})


async def cmd_danmaku(args):
    """发弹幕（text 由 OpenClaw 传入）."""
    if not args.text:
        _err("--text 必填")
        return 2
    bv = _make_bapi(args.dry_run)
    item = await _build_dm_item(bv, args.bvid)
    safe = _make_safety()
    text = args.text[:20]  # 弹幕 ≤20 字
    if safe.should_block(text):
        actions_log.append_operation_log({
            "action": "danmaku_blocked", "bvid": args.bvid, "reason": "safety",
        })
        return _ok({"action": "danmaku", "bvid": args.bvid, "ok": False,
                   "reason": "blocked_by_safety"})
    ok = await bv.send_danmaku(item, text)
    actions_log.write_action_markdown(
        category="danmaku",
        filename=f"{args.bvid}-{int(asyncio.get_event_loop().time()*1000)}",
        frontmatter={"ts": cfg._today() if hasattr(cfg, "_today") else None,
                     "bvid": args.bvid, "title": item.title,
                     "action": "danmaku_sent", "ok": ok},
        body=f"# 弹幕: 《{item.title}》\n\n**内容**:\n{text}\n",
    )
    actions_log.append_operation_log({
        "action": "danmaku", "bvid": args.bvid, "ok": ok, "text": text,
    })
    return _ok({"action": "danmaku", "bvid": args.bvid, "ok": ok, "dry_run": args.dry_run})


async def cmd_follow(args):
    """关注 UP.

    v5.8 Hermes-2 修：之前 st["history"].append(...) 直接 KeyError
    （follow_state.json 没 history 字段或被外部清空）。
    改用 setdefault + try/except 防御，避免崩溃。
    """
    bv = _make_bapi(args.dry_run)
    ok = await bv.follow_user(int(args.uid))
    if ok:
        import json as _json
        from pathlib import Path as _P
        from datetime import datetime as _dt
        fp = cfg.FOLLOW_STATE_FILE
        st: dict = {}
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    st = _json.load(f) or {}
            except (OSError, _json.JSONDecodeError):
                st = {}
        # 防御性 setdefault —— 缺哪个字段补哪个
        # v5.8 Hermes-2 修：原 _dt.date.today() 错的 — _dt 是 datetime 类，
        # _dt.date 是绑定方法（method_descriptor），没有 today 类方法。
        # 正确: _dt.today().date() 拿今天日期
        st.setdefault("today", str(_dt.today().date()))
        st.setdefault("daily_count", 0)
        st.setdefault("cooldowns", {})
        st.setdefault("history", [])
        st["history"].append({
            "ts": _dt.now().isoformat(),
            "uid": int(args.uid), "name": args.name or "",
            "action": "follow", "reason": "v3 cli",
        })
        st["cooldowns"][str(args.uid)] = _dt.now().isoformat()
        try:
            tmp = fp.with_suffix(fp.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(st, f, ensure_ascii=False, indent=2)
                f.flush()
                import os
                os.fsync(f.fileno())
            os.replace(tmp, fp)
        except OSError as e:
            print(f"[WARN] cmd_follow save state failed: {e}", file=sys.stderr)
    actions_log.append_operation_log({
        "action": "follow", "uid": int(args.uid), "name": args.name, "ok": ok,
    })
    return _ok({"action": "follow", "uid": int(args.uid), "ok": ok, "dry_run": args.dry_run})


async def cmd_unfollow(args):
    """取消关注."""
    bv = _make_bapi(args.dry_run)
    ok = await bv.unfollow_user(int(args.uid))
    actions_log.append_operation_log({
        "action": "unfollow", "uid": int(args.uid), "ok": ok,
    })
    return _ok({"action": "unfollow", "uid": int(args.uid), "ok": ok, "dry_run": args.dry_run})


async def cmd_dm_send(args):
    """主动发私信."""
    if not args.text:
        _err("--text 必填")
        return 2
    safe = _make_safety()
    if safe.should_block(args.text):
        return _ok({"action": "dm_send", "uid": int(args.uid), "ok": False,
                   "reason": "blocked_by_safety"})
    bv = _make_bapi(args.dry_run)
    ok = await bv.send_private_message(int(args.uid), args.text)
    actions_log.append_operation_log({
        "action": "dm_send", "uid": int(args.uid), "ok": ok,
    })
    return _ok({"action": "dm_send", "uid": int(args.uid), "ok": ok, "dry_run": args.dry_run})


async def cmd_dm_check(args):
    """检查 + OpenClaw 提供回复.

    OpenClaw 调模式：
      bin/bilibili-autonomous dm check --reply-cmd "my_dm_reply.sh"

    skill 拉新私信，对每条消息调用 reply-cmd 命令传入 stdin：
      {"uid":"123","name":"foo","text":"hi","context":"..."}
    命令 stdout 必须输出一个 JSON：{"reply": "..."} 或 {"reply": ""}
    """
    import subprocess as _sp
    import json as _json
    if not args.reply_cmd:
        _err("需要 --reply-cmd 指定 OpenClaw 调用命令")
        return 2

    from .dm import DMService
    bv = _make_bapi(args.dry_run)
    safe = _make_safety()
    svc = DMService(bapi=bv, safety=safe)

    async def reply_provider(uid, name, text, context):
        proc = _sp.run(
            args.reply_cmd,
            input=_json.dumps({"uid": uid, "name": name, "text": text, "context": context}),
            capture_output=True, text=True, timeout=60, shell=True,
        )
        out = proc.stdout.strip()
        try:
            data = _json.loads(out)
            return (data.get("reply") or "").strip() or None
        except Exception:
            return None

    return _ok(await svc.check_and_reply(dry_run=args.dry_run,
                                          reply_provider=reply_provider))


# ===== 读 / 工具查询 =====

async def cmd_feed(args):
    """首页推荐流（OpenClaw 读后挑要刷哪些）."""
    bv = _make_bapi(args.dry_run)
    items = await bv.get_recommend_feed(limit=args.limit)
    out = [{
        "bvid": it.bvid, "aid": it.aid, "title": it.title,
        "up": it.up, "duration": it.duration, "cid": it.cid,
    } for it in items]
    return _ok({"count": len(out), "items": out})


async def cmd_video(args):
    """视频元信息."""
    bv = _make_bapi(args.dry_run)
    meta = await bv.get_video_full_meta(args.bvid)
    return _ok({"bvid": args.bvid, **meta})


async def cmd_subtitles(args):
    """B 站原生字幕."""
    bv = _make_bapi(args.dry_run)
    subs = await bv.get_video_subtitles(args.bvid)
    return _ok({"bvid": args.bvid, "count": len(subs),
               "subtitles": [{"lan": s.get("lan"), "lan_doc": s.get("lan_doc"),
                              "len": len(s.get("content", ""))}
                              for s in subs]})


async def cmd_user(args):
    """用户信息."""
    bv = _make_bapi(args.dry_run)
    info = await bv.get_user_profile(int(args.uid))
    return _ok({"uid": int(args.uid), **info})


# ===== 阈值门控（OpenClaw 自己算 score 后调用）=====

def cmd_gate(args):
    """单一阈值检查. 用法: gate <score> <action>
    action: coin|favorite|comment|follow|archive|understand"""
    t = Thresholds.from_config()
    passed = gate(args.score, args.action, t)
    return _ok({"score": args.score, "action": args.action,
               "threshold": getattr(t, args.action, None),
               "passed": passed})


def cmd_thresholds(args):
    """列出所有阈值."""
    t = Thresholds.from_config()
    return _ok({
        "coin_min": t.coin, "favorite_min": t.favorite,
        "comment_min": t.comment, "follow_min": t.follow,
        "archive_min": t.archive, "understand_min": t.understand,
        "follow_exceptional_min": t.follow_exceptional,
        "follow_min_impressions": t.follow_min_impressions,
    })


# ===== 视频理解工具 =====

async def cmd_understand(args):
    """视频理解工具调用.

    --mode subtitle：返回 B 站原生字幕（按优先级选）
    --mode whisper：本地 whisper 转写
    返回给 OpenClaw，让它自己总结/评分。
    """
    from . import understand as understand_mod
    bv = _make_bapi(args.dry_run)
    out: dict = {"bvid": args.bvid, "mode": args.mode}

    if args.mode in ("subtitle", "auto"):
        try:
            subs = await bv.get_video_subtitles(args.bvid)
            chosen = understand_mod.pick_subtitle(
                subs,
                cfg.load_app_config().get("video_understanding", {}).get(
                    "subtitle_priority", ["ai-zh", "zh-CN", "zh-Hans", "en"])
            )
            out["subtitle"] = {
                "available": bool(chosen),
                "lan": chosen.get("lan") if chosen else None,
                "len": len(chosen.get("content", "")) if chosen else 0,
                "content": chosen.get("content", "") if chosen else "",
            }
        except Exception as e:
            out["subtitle"] = {"available": False, "error": str(e)}

    if args.mode == "whisper" or (args.mode == "auto" and not out.get("subtitle", {}).get("content")):
        try:
            text = await understand_mod.whisper_transcribe(
                args.bvid,
                cfg.load_app_config().get("video_understanding", {}).get(
                    "whisper_model", "base"),
            )
            out["whisper"] = {"available": bool(text), "len": len(text or ""),
                               "text": text or ""}
        except Exception as e:
            out["whisper"] = {"available": False, "error": str(e)}

    return _ok(out)


# ===== 关注管理 =====

async def cmd_follow_status(args):
    from .follow import FollowEngine
    fe = FollowEngine()
    return _ok(fe.status())


async def cmd_follow_history(args):
    from .follow import FollowEngine
    fe = FollowEngine()
    return _ok({"history": fe.history(limit=args.limit)})


async def cmd_follow_inactive_scan(args):
    """扫描未活跃 UP（v2.2 修复的 scan_inactive）."""
    from .follow import FollowEngine
    fe = FollowEngine()
    result = fe.scan_inactive(dry_run=not args.apply)
    return _ok(result)


# ===== 管理 / 状态 =====

def cmd_status(args):
    state_view.print_full_status()
    return _ok(state_view.get_full_status())


def cmd_actions_list(args):
    """列动作记录."""
    import os
    base = cfg.ACTIONS_DIR
    categories = ["comments", "danmaku", "dms", "follows",
                  "understandings", "highlights"]
    out = []
    if args.category:
        # 列出某 category 下所有 .md
        cat_dir = base / args.category
        if cat_dir.exists():
            for p in sorted(cat_dir.rglob("*.md")):
                out.append({
                    "category": args.category,
                    "path": str(p.relative_to(cfg.SKILL_DIR)),
                    "size": p.stat().st_size,
                    "mtime": __import__("datetime").datetime.fromtimestamp(
                        p.stat().st_mtime).isoformat(),
                })
    else:
        for cat in categories:
            d = base / cat
            if d.exists():
                files = list(d.rglob("*.md"))
                out.append({"category": cat, "count": len(files)})
    return _ok({"items": out})


def cmd_actions_get(args):
    """读一个动作记录的 markdown."""
    p = Path(args.path)
    if not p.is_absolute():
        p = cfg.SKILL_DIR / p
    if not p.exists():
        _err(f"文件不存在: {p}")
        return 2
    print(p.read_text(encoding="utf-8"))
    return _ok({"path": str(p), "read": True})


def cmd_tools_log(args):
    """工具调用历史（OpenClaw 调试用）."""
    log_file = cfg.DATA_DIR / "logs" / "operations-2026-07-04.jsonl"
    # 找最新一天
    log_dir = cfg.DATA_DIR / "logs"
    files = sorted(log_dir.glob("operations-*.jsonl")) if log_dir.exists() else []
    if files:
        log_file = files[-1]
    if not log_file.exists():
        return _ok({"entries": [], "file": None})
    lines = log_file.read_text(encoding="utf-8").splitlines()
    tail = lines[-args.tail:] if args.tail < len(lines) else lines
    entries = []
    for ln in tail:
        try:
            entries.append(json.loads(ln))
        except Exception:
            pass
    return _ok({"file": str(log_file.relative_to(cfg.SKILL_DIR)),
               "count": len(entries), "entries": entries})


def cmd_configure(args):
    from . import cli_config
    cli_config.run()
    return _ok({"mode": "cli_configure"})


def cmd_serve(args):
    from . import web_panel
    settings = cfg.load_web_settings()
    host = args.bind or settings.get("bind", "127.0.0.1")
    port = args.port or int(settings.get("port", 8765))
    print(f"[INFO] bilibili-autonomous Web at http://{host}:{port}/")
    web_panel.run_server(host=host, port=port, debug=False)
    return _ok({"host": host, "port": port})


def cmd_openapi(args):
    """v3.7: 输出 skill 暴露的所有工具的 OpenAPI 3 描述."""
    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "bilibili-autonomous",
            "version": "3.0.0",
            "description": "B 站 API 工具集. AI 由调用方（OpenClaw）负责.",
        },
        "servers": [{"url": "http://127.0.0.1:8765", "description": "本地 Web (tool history)"}],
        "paths": {},
    }
    # 描述每个 CLI 子命令
    cmds = [
        ("like", "POST /like", {"bvid": "string"}),
        ("coin", "POST /coin", {"bvid": "string", "num": "int (default 1)"}),
        ("favorite", "POST /favorite", {"bvid": "string", "fid": "int (default 1)"}),
        ("comment", "POST /comment", {"bvid": "string", "text": "string"}),
        ("danmaku", "POST /danmaku", {"bvid": "string", "text": "string"}),
        ("follow", "POST /follow", {"uid": "int", "name": "string"}),
        ("unfollow", "POST /unfollow", {"uid": "int"}),
        ("dm.send", "POST /dm/send", {"uid": "int", "text": "string"}),
        ("dm.check", "POST /dm/check", {"reply_cmd": "shell command for OpenClaw"}),
        ("feed", "GET /feed", {"limit": "int"}),
        ("video", "GET /video/{bvid}", {"bvid": "string"}),
        ("subtitles", "GET /subtitles/{bvid}", {"bvid": "string"}),
        ("user", "GET /user/{uid}", {"uid": "int"}),
        ("gate", "GET /gate", {"score": "float", "action": "string"}),
        ("thresholds", "GET /thresholds", {}),
        ("understand", "POST /understand", {"bvid": "string", "mode": "string"}),
        ("follow.status", "GET /follow/status", {}),
        ("follow.history", "GET /follow/history", {"limit": "int"}),
        ("follow.inactive_scan", "POST /follow/inactive_scan", {"apply": "bool"}),
        ("status", "GET /status", {}),
        ("actions.list", "GET /actions", {"category": "string"}),
        ("actions.get", "GET /actions/file", {"path": "string"}),
        ("tools_log", "GET /tools_log", {"tail": "int"}),
        ("openapi", "GET /openapi.json", {}),
    ]
    for name, path, params in cmds:
        if name == "openapi":
            continue
        verb = "get" if "/GET" in path else "post" if "/POST" in path else "get"
        method = verb
        path_clean = path.split(" ", 1)[1]
        p_obj = {"summary": name, "description": f"对应 CLI: bilibili-autonomous {name}",
                 "responses": {"200": {"description": "ok"}}}
        # 简化参数描述（实际 OpenClaw 用 CLI，不需要严格 schema）
        spec["paths"][path_clean] = {method: p_obj}
    print(json.dumps(spec, ensure_ascii=False, indent=2))
    return _ok(spec)



def log(msg: str, level: str = "INFO") -> None:
    """v4.6 临时补：v3 重写后丢了 log helper，watch 需要."""
    ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}][{level}] {msg}", flush=True)




HEARTBEAT_TEMPLATE = """# OpenClaw HEARTBEAT — bilibili-autonomous v4.9

每个心跳周期:
1. 检查精力: `bin/bilibili-autonomous energy status` 精力耗尽 → 跳过本轮
2. 拉推荐: `bin/bilibili-autonomous feed --limit 10` (JSON)
3. 用你自己的 LLM 给每个视频评分
4. `bin/bilibili-autonomous gate <score> <action>` 查阈值
5. 调原子动作:
   - `bin/bilibili-autonomous like <bvid>`
   - `bin/bilibili-autonomous coin <bvid> --num 1`
   - `bin/bilibili-autonomous favorite <bvid> --auto-check --score <X>`
   - `bin/bilibili-autonomous comment <bvid> --text "<你LLM生成的>"`
   - `bin/bilibili-autonomous danmaku <bvid> --text "<你LLM生成的>"`
   - `bin/bilibili-autonomous follow <uid>`
6. 消耗精力: `bin/bilibili-autonomous energy consume --n 1`
7. 把结果写到你自己的 memory

Web 面板: http://{host}:{port}/
skill 路径: {skill_dir}
OpenAPI: `bin/bilibili-autonomous openapi`
"""


def cmd_start(args):
    """v4.9 一键启动: 起 Web serve (前台) + 写 HEARTBEAT.md 给 OpenClaw."""
    import signal
    from . import web_panel
    settings = cfg.load_web_settings()
    host = args.bind or settings.get("bind", "127.0.0.1")
    port = args.port or int(settings.get("port", 8765))
    pid_file = cfg.DATA_DIR / ".start.pid"
    cfg.ensure_dirs()
    try:
        pid_file.write_text(str(__import__("os").getpid()), encoding="utf-8")
    except OSError:
        pass
    heartbeat_md = cfg.DATA_DIR / "HEARTBEAT.md"
    heartbeat_md.write_text(HEARTBEAT_TEMPLATE.format(
        skill_dir=cfg.SKILL_DIR, host=host, port=port
    ), encoding="utf-8")
    print(f"\n=== bilibili-autonomous 启动 ===")
    print(f"Web: http://{host}:{port}/")
    print(f"HEARTBEAT 模板: {heartbeat_md}")
    print(f"PID 文件: {pid_file}\n按 Ctrl+C 停止\n")
    try:
        web_panel.run_server(host=host, port=port, debug=False)
    except KeyboardInterrupt:
        print("\n[INFO] 停止")
    finally:
        try:
            pid_file.unlink()
        except OSError:
            pass




def cmd_energy_schedule(args):
    """v5.2 精力时段 CLI."""
    from . import energy_schedule as es
    if args.schedule_action == "status":
        s = es.status()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return _ok(s)
    if args.schedule_action == "add-active":
        s = es.add_active_hours(args.start, args.end, args.bonus)
        return _ok(s)
    if args.schedule_action == "add-low":
        s = es.add_low_hours(args.start, args.end, args.penalty)
        return _ok(s)
    if args.schedule_action == "clear":
        return _ok(es.clear())
    return _ok({"error": "unknown schedule action"})


def cmd_knowledge(args):
    """v5.5 知识库 CLI."""
    from . import knowledge as kn
    if args.knowledge_action == "list":
        cats = kn.list_categories()
        return _ok({"categories": cats})
    if args.knowledge_action == "cat":
        items = kn.list_in_category(args.category, limit=args.limit)
        return _ok({"category": args.category, "items": items})
    if args.knowledge_action == "read":
        content = kn.read_article(args.category, args.filename)
        if content is None:
            return _ok({"error": "not_found", "category": args.category, "filename": args.filename})
        return _ok({"category": args.category, "filename": args.filename, "content": content})
    if args.knowledge_action == "save":
        # OpenClaw 用: knowledge save <bvid> <title> <up> --tags t1,t2
        r = kn.save_knowledge(
            bvid=args.bvid, title=args.title, up=args.up,
            body_md=args.body, tags=args.tags.split(",") if args.tags else None,
            score=args.score,
        )
        return _ok(r)
    return _ok({"error": "unknown knowledge action"})


async def cmd_understand_v5(args):
    """v5.4 视频理解 (多模态): 拿封面+字幕+评论+弹幕 给 OpenClaw."""
    bv = _make_bapi(args.dry_run)
    out = {"bvid": args.bvid}
    # 元信息
    try:
        meta = await bv.get_video_full_meta(args.bvid)
        out["meta"] = meta
    except Exception as e:
        out["meta_error"] = str(e)
    # 封面 (OpenClaw 自己 LLM 看图, skill 不读图)
    try:
        cover = (out.get("meta") or {}).get("cover_url") or (
            f"https://i0.hdslb.com/bfs/archive/{(out.get('meta') or {}).get('aid','')}.jpg"
        )
        out["cover"] = {"url": cover, "note": "OpenClaw 用 LLM 看图分析封面"}
    except Exception:
        out["cover"] = None
    # 字幕
    if args.mode in ("subtitle", "auto"):
        try:
            subs = await bv.get_video_subtitles(args.bvid)
            from .understand import pick_subtitle
            priority = cfg.load_app_config().get("video_understanding", {}).get(
                "subtitle_priority", ["ai-zh", "zh-CN", "zh-Hans", "en"])
            chosen = pick_subtitle(subs, priority)
            out["subtitle"] = {
                "available": bool(chosen),
                "lan": chosen.get("lan") if chosen else None,
                "content": chosen.get("content", "") if chosen else "",
            }
        except Exception as e:
            out["subtitle_error"] = str(e)
    # 评论 + 弹幕 (v5.4: 拿 raw 给 OpenClaw)
    if args.with_comments:
        try:
            out["comments"] = await bv.get_top_comments(args.bvid, limit=10)
        except Exception as e:
            out["comments_error"] = str(e)
    if args.with_danmaku:
        try:
            out["danmaku"] = await bv.get_top_danmaku(args.bvid, limit=30)
        except Exception as e:
            out["danmaku_error"] = str(e)
    # 把 mood 传 OpenClaw (v5.1 集成)
    from . import mood as mood_mod
    out["mood"] = mood_mod.prompt_block()
    # psychology path
    from . import keywords as kw
    out["psychology_path"] = kw.load_psychology_path()
    return _ok(out)


async def cmd_watch(args):
    """v4.6: 高级便利工具 — OpenClaw 在 heartbeat 里调这一行就能自动刷.

    行为：
    1. 检查 energy (≤0 则拒绝)
    2. 拉 feed
    3. 对每个视频按 autonomy.* 的 prob 随机选 1-2 个原子动作
    4. 每个视频消耗 1 精力
    5. 视频间 short_sleep (5-15s)
    6. 每 --long-interval 视频穿插 long_sleep (30-180s)

    --no-energy: 跳过精力值检查 (用户已自己管理)
    v4.1 移除: --comment-cmd / --danmaku-cmd (OpenClaw 19:54 废弃)
    """
    import random as _r
    from . import energy as energy_mod

    dry_run = bool(args.dry_run)
    bv = _make_bapi(dry_run)
    app_cfg = cfg.load_app_config()
    autonomy = app_cfg.get("autonomy", {})
    if not autonomy.get("enabled", True):
        return _ok({"skipped": "autonomy.enabled=False"})

    # 1. 精力值检查
    if not args.no_energy:
        try:
            energy_mod.consume(0)  # 触发 cooldown 计算
            # 实际消耗在每个视频后
        except energy_mod.ExhaustedError as e:
            return _ok({"skipped": "energy exhausted", "detail": str(e)})

    # 2. 拉 feed
    try:
        feed = await bv.get_recommend_feed(limit=max(20, args.count + 10))
    except Exception as e:
        return _ok({"error": f"feed failed: {e}"})

    if not feed:
        return _ok({"watched": 0, "skipped": "empty feed"})

    # v4.8 视频关键词过滤 (OpenClaw 不想刷不符合关键词的视频)
    from . import favorite_keys as fk
    app_cfg_now = cfg.load_app_config()
    skipped_filter: list[dict] = []
    if app_cfg_now.get("video_filter", {}).get("enabled", False):
        kept = []
        for it in feed:
            ok, reason = fk.should_process_video(
                title=it.title, up_name=it.up, desc="",
                config=app_cfg_now,
            )
            if ok:
                kept.append(it)
            else:
                skipped_filter.append({"bvid": it.bvid, "reason": reason})
        feed = kept
        if not feed:
            return _ok({"watched": 0, "skipped": "video_filter_excluded_all",
                        "skipped_count": len(skipped_filter),
                        "skipped_filter": skipped_filter})

    selected = _r.sample(feed, min(args.count, len(feed)))
    log(f"watch: 抽 {len(selected)} 个视频 (energy={energy_mod.status()['current']}/{energy_mod.status()['max']})", "INFO")

    actions_done = 0
    actions_fail = 0
    videos_need_comment: list[dict] = []  # [{bvid, title, up}, ...] OpenClaw 处理

    plan_mode = bool(getattr(args, "plan", False))

    for idx, item in enumerate(selected):
        # 1+2: 精力值检查 + 消耗
        if not args.no_energy:
            try:
                energy_mod.consume(1)
            except energy_mod.ExhaustedError as e:
                log(f"🔋 精力耗尽，停止 (resume {e.until_iso})", "WARN")
                break

        log(f"[{idx+1}/{len(selected)}] {item.short()}", "BILI")

        # 3. 随机选 1-2 个动作（按 autonomy.* prob）
        actions_picked = []
        for action_type, prob_key, enable_key in [
            ("like", "prob_like", "enable_like"),
            ("coin", "prob_coin", "enable_coin"),
            ("favorite", "prob_favorite", "enable_favorite"),
            ("comment", "prob_comment", "enable_comment"),
            ("danmaku", "prob_danmaku", "enable_danmaku"),
        ]:
            if not autonomy.get(enable_key, True):
                continue
            if _r.random() < float(autonomy.get(prob_key, 0.0)):
                actions_picked.append(action_type)
        if not actions_picked:
            log("   无随机动作", "INFO")
        n_pick = min(len(actions_picked), _r.randint(1, 2))
        actions_picked = _r.sample(actions_picked, min(n_pick, len(actions_picked)))

        for action_type in actions_picked:
            try:
                if action_type == "like":
                    if not plan_mode:
                        ok = await bv.like_video(item)
                    else:
                        ok = True
                elif action_type == "coin":
                    if not plan_mode:
                        ok = await bv.coin_video(item, num=1)
                    else:
                        ok = True
                elif action_type == "favorite":
                    if not plan_mode:
                        ok = await bv.favorite_video(item)
                    else:
                        ok = True
                # v4.8 (OpenClaw 21:07 C 方案): watch 不再生成 text
                # 跳过 comment/danmaku，记入 videos_need_comment 给 OpenClaw 自己处理
                elif action_type == "comment":
                    videos_need_comment.append({
                        "bvid": item.bvid, "title": item.title, "up": item.up,
                        "aid": item.aid, "cid": item.cid,
                    })
                    log(f"   need comment (OpenClaw 调 comment --text)", "INFO")
                    continue
                elif action_type == "danmaku":
                    log(f"   need danmaku (OpenClaw 调 danmaku --text)", "INFO")
                    continue
                else:
                    continue
                actions_done += 1 if ok else 0
                actions_fail += 0 if ok else 1
                log(f"   {action_type}: {'✅' if ok else '❌'}", "INFO")
            except Exception as e:
                log(f"   {action_type} 异常: {e}", "WARN")
                actions_fail += 1

        # 5/6: 间隔
        if idx < len(selected) - 1:
            # 每 long_interval 个视频穿插 long_sleep
            if (idx + 1) % args.long_interval == 0:
                secs = _r.uniform(30.0, 180.0)
                log(f"   [HUMAN] 长间隔 {secs:.0f}s", "HUMAN")
                if not dry_run:
                    await asyncio.sleep(secs)
            else:
                secs = _r.uniform(5.0, 15.0)
                if not dry_run:
                    await asyncio.sleep(secs)
            # 检查 energy (中途可能耗尽)
            if not args.no_energy and energy_mod.status()["current"] <= 0:
                log("🔋 精力中途耗尽", "WARN")
                break

    summary = {
        "videos_watched": len(selected),
        "actions_done": actions_done,
        "actions_fail": actions_fail,
        "energy_remaining": energy_mod.status()["current"],
        "dry_run": dry_run,
        "plan_mode": plan_mode,
        "videos_need_comment": videos_need_comment,
        "skipped_filter_count": len(skipped_filter) if "skipped_filter" in dir() else 0,
    }
    if plan_mode:
        # plan 模式：不实际执行任何动作（包括 like/coin/favorite）
        log(f"[PLAN] {len(selected)} videos, "
            f"{sum(1 for v in videos_need_comment)} 需要评论", "INFO")
    cfg.append_log({"action": "watch", **summary})
    return _ok(summary)




def cmd_mood(args):
    """v5.1 心情 CLI."""
    from . import mood
    if args.mood_action == "status":
        s = mood.status()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return _ok(s)
    if args.mood_action == "set":
        return _ok(mood.set_mood(args.mood, args.energy, event="cli set"))
    if args.mood_action == "nudge":
        return _ok(mood.nudge(args.delta, event="cli nudge"))
    if args.mood_action == "auto":
        return _ok(mood.set_auto(args.flag == "on", args.interval))
    if args.mood_action == "prompt":
        print(mood.prompt_block())
        return _ok({"prompt": mood.prompt_block()})
    return _ok({"error": "unknown mood action"})


def cmd_energy(args):
    """v4.1: 精力值 CLI."""
    from . import energy as energy_mod
    if args.energy_action == "status":
        s = energy_mod.status()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return _ok(s)
    if args.energy_action == "consume":
        try:
            n = args.n or 1
            s = energy_mod.consume(n)
            return _ok(s)
        except energy_mod.ExhaustedError as e:
            print(f"[ERROR] {e}")
            return _ok({"current": 0.0, "exhausted": True, "resume_at": e.until_iso})
    if args.energy_action == "set-max":
        s = energy_mod.set_max(args.value)
        return _ok(s)
    if args.energy_action == "disabled":
        if args.flag == "on":
            s = energy_mod.set_disabled(True)
        elif args.flag == "off":
            s = energy_mod.set_disabled(False)
        else:
            return _ok({"error": "disabled on|off"})
        return _ok(s)
    if args.energy_action == "refill":
        return _ok(energy_mod.force_refill())
    return _ok({"error": "unknown energy action"})


# ===== argparse =====

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bilibili-autonomous",
        description="v3 B 站 API 工具集（OpenClaw AI 调用入口）")
    p.add_argument("--dry-run", action="store_true",
                   help="所有写操作只 log 不真发")
    sub = p.add_subparsers(dest="cmd")

    def add_w(subp):
        subp.add_argument("--dry-run", action="store_true",
                          help="覆盖全局 --dry-run 强制真发")

    # 写
    # v5.8 Hermes-2 修：之前用 add_w() 给每个子命令单独加 --dry-run，
    # 但 argparse 中子 parser 默认 False 会覆盖顶层的 True，
    # 导致 OpenClaw 传了 --dry-run 仍然真发。
    # 现在统一从顶层 args.dry_run 读；子命令不再单独定义。
    p_like = sub.add_parser("like"); p_like.add_argument("bvid")
    p_coin = sub.add_parser("coin"); p_coin.add_argument("bvid")
    p_coin.add_argument("--num", type=int, default=1)
    p_fav = sub.add_parser("favorite"); p_fav.add_argument("bvid")
    p_fav.add_argument("--fid", type=int, default=None)
    p_fav.add_argument("--auto-check", action="store_true",
                       help="按 config.favorite.keywords 自动决定收藏")
    p_fav.add_argument("--score", type=float, default=None,
                       help="OpenClaw 算的 score；>=archive_min 自动收藏")
    p_cmt = sub.add_parser("comment"); p_cmt.add_argument("bvid")
    p_cmt.add_argument("--text", required=True)
    p_dan = sub.add_parser("danmaku"); p_dan.add_argument("bvid")
    p_dan.add_argument("--text", required=True)
    p_fol = sub.add_parser("follow"); p_fol.add_argument("uid", type=int)
    p_fol.add_argument("--name", default="")
    p_ufl = sub.add_parser("unfollow"); p_ufl.add_argument("uid", type=int)
    p_ufl.add_argument("--name", default="")
    p_dms = sub.add_parser("dm.send"); p_dms.add_argument("uid", type=int)
    p_dms.add_argument("--text", required=True)
    p_dmc = sub.add_parser("dm.check")
    p_dmc.add_argument("--reply-cmd", required=True,
                       help="OpenClaw 提供的 shell 命令，stdin=json, stdout=json{reply}")

    # 读
    p_feed = sub.add_parser("feed"); p_feed.add_argument("--limit", type=int, default=10)
    p_video = sub.add_parser("video"); p_video.add_argument("bvid")
    p_subs = sub.add_parser("subtitles"); p_subs.add_argument("bvid")
    p_user = sub.add_parser("user"); p_user.add_argument("uid", type=int)

    # 阈值
    p_gate = sub.add_parser("gate")
    p_gate.add_argument("score", type=float)
    p_gate.add_argument("action",
                        choices=["coin", "favorite", "comment",
                                 "follow", "archive", "understand"])
    sub.add_parser("thresholds")

    # 视频理解
    p_und = sub.add_parser("understand"); p_und.add_argument("bvid")
    p_und.add_argument("--mode", default="subtitle",
                       choices=["subtitle", "whisper", "auto"])

    # 关注管理
    sub.add_parser("follow.status")
    p_fh = sub.add_parser("follow.history"); p_fh.add_argument("--limit", type=int, default=20)
    p_fis = sub.add_parser("follow.inactive_scan")
    p_fis.add_argument("--apply", action="store_true",
                        help="真的标记/记录（否则 dry-run）")

    # 管理
    sub.add_parser("status")
    p_act = sub.add_parser("actions.list"); p_act.add_argument("--category", default="")
    sub.add_parser("actions.get").add_argument("path")
    sub.add_parser("tools-log").add_argument("--tail", type=int, default=50)
    sub.add_parser("configure")
    p_serve = sub.add_parser("serve"); p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--bind", default=None)
    sub.add_parser("openapi")

    # v5.2 精力时段
    p_sched = sub.add_parser("energy-schedule", help="v5.2 精力时段 (status|add-active|add-low|clear)")
    sched_sub = p_sched.add_subparsers(dest="schedule_action", required=True)
    sched_sub.add_parser("status", help="看当前 schedule")
    p_sa = sched_sub.add_parser("add-active", help="加 active_hours (心情活跃时恢复 +bonus)")
    p_sa.add_argument("start"); p_sa.add_argument("end")
    p_sa.add_argument("--bonus", type=int, default=5)
    p_sl = sched_sub.add_parser("add-low", help="加 low_hours (精力缺乏时恢复 -penalty)")
    p_sl.add_argument("start"); p_sl.add_argument("end")
    p_sl.add_argument("--penalty", type=int, default=5)
    sched_sub.add_parser("clear", help="清空所有时段")

    # v5.5 知识库
    p_kn = sub.add_parser("knowledge", help="v5.5 知识库 (list|cat|read|save)")
    kn_sub = p_kn.add_subparsers(dest="knowledge_action", required=True)
    kn_sub.add_parser("list", help="列所有分类")
    p_knc = kn_sub.add_parser("cat", help="列某分类下的文章")
    p_knc.add_argument("category")
    p_knc.add_argument("--limit", type=int, default=20)
    p_knr = kn_sub.add_parser("read", help="读单篇")
    p_knr.add_argument("category"); p_knr.add_argument("filename")
    p_kns = kn_sub.add_parser("save", help="OpenClaw 调: 归档一条知识")
    p_kns.add_argument("bvid"); p_kns.add_argument("title"); p_kns.add_argument("up")
    p_kns.add_argument("--body", required=True, help="markdown 内容")
    p_kns.add_argument("--tags", default="", help="逗号分隔")
    p_kns.add_argument("--score", type=float, default=None)

    # v5.4 视频理解多模态
    p_und5 = sub.add_parser("understand5", help="v5.4 视频理解: 拿封面+字幕+评论+弹幕 给 OpenClaw")
    p_und5.add_argument("bvid")
    p_und5.add_argument("--mode", default="auto", choices=["auto", "subtitle", "whisper"])
    p_und5.add_argument("--with-comments", action="store_true", help="拉热评")
    p_und5.add_argument("--with-danmaku", action="store_true", help="拉弹幕")

    # v4.9 一键启动
    p_start = sub.add_parser("start", help="v4.9 一键启动: 起 Web + 写 HEARTBEAT.md 给 OpenClaw")
    p_start.add_argument("--port", type=int, default=None, help="Web 端口（默认从 web_panel.port 读）")
    p_start.add_argument("--bind", default=None, help="Web 绑定地址")

    # v4.1 精力值 CLI
    p_en = sub.add_parser("energy", help="v4.1 精力值管理 (status|consume|set-max|disabled|refill)")
    en_sub = p_en.add_subparsers(dest="energy_action", required=True)
    en_sub.add_parser("status", help="看当前精力值")
    p_enc = en_sub.add_parser("consume", help="消耗精力（OpenClaw 调用）")
    p_enc.add_argument("--n", type=int, default=1)
    p_enm = en_sub.add_parser("set-max", help="重置 max_energy (1-1000)")
    p_enm.add_argument("value", type=int)
    p_end = en_sub.add_parser("disabled", help="开/关精力值（false=用精力值 true=无限）")
    p_end.add_argument("flag", choices=["on", "off"])
    en_sub.add_parser("refill", help="手动立刻加满精力")

    # v5.1 心情 CLI
    p_mood = sub.add_parser("mood", help="v5.1 心情状态 (status|set|nudge|auto|prompt)")
    mood_sub = p_mood.add_subparsers(dest="mood_action", required=True)
    mood_sub.add_parser("status", help="看当前心情")
    p_mood_set = mood_sub.add_parser("set", help="手动设心情")
    p_mood_set.add_argument("mood", choices=["兴奋", "愉快", "平静", "好奇", "慵懒", "深沉", "调皮", "温柔", "毒舌", "学究", "中二", "佛系", "热血"])
    p_mood_set.add_argument("--energy", type=int, default=None)
    p_mood_nudge = mood_sub.add_parser("nudge", help="改精力")
    p_mood_nudge.add_argument("delta", type=int, help="+/- 整数")
    p_mood_auto = mood_sub.add_parser("auto", help="开/关自动变化")
    p_mood_auto.add_argument("flag", choices=["on", "off"])
    p_mood_auto.add_argument("--interval", type=int, default=None)
    mood_sub.add_parser("prompt", help="v5.1 关键: 输出心情 prompt 给 OpenClaw 看")

    # v4.6 watch 高级工具
    p_w = sub.add_parser("watch", help="v4.6 自动刷 N 个视频（OpenClaw heartbeat 用）")
    p_w.add_argument("--count", type=int, default=3, help="本次刷多少视频")
    p_w.add_argument("--long-interval", type=int, default=5, help="每隔 N 个穿插长间隔")
    p_w.add_argument("--no-energy", action="store_true", help="跳过精力值检查（用户自己管）")
    p_w.add_argument("--plan", action="store_true",
                    help="v4.8 只输出动作计划（哪些视频要评论），不实际执行（OpenClaw 21:07 C 方案）")
    # v4.7 删 --comment-cmd/--danmaku-cmd（OpenClaw 19:54 废弃）
    # OpenClaw 自己生成 text 再 comment/danmaku

    return p


async def main_async(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 路由
    try:
        if args.cmd in ("like", "coin", "favorite", "comment", "danmaku",
                        "follow", "unfollow", "dm.send"):
            fn = {"like": cmd_like, "coin": cmd_coin, "favorite": cmd_favorite,
                  "comment": cmd_comment, "danmaku": cmd_danmaku,
                  "follow": cmd_follow, "unfollow": cmd_unfollow,
                  "dm.send": cmd_dm_send}[args.cmd]
            return 0 if await fn(args) is not None else 0
        if args.cmd == "dm.check":
            return 0 if await cmd_dm_check(args) is not None else 0
        if args.cmd == "feed":
            await cmd_feed(args); return 0
        if args.cmd == "video":
            await cmd_video(args); return 0
        if args.cmd == "subtitles":
            await cmd_subtitles(args); return 0
        if args.cmd == "user":
            await cmd_user(args); return 0
        if args.cmd == "gate":
            cmd_gate(args); return 0
        if args.cmd == "thresholds":
            cmd_thresholds(args); return 0
        if args.cmd == "understand":
            await cmd_understand(args); return 0
        if args.cmd == "follow.status":
            await cmd_follow_status(args); return 0
        if args.cmd == "follow.history":
            await cmd_follow_history(args); return 0
        if args.cmd == "follow.inactive_scan":
            await cmd_follow_inactive_scan(args); return 0
        if args.cmd == "status":
            cmd_status(args); return 0
        if args.cmd == "actions.list":
            cmd_actions_list(args); return 0
        if args.cmd == "actions.get":
            return cmd_actions_get(args)
        if args.cmd == "tools-log":
            cmd_tools_log(args); return 0
        if args.cmd == "configure":
            cmd_configure(args); return 0
        if args.cmd == "serve":
            cmd_serve(args); return 0
        if args.cmd == "openapi":
            cmd_openapi(args); return 0
        if args.cmd == "start":
            return cmd_start(args)
        if args.cmd == "energy-schedule":
            cmd_energy_schedule(args); return 0
        if args.cmd == "knowledge":
            cmd_knowledge(args); return 0
        if args.cmd == "understand5":
            return await cmd_understand_v5(args)
        if args.cmd == "watch":
            return await cmd_watch(args)
        if args.cmd == "mood":
            return cmd_mood(args)
        if args.cmd == "energy":
            cmd_energy(args); return 0
        parser.print_help()
        return 2
    except SystemExit as se:
        print(f"[ERROR] {se}", file=sys.stderr)
        return 1


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("[WARN] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
