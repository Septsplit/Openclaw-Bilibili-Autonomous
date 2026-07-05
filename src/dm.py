"""src/dm.py — v2.5 私信收发 + 上下文管理 + pacing.

参考 bilibili_learning_bot/new_agent.py:PrivateMessageManager (L1218-1600) + services/managers.py:PrivateContextDB.

Pacing:
- 单用户 cooldown 5min (configurable)
- 连续 AI 回复 ≤3 后强制沉默 1 轮
- max_replies_per_check 3 / check
- only_recent_seconds: 只回 15 分钟内的

输出安全：复用 safety.should_block().
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import actions_log, bapi as bapi_mod, config as cfg


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_log() -> dict:
    if not cfg.DM_LOG_FILE.exists():
        return {"processed_msg_ids": [], "history": []}
    try:
        with open(cfg.DM_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"processed_msg_ids": [], "history": []}


def _save_log(data: dict) -> None:
    try:
        with open(cfg.DM_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[WARN] save dm_log 失败: {e}", file=sys.stderr)


def _load_context() -> dict[str, list[dict]]:
    if not cfg.DM_CONTEXT_FILE.exists():
        return {}
    try:
        with open(cfg.DM_CONTEXT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_context(ctx: dict[str, list[dict]]) -> None:
    try:
        with open(cfg.DM_CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[WARN] save dm_context 失败: {e}", file=sys.stderr)


class DMService:
    def __init__(self, bapi: bapi_mod.BiliAPI | None = None,
                 safety=None):
        """v3 重构：DMService 不再生成内容，只负责收发 + 安全过滤。

        OpenClaw 决策"怎么回"，传入 `reply_text`；我们管 send/safety/queue。
        移除了 llm/persona/mood/user_profile 依赖。
        """
        self.bapi = bapi
        self.safety = safety
        self.cfg = cfg.load_app_config().get("dm", {})

    # ===== 主入口 =====

    async def check_and_reply(self, dry_run: bool = False,
                             reply_provider=None) -> dict:
        """v3 重构: 检查新私信 + 调 OpenClaw 提供的 reply_provider 生成回复.

        Args:
            dry_run: 干跑模式（不真发）
            reply_provider: async fn(uid, name, text, context) -> str
                （OpenClaw 提供的回复文本）。
        """
        if not self.cfg.get("enabled", True):
            return {"skipped": "dm disabled"}
        if not self.cfg.get("auto_reply", True):
            return {"skipped": "auto_reply off"}
        if not reply_provider:
            return {"skipped": "no reply_provider (OpenClaw 必须提供)"}

        log = _load_log()
        processed_ids = set(log.get("processed_msg_ids", []))
        ctx = _load_context()

        try:
            assert self.bapi is not None
            msgs = await self.bapi.fetch_new_dms(
                only_recent_seconds=int(self.cfg.get("only_recent_seconds", 900))
            )
        except Exception as e:
            return {"error": f"fetch failed: {e}"}

        if not msgs:
            return {"processed": 0}

        max_n = int(self.cfg.get("max_replies_per_check", 3))
        cooldown_min = int(self.cfg.get("private_reply_cooldown_minutes", 3))
        max_consec = int(cfg.load_app_config()
                         .get("autonomous", {}).get("max_consecutive_ai_replies", 3))

        processed_count = 0
        history_entries: list[dict] = []

        for msg in msgs[:max_n]:
            msg_id = str(msg.get("id") or msg.get("msg_id") or "")
            if msg_id in processed_ids:
                continue
            uid = str(msg.get("sender_uid") or msg.get("uid") or "")
            user_name = msg.get("sender_name") or msg.get("name") or f"uid_{uid}"
            text = msg.get("text") or msg.get("content") or ""
            ts_iso = msg.get("ts") or datetime.now().isoformat()

            # pacing: cooldown
            if not self._pacing_ok(uid, cooldown_min, ctx):
                history_entries.append({
                    "ts": datetime.now().isoformat(),
                    "msg_id": msg_id, "uid": uid, "user_name": user_name,
                    "direction": "received", "content": text, "action": "skipped_cooldown",
                })
                continue

            # context
            user_ctx = ctx.get(uid, [])
            context_block = self._build_context_block(user_ctx)

            # v3: 从 OpenClaw 的 reply_provider 拿回复（不是 LLM）
            reply = None
            try:
                reply = await reply_provider(uid, user_name, text, context_block)
            except Exception as e:
                print(f"   [WARN] OpenClaw reply_provider 失败: {e}")

            # 安全检查
            if reply and self.safety and self.safety.should_block(reply):
                reply = None
                history_entries.append({
                    "ts": datetime.now().isoformat(),
                    "msg_id": msg_id, "uid": uid, "user_name": user_name,
                    "direction": "received", "content": text,
                    "action": "blocked_by_safety",
                })

            # 发送（除非 dry_run）
            sent_ok = False
            if reply and not dry_run:
                try:
                    sent_ok = await self.bapi.send_private_message(int(uid), reply)
                except Exception as e:
                    print(f"   [WARN] 发送私信失败: {e}")
            elif reply and dry_run:
                sent_ok = True  # dry_run 假装成功

            # 更新 context
            user_ctx.append({"role": "user", "content": text, "ts": ts_iso})
            if reply:
                user_ctx.append({"role": "assistant", "content": reply,
                                 "ts": datetime.now().isoformat()})
            # trim
            ctx_len = int(self.cfg.get("context_len", 20))
            if len(user_ctx) > ctx_len:
                user_ctx = user_ctx[-ctx_len:]
            ctx[uid] = user_ctx

            processed_ids.add(msg_id)
            processed_count += 1

            # markdown 记录
            actions_log.write_action_markdown(
                category="dms",
                filename=f"{uid}-{msg_id}",
                frontmatter={
                    "ts": datetime.now().isoformat(),
                    "uid": int(uid),
                    "user_name": user_name,
                    "action": "dm_received" + ("_replied" if sent_ok else "_no_reply"),
                    "msg_id": msg_id,
                },
                body=f"# 私信: 与 {user_name} (uid={uid})\n\n"
                     f"**收到** ({ts_iso}):\n{text}\n\n"
                     + (f"**回复**:\n{reply}\n" if reply else "**未回复**\n"),
            )

            history_entries.append({
                "ts": datetime.now().isoformat(),
                "msg_id": msg_id, "uid": uid, "user_name": user_name,
                "direction": "received",
                "content": text, "reply": reply,
                "sent": sent_ok, "dry_run": dry_run,
            })

        # 持久化
        log["processed_msg_ids"] = list(processed_ids)[-1000:]  # 只保留最近 1000
        log["history"] = (log.get("history", []) + history_entries)[-500:]
        _save_log(log)
        _save_context(ctx)

        # 操作日志
        actions_log.append_operation_log({
            "action": "dm_check",
            "processed": processed_count,
            "dry_run": dry_run,
        })
        return {"processed": processed_count, "dry_run": dry_run}

    # ===== Pacing =====

    def _pacing_ok(self, uid: str, cooldown_min: int,
                   ctx: dict[str, list[dict]]) -> bool:
        """检查用户 cooldown + 连续 AI 回复数."""
        user_ctx = ctx.get(uid, [])
        if not user_ctx:
            return True
        # 上次 assistant 回复距今
        last_assistant = next(
            (m for m in reversed(user_ctx) if m.get("role") == "assistant"),
            None,
        )
        if last_assistant and last_assistant.get("ts"):
            try:
                last_dt = datetime.fromisoformat(last_assistant["ts"])
                if datetime.now() - last_dt < timedelta(minutes=cooldown_min):
                    return False
            except ValueError:
                pass
        # 连续 AI 回复数（用户没回就一直 AI 回）
        consec = 0
        for m in reversed(user_ctx):
            if m.get("role") == "assistant":
                consec += 1
            else:
                break
        max_consec = int(cfg.load_app_config()
                         .get("autonomous", {}).get("max_consecutive_ai_replies", 3))
        if consec >= max_consec:
            return False  # 强制沉默 1 轮
        return True

    def _build_context_block(self, user_ctx: list[dict]) -> str:
        if not user_ctx:
            return ""
        lines = []
        for m in user_ctx[-10:]:  # 只用最近 10 条避免 prompt 过长
            who = "我" if m.get("role") == "assistant" else "对方"
            lines.append(f"{who}: {m.get('content', '')}")
        return "\n".join(lines)

    # ===== 查询 =====

    def status(self) -> dict:
        log = _load_log()
        return {
            "processed_total": len(log.get("processed_msg_ids", [])),
            "history_count": len(log.get("history", [])),
            "context_users": len(_load_context()),
            "enabled": self.cfg.get("enabled", True),
            "auto_reply": self.cfg.get("auto_reply", True),
        }