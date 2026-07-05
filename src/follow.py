"""src/follow.py — v2.5 UP 主关注/取关 + 决策树.

参考 bilibili_learning_bot/new_agent.py:maybe_follow_up (L10480) + follow_up (L7268).

决策流程：
1. score >= min_score(7.0) AND (impressions >= 2 OR score >= exceptional(8.5))
2. random < auto_follow_prob(0.08)
3. daily < max_daily_follows(3)
4. uid 不在 cooldown 中（90 分钟）

状态：Data/follow_state.json
- today / daily_count / last_follow_at / per_uid_cooldown / 关注历史
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import actions_log, bapi as bapi_mod, config as cfg


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_state() -> dict:
    if not cfg.FOLLOW_STATE_FILE.exists():
        return _fresh_state()
    try:
        with open(cfg.FOLLOW_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return _fresh_state()


def _save_state(st: dict) -> None:
    try:
        with open(cfg.FOLLOW_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[WARN] save follow_state 失败: {e}", file=sys.stderr)


def _fresh_state() -> dict:
    return {
        "today": _today(),
        "daily_count": 0,
        "cooldowns": {},     # {uid_str: iso_ts}
        "history": [],       # [{ts, uid, name, action: follow/unfollow, score?, reason?}]
    }


def _maybe_rollover(st: dict) -> dict:
    if st.get("today") != _today():
        st["today"] = _today()
        st["daily_count"] = 0
        # cooldowns 不清空（按时间过期判断）
    return st


class FollowEngine:
    def __init__(self, bapi: bapi_mod.BiliAPI | None = None):
        self.bapi = bapi
        self.cfg = cfg.load_app_config().get("follow", {})

    # ===== 决策 =====

    def decide(self, uid: int | str, up_name: str = "",
               score: float = 0.0, impressions: int = 0) -> tuple[bool, str]:
        """判断是否应该关注。返回 (should, reason)。"""
        if not self.cfg.get("enabled", True):
            return False, "follow disabled"
        if score < float(self.cfg.get("min_score", 7.0)):
            return False, f"score {score:.1f} < min_score {self.cfg['min_score']}"

        exceptional = float(self.cfg.get("exceptional_score", 8.5))
        min_imp = int(self.cfg.get("min_impressions", 2))
        if score < exceptional and impressions < min_imp:
            return False, f"score {score:.1f} < exceptional {exceptional} AND impressions {impressions} < {min_imp}"

        st = _maybe_rollover(_load_state())
        max_daily = int(self.cfg.get("max_daily_follows", 3))
        if st["daily_count"] >= max_daily:
            return False, f"daily limit reached ({max_daily})"

        # cooldown 检查
        cooldown_min = int(self.cfg.get("cooldown_minutes", 90))
        last_iso = st.get("cooldowns", {}).get(str(uid))
        if last_iso:
            try:
                last_dt = datetime.fromisoformat(last_iso)
                if datetime.now() - last_dt < timedelta(minutes=cooldown_min):
                    return False, f"cooldown ({cooldown_min}min)"
            except ValueError:
                pass

        # 概率抽奖
        prob = float(self.cfg.get("auto_follow_prob", 0.08))
        if random.random() > prob:
            return False, f"random roll failed (p={prob})"

        return True, "eligible"

    # ===== 执行 =====

    async def follow(self, uid: int, name: str = "", score: float = 0.0,
                     reason: str = "") -> tuple[bool, str]:
        """实际调用 B 站 API 关注."""
        if self.bapi is None:
            return False, "no bapi"
        try:
            ok = await self.bapi.follow_user(uid)
            if ok:
                st = _maybe_rollover(_load_state())
                st["daily_count"] = int(st.get("daily_count", 0)) + 1
                st.setdefault("cooldowns", {})[str(uid)] = datetime.now().isoformat()
                st.setdefault("history", []).append({
                    "ts": datetime.now().isoformat(),
                    "uid": int(uid), "name": name,
                    "action": "follow", "score": round(score, 2),
                    "reason": reason[:120],
                })
                _save_state(st)
                # 动作 markdown
                self._write_follow_md(name, uid, "follow", score, reason)
                actions_log.append_operation_log({
                    "action": "follow", "uid": int(uid), "name": name,
                    "score": round(score, 2), "reason": reason[:120],
                })
            return ok, "ok" if ok else "api failed"
        except Exception as e:
            return False, f"exception: {e}"

    async def unfollow(self, uid: int, name: str = "") -> tuple[bool, str]:
        if self.bapi is None:
            return False, "no bapi"
        try:
            ok = await self.bapi.unfollow_user(uid)
            if ok:
                st = _maybe_rollover(_load_state())
                st.setdefault("history", []).append({
                    "ts": datetime.now().isoformat(),
                    "uid": int(uid), "name": name,
                    "action": "unfollow",
                })
                _save_state(st)
                self._write_follow_md(name, uid, "unfollow", 0.0, "")
                actions_log.append_operation_log({
                    "action": "unfollow", "uid": int(uid), "name": name,
                })
            return ok, "ok" if ok else "api failed"
        except Exception as e:
            return False, f"exception: {e}"

    def _write_follow_md(self, name: str, uid: int, action: str,
                         score: float, reason: str) -> Path | None:
        return actions_log.write_action_markdown(
            category="follows",
            filename=f"{int(uid)}-{action}",
            frontmatter={
                "ts": datetime.now().isoformat(),
                "uid": int(uid),
                "name": name,
                "action": action,
                "score": round(score, 2),
            },
            body=f"# {'关注' if action == 'follow' else '取关'}: {name} (uid={uid})\n\n"
                 f"- 评分: {score:.1f}\n- 理由: {reason}\n- ts: {datetime.now().isoformat()}\n",
        )

    # ===== 查询 =====

    def status(self) -> dict:
        st = _maybe_rollover(_load_state())
        return {
            "today": st["today"],
            "daily_count": st["daily_count"],
            "max_daily": int(self.cfg.get("max_daily_follows", 3)),
            "remaining": max(0, int(self.cfg.get("max_daily_follows", 3)) - st["daily_count"]),
            "history_count": len(st.get("history", [])),
        }

    def history(self, limit: int = 20) -> list[dict]:
        st = _maybe_rollover(_load_state())
        return st.get("history", [])[-limit:][::-1]
    # ===== v2.2 审核新增：unfollow_inactive_days 自动取关 =====

    def mark_up_active(self, uid: int) -> None:
        """标记某个 UP 主最近被互动过（用于 active-tracking）。"""
        st = _maybe_rollover(_load_state())
        st.setdefault("active_log", {})[str(uid)] = datetime.now().isoformat()
        _save_state(st)

    def scan_inactive(self, dry_run: bool = True) -> dict:
        """扫描关注历史，找出最近没活跃 > unfollow_inactive_days 天的 UP 主。

        返回 {inactive: [...], unfollowed: [...], skipped: [...]}。
        dry_run=False 时只更新本地状态 + 标记；**实际 B 站 API 取关**
        仍需调用者用 bapi.unfollow_user() 二次执行。
        """
        days = int(self.cfg.get("unfollow_inactive_days", 0))
        if days <= 0:
            return {"skipped": "unfollow_inactive_days <= 0 (功能关闭)"}

        st = _maybe_rollover(_load_state())
        history = st.get("history", [])
        active_log = st.get("active_log", {}) or {}
        now = datetime.now()

        # 找所有关注过的 UP（按 uid 聚合成"最新一次 follow"）
        followed: dict[str, dict] = {}
        for h in history:
            if h.get("action") != "follow":
                continue
            uid_s = str(h.get("uid", ""))
            if not uid_s:
                continue
            ts = h.get("ts", "")
            if uid_s not in followed or ts > followed[uid_s]["ts"]:
                followed[uid_s] = {"uid": uid_s, "name": h.get("name", ""), "ts": ts}

        # 过滤掉已 unfollow 的
        for h in history:
            if h.get("action") in ("unfollow", "unfollow_auto_inactive"):
                followed.pop(str(h.get("uid", "")), None)
                active_log.pop(str(h.get("uid", "")), None)

        # 找过期未活跃的 UP
        inactive: list[dict] = []
        for uid_s, info in followed.items():
            active_ts = active_log.get(uid_s)
            if active_ts:
                last_active = datetime.fromisoformat(active_ts)
            elif info["ts"]:
                last_active = datetime.fromisoformat(info["ts"])
            else:
                last_active = now
            days_since = (now - last_active).days
            if days_since >= days:
                inactive.append({
                    "uid": uid_s,
                    "name": info["name"],
                    "last_active": active_ts or info["ts"],
                    "days_inactive": days_since,
                })
        inactive.sort(key=lambda x: -x["days_inactive"])

        if dry_run:
            return {"inactive": inactive, "dry_run": True,
                    "threshold_days": days}

        # 真标记：写 unfollow_auto_inactive 历史
        unfollowed = []
        for item in inactive:
            st["history"].append({
                "ts": now.isoformat(),
                "uid": int(item["uid"]),
                "name": item["name"],
                "action": "unfollow_auto_inactive",
                "reason": f"inactive {item['days_inactive']}d >= {days}d",
            })
            unfollowed.append(item)
            actions_log.append_operation_log({
                "action": "unfollow_auto_inactive",
                "uid": int(item["uid"]),
                "name": item["name"],
                "days_inactive": item["days_inactive"],
            })
        _save_state(st)
        return {"inactive": inactive, "unfollowed": unfollowed,
                "threshold_days": days, "dry_run": False}
