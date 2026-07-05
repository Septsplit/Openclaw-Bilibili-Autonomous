"""src/web_panel.py — v2.2 Flask Web 面板.

仿 bilibili_learning_bot/web_panel.py 三段认证 + 配置编辑.
简化版：只做 config 编辑 + 状态查看 + 日志查看，不控制 bot 进程。

路由：
  GET  /                    单页 SPA (templates/web.html)
  GET  /api/auth/status     认证状态（none/setup_required/needs_login/ok）
  POST /api/auth/setup      首次设置 username + password（仅当未设置时）
  POST /api/auth/login      登录
  POST /api/auth/logout     登出
  GET  /api/config          读 config
  POST /api/config          写 config（白名单 section）
  GET  /api/state           综合状态
  GET  /api/logs?lines=100  tail operations JSONL
  GET  /api/understandings  列出 understanding markdown
  GET  /api/highlights      列出归档
  GET  /api/actions/<cat>   列出某 category 的动作 markdown
  POST /api/web/settings    改 bind/port
"""
from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from flask import Flask, jsonify, request, session, send_from_directory, redirect, Response
except ImportError:
    print("[ERROR] 缺 flask. 请 uv pip install flask", file=sys.stderr)
    raise

import bcrypt

from . import actions_log, archive, config as cfg, state_view
from .safety import ReplySafetyGuard


# ===== 安全白名单：哪些 config 字段允许通过 Web 修改 =====

ALLOWED_CONFIG_SECTIONS = {
    "behavior", "interaction", "danmaku", "reply_safety",
    "autonomy", "dm", "follow", "scoring", "web_panel",
    "energy", "favorite",
}


def _filter_config(c: dict) -> dict:
    """只返回白名单 section（不再含 api，v4 已迁移到 OpenClaw 自己管 LLM）."""
    out = {}
    for k in ALLOWED_CONFIG_SECTIONS:
        if k in c:
            out[k] = json.loads(json.dumps(c[k]))
    return out


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder=str(cfg.TEMPLATES_DIR),
                static_folder=str(cfg.TEMPLATES_DIR))
    settings = cfg.load_web_settings()
    app.secret_key = settings.get("secret_key") or secrets.token_hex(32)
    # 持久化 secret_key
    if not settings.get("secret_key"):
        settings["secret_key"] = app.secret_key
        cfg.save_web_settings(settings)

    # v2.2 审核修复：session cookie 安全标志
    # bind=127.0.0.1 (HTTP) 时不强制 SECURE，因为 SECURE 要求 HTTPS
    # 否则 cookie 直接被丢弃；用 127.0.0.1/localhost 已经避免中间人风险
    bind = settings.get("bind", "127.0.0.1")
    is_https = str(bind).startswith("https://") or bind == "0.0.0.0" and False  # 简化判断
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,    # 防 XSS 读 cookie
        SESSION_COOKIE_SAMESITE="Lax",  # 防 CSRF
        # bind 是 https:// 时才 SECURE；纯 IP 默认 False
        SESSION_COOKIE_SECURE=is_https,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )

    # ---- 工具 ----
    def _is_logged_in() -> bool:
        return bool(session.get("logged_in"))

    def _auth_status() -> str:
        """none / setup_required / needs_login / ok."""
        if cfg.load_web_auth() is None:
            return "setup_required"
        if _is_logged_in():
            return "ok"
        return "needs_login"

    # v5.8 Hermes-2 修：原来 _require_login 在文件下方定义（line 167），
    # 但 api_change_password 在 line 142 就用了 @_require_login，导致
    # UnboundLocalError: cannot access local variable '_require_login'。
    # 修复：把装饰器定义移到所有 @app.route 之前。
    def _require_login(fn):
        from functools import wraps
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if _auth_status() != "ok":
                return jsonify({"error": "auth required",
                                "status": _auth_status()}), 401
            return fn(*args, **kwargs)
        return wrapper

    # ---- 认证 ----

    @app.route("/api/auth/status")
    def api_auth_status():
        return jsonify({"status": _auth_status()})

    @app.post("/api/auth/setup")
    def api_auth_setup():
        if cfg.load_web_auth() is not None:
            return jsonify({"error": "already set up"}), 400
        data = request.get_json() or {}
        user = (data.get("user") or "").strip()
        pw = data.get("password") or ""
        if not user or not pw:
            return jsonify({"error": "user/password required"}), 400
        if len(pw) < 6:
            return jsonify({"error": "password too short (≥6)"}), 400
        h = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        if not cfg.save_web_auth(user, h):
            return jsonify({"error": "save failed"}), 500
        session["logged_in"] = True
        session["user"] = user
        return jsonify({"status": "ok", "user": user})

    @app.post("/api/auth/login")
    def api_auth_login():
        auth = cfg.load_web_auth()
        if not auth:
            return jsonify({"error": "no auth set, use /api/auth/setup"}), 400
        data = request.get_json() or {}
        user = (data.get("user") or "").strip()
        pw = data.get("password") or ""
        if user != auth.get("user"):
            return jsonify({"error": "bad credentials"}), 401
        if not bcrypt.checkpw(pw.encode("utf-8"), auth.get("hash", "").encode("utf-8")):
            return jsonify({"error": "bad credentials"}), 401
        session["logged_in"] = True
        session["user"] = user
        return jsonify({"status": "ok", "user": user})

    @app.post("/api/auth/logout")
    def api_auth_logout():
        session.clear()
        return jsonify({"status": "ok"})

    @app.post("/api/auth/change-password")
    @_require_login
    def api_change_password():
        """v4.9: 改密码（验证旧密码 + bcrypt 新密码）."""
        data = request.get_json() or {}
        old_pw = data.get("old", "")
        new_pw = data.get("new", "")
        if not old_pw or not new_pw:
            return jsonify({"error": "old/new 必填"}), 400
        if len(new_pw) < 6:
            return jsonify({"error": "新密码 ≥ 6 位"}), 400
        auth = cfg.load_web_auth()
        if not auth:
            return jsonify({"error": "auth 未初始化"}), 400
        if not bcrypt.checkpw(old_pw.encode("utf-8"),
                              auth.get("hash", "").encode("utf-8")):
            return jsonify({"error": "旧密码错"}), 401
        new_hash = bcrypt.hashpw(new_pw.encode("utf-8"),
                                 bcrypt.gensalt()).decode("ascii")
        if not cfg.save_web_auth(auth.get("user", "admin"), new_hash):
            return jsonify({"error": "save failed"}), 500
        actions_log.append_operation_log({
            "action": "change_password", "user": auth.get("user", "admin"),
        })
        return jsonify({"status": "ok"})

    # ---- 配置 ----

    @app.get("/api/config")
    @_require_login
    def api_get_config():
        c = cfg.load_app_config()
        return jsonify(_filter_config(c))

    @app.post("/api/config")
    @_require_login
    def api_save_config():
        data = request.get_json() or {}
        # 白名单过滤
        safe = {k: v for k, v in data.items() if k in ALLOWED_CONFIG_SECTIONS}
        if not safe:
            return jsonify({"error": "no allowed sections"}), 400
        # 合并到现有 config
        c = cfg.load_app_config()
        for k, v in safe.items():
            c[k] = v
        if not cfg.save_app_config(c):
            return jsonify({"error": "save failed"}), 500
        actions_log.append_operation_log({"action": "web_config_update",
                                           "sections": list(safe.keys())})
        return jsonify({"status": "ok", "updated": list(safe.keys())})

    # ---- 状态 ----

    @app.get("/api/state")
    def api_get_state():
        if _auth_status() != "ok":
            return jsonify({"error": "auth required", "status": _auth_status()}), 401
        return jsonify(state_view.get_full_status())

    @app.get("/api/logs")
    def api_logs():
        if _auth_status() != "ok":
            return jsonify({"error": "auth required"}), 401
        lines = int(request.args.get("lines", 100))
        return jsonify({"entries": actions_log.tail_operations(lines=lines)})

    @app.get("/api/understandings")
    def api_understandings():
        if _auth_status() != "ok":
            return jsonify({"error": "auth required"}), 401
        return jsonify({"files": actions_log.list_action_files("understandings")})

    @app.get("/api/highlights")
    def api_highlights():
        if _auth_status() != "ok":
            return jsonify({"error": "auth required"}), 401
        cat = request.args.get("category")
        return jsonify({"files": archive.list_highlights(category=cat)})

    @app.get("/api/actions/<category>")
    def api_actions(category):
        if _auth_status() != "ok":
            return jsonify({"error": "auth required"}), 401
        date = request.args.get("date")
        return jsonify({"files": actions_log.list_action_files(category, date=date)})

    @app.post("/api/web/settings")
    @_require_login
    def api_web_settings():
        data = request.get_json() or {}
        s = cfg.load_web_settings()
        if "bind" in data:
            s["bind"] = str(data["bind"])
        if "port" in data:
            try:
                s["port"] = int(data["port"])
            except (TypeError, ValueError):
                return jsonify({"error": "port must be int"}), 400
        cfg.save_web_settings(s)
        return jsonify({"status": "ok", "settings": s})

    # ---- Cookie (v5.9) ----

    @app.get("/api/cookie")
    @_require_login
    def api_get_cookie():
        """返回当前 4 个字段的脱敏状态，不返回真实值。"""
        st = cfg.cookie_status()
        return jsonify({
            "present": st["present"],
            "missing": st["missing"],
            "count": st["count"],
            "complete": st["complete"],
            "masked": cfg.mask_cookies_dict(st["dict"]),
        })

    @app.post("/api/cookie")
    @_require_login
    def api_set_cookie():
        """接收整段 Cookie header，解析后合并写入。

        body: {"cookie": "SESSDATA=xxx; bili_jct=yyy; ..."}
        """
        data = request.get_json() or {}
        raw = (data.get("cookie") or data.get("header") or "").strip()
        if not raw:
            return jsonify({"error": "cookie 字段为空"}), 400
        parsed = cfg.parse_cookie_header(raw)
        if not parsed:
            return jsonify({
                "error": "未能解析出 SESSDATA/bili_jct/DedeUserID/ac_time_value 任一字段",
                "hint": "格式: SESSDATA=xxx; bili_jct=yyy; DedeUserID=123; ac_time_value=zzz",
            }), 400
        existing = cfg.load_cookies()
        existing.update(parsed)
        if not cfg.save_cookies(existing):
            return jsonify({"error": "save_cookies 失败，检查权限"}), 500
        actions_log.append_operation_log({
            "action": "cookie_update", "parsed": list(parsed.keys()),
            "count": len(parsed),
        })
        st = cfg.cookie_status()
        return jsonify({
            "status": "ok", "parsed": list(parsed.keys()),
            "count": st["count"], "complete": st["complete"],
            "missing": st["missing"],
        })

    @app.post("/api/cookie/clear")
    @_require_login
    def api_clear_cookie():
        if not cfg.save_cookies({}):
            return jsonify({"error": "save_cookies 失败"}), 500
        actions_log.append_operation_log({"action": "cookie_clear"})
        return jsonify({"status": "ok", "count": 0})

    # ---- SPA ----

    @app.get("/")
    def root():
        # 让前端根据 auth_status 决定显示 disclaimer/setup/login/main
        return send_from_directory(cfg.TEMPLATES_DIR, "web.html")

    # ===== v3 OpenAPI / tool discovery endpoints (OpenClaw 调) =====

    def _build_openapi_spec() -> dict:
        """v3.7: 单点 OpenAPI 描述（CLI + HTTP 都用同一份）."""
        spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "bilibili-autonomous",
                "version": "3.0.0",
                "description": "B 站 API 工具集. AI 由调用方(OpenClaw)负责.",
            },
            "servers": [{"url": "http://127.0.0.1:8765", "description": "本地 Web"}],
            "paths": {},
        }
        cmds = [
            ("like", "/like", "post"),
            ("coin", "/coin", "post"),
            ("favorite", "/favorite", "post"),
            ("comment", "/comment", "post"),
            ("danmaku", "/danmaku", "post"),
            ("follow", "/follow", "post"),
            ("unfollow", "/unfollow", "post"),
            ("dm.send", "/dm/send", "post"),
            ("dm.check", "/dm/check", "post"),
            ("feed", "/feed", "get"),
            ("video", "/video/{bvid}", "get"),
            ("subtitles", "/subtitles/{bvid}", "get"),
            ("user", "/user/{uid}", "get"),
            ("gate", "/gate", "get"),
            ("thresholds", "/thresholds", "get"),
            ("understand", "/understand", "post"),
            ("follow.status", "/follow/status", "get"),
            ("follow.history", "/follow/history", "get"),
            ("follow.inactive_scan", "/follow/inactive_scan", "post"),
            ("status", "/status", "get"),
            ("actions.list", "/actions", "get"),
            ("actions.get", "/actions/file", "get"),
            ("tools_log", "/tools-log", "get"),
        ]
        for name, path, method in cmds:
            spec["paths"][path] = {
                method: {
                    "summary": name,
                    "description": f"CLI: bilibili-autonomous {name}",
                    "responses": {"200": {"description": "JSON ok"}},
                }
            }
        return spec

    @app.get("/openapi.json")
    @_require_login
    def api_openapi():
        return jsonify(_build_openapi_spec())

    @app.get("/tools-log")
    @_require_login
    def api_tools_log():
        """v3.8: OpenClaw / 用户都可以看"""
        from . import actions_log
        log_dir = cfg.DATA_DIR / "logs"
        files = sorted(log_dir.glob("operations-*.jsonl")) if log_dir.exists() else []
        if not files:
            return jsonify({"entries": [], "file": None})
        latest = files[-1]
        try:
            tail = int(request.args.get("tail", "100"))
        except (TypeError, ValueError):
            tail = 100
        lines = latest.read_text(encoding="utf-8").splitlines()[-tail:]
        entries = []
        for ln in lines:
            try:
                entries.append(json.loads(ln))
            except Exception:
                pass
        return jsonify({"file": str(latest.relative_to(cfg.SKILL_DIR)),
                        "count": len(entries), "entries": entries})

    @app.get("/actions")
    @_require_login
    def api_actions_list():
        """v3.8: 列所有 actions markdown."""
        from . import actions_log
        cat = request.args.get("category")
        items = actions_log.list_action_files(category=cat or None)
        return jsonify({"items": items})

    @app.get("/actions/file")
    @_require_login
    def api_actions_get():
        """v3.8: 读单个 actions markdown."""
        from . import actions_log
        rel = request.args.get("path", "")
        if not rel:
            return jsonify({"error": "path 必填"}), 400
        p = cfg.SKILL_DIR / rel
        if not p.exists() or not p.is_relative_to(cfg.SKILL_DIR):
            return jsonify({"error": "文件不存在或越界"}), 404
        try:
            return Response(p.read_text(encoding="utf-8"), mimetype="text/markdown")
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.get("/healthz")
    @_require_login  # v2.2 审核修复：避免被探测服务存活状态
    def healthz():
        return jsonify({"status": "ok", "ts": datetime.now().isoformat()})

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765,
               debug: bool = False) -> None:
    cfg.ensure_dirs()
    app = create_app()
    print(f"[INFO] bilibili-autonomous Web at http://{host}:{port}/")
    print(f"[INFO] 首次访问会要求设置用户名密码")
    app.run(host=host, port=port, debug=debug, use_reloader=False)