"""src/cli_config.py — v2.1 交互式 CLI 配置器.

仿 bilibili_learning_bot/new_agent.py:show_main_menu + configure_X 模式.

主菜单 9 项：
  1. AI 配置（API key / base URL / 模型）
  2. 互动配置（投币/收藏/评论阈值、概率权重、AI marker）
  3. Run 节奏（watch_min/max、interval、enable flags）
  4. 安全过滤（reply_safety）
  5. 私信 (v2.5)
  6. 关注 UP 主 (v2.5)
  7. 视频理解 (v2.4)
  8. Web 面板 (v2.2)
  9. 限流 & 状态 / 日志 tail

每个 configure_X 函数：显示当前值 → input("回车保持") → 校验 → save_app_config。
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    _HAS_COLOR = True
except ImportError:
    _HAS_COLOR = False

    class _Dummy:
        def __getattr__(self, _): return ""
    Fore = Style = _Dummy()  # type: ignore[assignment]

from . import actions_log, config as cfg


# ===== 工具 =====

def _hr() -> None:
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")


def _info(s: str) -> None:
    print(f"{Fore.CYAN}{s}{Style.RESET_ALL}")


def _ok(s: str) -> None:
    print(f"{Fore.GREEN}[OK] {s}{Style.RESET_ALL}")


def _warn(s: str) -> None:
    print(f"{Fore.YELLOW}[WARN] {s}{Style.RESET_ALL}")


def _ask(s: str, current: Any = None) -> str:
    """带当前值提示的输入。直接回车保持。"""
    if current is not None and current != "":
        shown = current if not isinstance(current, str) or len(current) < 100 else cfg.mask_secret(current)
        prompt = f"{Fore.YELLOW}{s} (当前: {shown}，回车保持): {Style.RESET_ALL}"
    else:
        prompt = f"{Fore.YELLOW}{s}: {Style.RESET_ALL}"
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _ask_int(s: str, current: int, lo: int | None = None, hi: int | None = None) -> int | None:
    raw = _ask(s, current)
    if not raw:
        return None
    try:
        v = int(raw)
        if lo is not None and v < lo:
            _warn(f"值 {v} < 最小 {lo}，保持原样")
            return None
        if hi is not None and v > hi:
            _warn(f"值 {v} > 最大 {hi}，保持原样")
            return None
        return v
    except ValueError:
        _warn(f"不是整数: {raw!r}，保持原样")
        return None


def _ask_float(s: str, current: float, lo: float = 0.0, hi: float = 1.0) -> float | None:
    raw = _ask(s, current)
    if not raw:
        return None
    try:
        v = float(raw)
        if v < lo or v > hi:
            _warn(f"值 {v} 不在 [{lo}, {hi}]，保持原样")
            return None
        return v
    except ValueError:
        _warn(f"不是数字: {raw!r}，保持原样")
        return None


def _ask_bool(s: str, current: bool) -> bool | None:
    cur_s = "开" if current else "关"
    raw = _ask(f"{s} (开/关)", cur_s).lower()
    if not raw:
        return None
    if raw in ("开", "y", "yes", "true", "on", "1"):
        return True
    if raw in ("关", "n", "no", "false", "off", "0"):
        return False
    _warn(f"无法识别: {raw!r}，保持原样")
    return None


def _save(cfg_dict: dict) -> None:
    if cfg.save_app_config(cfg_dict):
        _ok("配置已保存到 Data/config.json")
    else:
        _warn("保存失败")


# ===== 主菜单 =====

def show_main_menu() -> None:
    while True:
        _hr()
        _info("bilibili-autonomous 配置器 (v4)")
        print()
        print(f"  {Fore.GREEN}[Q] ⚡ 快速配置{Style.RESET_ALL}    关键选项（精力/关键词/总开关）")
        print(f"  {Fore.GREEN}[A] 🔧 全方位配置{Style.RESET_ALL}    所有细节（阈值/概率/安全/私信等）")
        print(f"  {Fore.GREEN}[C] 🍪 B 站 Cookie 设置{Style.RESET_ALL}  凭据 4 个字段（指定账号刷）")
        print("  [b] ↩️  返回上级 (退出)")
        print()
        try:
            choice = input(f"{Fore.CYAN}请选择 (Q/A/C/b): {Style.RESET_ALL}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        # v4.2 修 bug：原代码 "b/q/quit/exit" 一律 return，把 q 当退出信号，永远走不到 _menu_quick
        if choice in ("b", "quit", "exit"):
            return
        if choice in ("q", "quick", "1"):
            _menu_quick()
        elif choice in ("a", "advanced", "2"):
            _menu_advanced()
        elif choice in ("c", "cookie", "3"):  # v5.9 新增
            _menu_cookie()
        else:
            _warn(f"未知选项: {choice}")


def _menu_quick() -> None:
    """v5 快速配置 — 关键的 6 个设置块 (含 v5.1/5.2/5.3)."""
    while True:
        _hr()
        _info("⚡ 快速配置（v5：含心情/时段/关键词升级）")
        print()
        c = cfg.load_app_config()

        print(f"{Fore.CYAN}[1] 🔋 精力值系统{Style.RESET_ALL}")
        from . import energy as em
        print(f"   当前状态: {em.status()}")
        raw = _ask(f"   max_energy (1-1000, 当前 {c.get('energy', {}).get('max_energy', 20)})", "")
        if raw:
            try:
                c.setdefault('energy', {})['max_energy'] = int(raw)
                em.set_max(int(raw))
            except ValueError:
                _warn("不是整数")
        raw = _ask("   关闭精力值? (关=无限, 默认开)", "")
        if raw.lower() in ("关", "no", "false", "off", "0"):
            c['energy']['disabled'] = True
            em.set_disabled(True)
        elif raw.lower() in ("开", "yes", "true", "on", "1"):
            c['energy']['disabled'] = False
            em.set_disabled(False)

        print()
        print(f"{Fore.CYAN}[2] 🔖 关键词收藏{Style.RESET_ALL}")
        current_kws = c.get("favorite", {}).get("keywords", []) or []
        print(f"   当前关键词 ({len(current_kws)} 个): {current_kws if current_kws else '(空)'}")
        raw = _ask("   新增关键词（逗号分隔，留空跳过）", "")
        if raw:
            new_kws = [w.strip() for w in raw.split(",") if w.strip()]
            existing = set(current_kws)
            for w in new_kws:
                if w not in existing:
                    current_kws.append(w)
                    existing.add(w)
            c['favorite']['keywords'] = current_kws

        print()
        print(f"{Fore.CYAN}[3] 🛡 总开关 (autonomy){Style.RESET_ALL}")
        aut = c.setdefault("autonomy", {})
        flags = [
            ("enable_like", "点赞"),
            ("enable_coin", "投币"),
            ("enable_comment", "评论"),
            ("enable_danmaku", "弹幕"),
            ("enable_favorite", "收藏"),
            ("enable_dm_reply", "回私信"),
            ("enable_proactive_dm", "主动私信"),
            ("enable_high_quality_archive", "高质归档"),
            ("enable_proactive_coin_like", "主动投币点赞"),
        ]
        print("   当前: " + ", ".join(f"{label}={'开' if aut.get(k) else '关'}" for k, label in flags))
        raw = _ask("   输入要切换的 key=on/off 列表 (例: enable_like off,enable_coin on)", "")
        if raw:
            for token in raw.split(","):
                token = token.strip()
                if "=" not in token:
                    continue
                key, val = token.split("=", 1)
                key = key.strip()
                val = val.strip().lower()
                if key in [f[0] for f in flags]:
                    aut[key] = (val in ("on", "true", "yes", "1", "开"))

        print()
        print(f"{Fore.CYAN}[4] ⭐ 收藏总开关{Style.RESET_ALL}")
        cur = c.get("favorite", {}).get("enabled", True)
        raw = _ask(f"   启用收藏 ({'开' if cur else '关'})", "")
        if raw.lower() in ("关", "no", "false", "off", "0"):
            c['favorite']['enabled'] = False
        elif raw.lower() in ("开", "yes", "true", "on", "1"):
            c['favorite']['enabled'] = True

        # === v5.1 心情系统 ===
        print()
        print(Fore.CYAN + "[5] 💚 心情系统 (v5.1)" + Style.RESET_ALL)
        from . import mood as mood_mod
        ms = mood_mod.status()
        print(f"   当前: {ms['current']} 精力 {ms['energy']}/100 ({ms['level']})  auto={ms['auto_change']}")
        raw = _ask(f"   心情名 (留空跳过, 选项 {list(mood_mod.MOODS)})", "")
        if raw in mood_mod.MOODS:
            mood_mod.set_mood(raw, event="cli quick set")
            print(f"   ✅ 心情 → {raw}")
        raw2 = _ask("   设心情精力 0-100 (留空跳过)", "")
        if raw2 and raw2.isdigit():
            mood_mod.set_mood(mood_mod.status()["current"], int(raw2), event="cli energy")

        # === v5.2 精力时段 ===
        print()
        print(Fore.CYAN + "[6] ⏰ 精力时段 (v5.2)" + Style.RESET_ALL)
        from . import energy_schedule as es
        sched = es.status()
        print(f"   active_hours: {len(sched['active_hours'])} 个  /  low_hours: {len(sched['low_hours'])} 个")
        raw = _ask("   加 active (格式 20:00 23:00 bonus=8, 留空跳过)", "")
        if raw:
            try:
                parts = raw.split()
                if len(parts) < 2:
                    raise ValueError("需要 start end [bonus=N] 至少 2 段")
                start, end = parts[0], parts[1]
                bonus = 5
                if len(parts) > 2 and "=" in parts[2]:
                    bonus = int(parts[2].split("=", 1)[1])
                es.add_active_hours(start, end, bonus)
                print(f"   ✅ active {start}-{end} +{bonus}")
            except ValueError as e:
                _warn(f"解析失败: {e}")
            except Exception as e:
                _warn(f"未知错误: {e}")
        raw = _ask("   加 low (格式 01:00 05:00 penalty=6, 留空跳过)", "")
        if raw:
            try:
                parts = raw.split()
                if len(parts) < 2:
                    raise ValueError("需要 start end [penalty=N] 至少 2 段")
                start, end = parts[0], parts[1]
                penalty = 5
                if len(parts) > 2 and "=" in parts[2]:
                    penalty = int(parts[2].split("=", 1)[1])
                es.add_low_hours(start, end, penalty)
                print(f"   ✅ low {start}-{end} -{penalty}")
            except ValueError as e:
                _warn(f"解析失败: {e}")
            except Exception as e:
                _warn(f"未知错误: {e}")

        # === v5.3 关键词升级 ===
        print()
        print(Fore.CYAN + "[7] 🔍 关键词升级 (v5.3: synonyms/exclude)" + Style.RESET_ALL)
        ks = c.setdefault("keyword_system", {}).setdefault("favorite", {})
        cur_inc = ks.get("include", []) or []
        cur_exc = ks.get("exclude", []) or []
        print(f"   当前 include: {cur_inc}  /  exclude: {cur_exc}")
        raw = _ask("   加 include 词 (逗号分隔, 留空跳过)", "")
        if raw:
            new = [w.strip() for w in raw.split(",") if w.strip()]
            ks.setdefault("include", []).extend(new)
            ks["include"] = list(dict.fromkeys(ks["include"]))
        raw = _ask("   加 exclude 词 (逗号分隔)", "")
        if raw:
            new = [w.strip() for w in raw.split(",") if w.strip()]
            ks.setdefault("exclude", []).extend(new)
            ks["exclude"] = list(dict.fromkeys(ks["exclude"]))
        print(f"   💡 synonyms (词组展开) 用 'configure → A 全方位' 配 JSON")

        print()
        if cfg.save_app_config(c):
            _ok("快速配置已保存")
        else:
            _warn("保存失败")
        print()
        raw = _ask("继续配置其他项? (回车=退出)", "")
        if not raw:
            return


def _menu_advanced() -> None:
    """v4.4 全方位配置 — 详细菜单."""
    while True:
        _hr()
        _info("🔧 全方位配置")
        print()
        print("  [1] 💬 互动配置（阈值、概率）")
        print("  [2] 🛡  安全过滤（reply_safety）")
        print("  [3] 📨 私信")
        print("  [4] ➕  关注 UP 主")
        print("  [5] 🎬 视频理解（whisper 配置）")
        print("  [6] 🌐 Web 面板")
        print("  [7] 📈 评分阈值")
        print("  [8] 🚦 限流 & 状态")
        print("  [b] ↩️  返回上级")
        print()
        try:
            choice = input(f"{Fore.CYAN}请选择: {Style.RESET_ALL}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("b", "q", "back"):
            return
        if choice == "1":
            _menu_interaction()
        elif choice == "2":
            _menu_safety()
        elif choice == "3":
            _menu_dm()
        elif choice == "4":
            _menu_follow()
        elif choice == "5":
            _menu_understand()
        elif choice == "6":
            _menu_web()
        elif choice == "7":
            _menu_thresholds()
        elif choice == "8":
            from . import state_view
            state_view.print_full_status()
        else:
            _warn(f"未知选项: {choice}")


def _menu_thresholds() -> None:
    """v4.4: 列出所有阈值."""
    from . import scorer as scorer_mod
    t = scorer_mod.Thresholds.from_config()
    print(f"\\n{Fore.CYAN}📈 当前阈值:{Style.RESET_ALL}")
    print(f"   coin: {t.coin}    favorite: {t.favorite}    comment: {t.comment}")
    print(f"   follow: {t.follow}  (exceptional {t.follow_exceptional}, impressions {t.follow_min_impressions})")
    print(f"   archive: {t.archive}    understand: {t.understand}")
    raw = _ask("重置为默认值? (y)", "")
    if raw.lower() == "y":
        c = cfg.load_app_config()
        c['scoring'] = {
            "coin_min": 8.0, "favorite_min": 8.5, "comment_min": 6.5,
            "follow_min": 7.0, "archive_min": 7.5, "understand_min": 6.0,
            "follow_exceptional": 8.5, "follow_min_impressions": 2,
        }
        if cfg.save_app_config(c):
            _ok("已重置")

            _menu_follow()
        elif choice == "7":
            _menu_understand()
        elif choice == "8":
            _menu_web()
        elif choice == "9":
            _menu_status()
        elif choice == "s":
            _ok("已保存。再见！")
            return
        elif choice == "q":
            _warn("不保存退出")
            return


# ===== 子菜单 =====

def _menu_interaction() -> None:
    c = cfg.load_app_config()
    inter = c["interaction"]
    beh = c["behavior"]
    print(f"\n{Fore.CYAN}--- 互动配置 ---{Style.RESET_ALL}")

    v = _ask_float("投币阈值 coin_threshold (0-10)", inter.get("coin_threshold", 8.0), 0, 10)
    if v is not None:
        inter["coin_threshold"] = v

    v = _ask_float("收藏阈值 fav_threshold (0-10)", inter.get("fav_threshold", 8.5), 0, 10)
    if v is not None:
        inter["fav_threshold"] = v

    v = _ask_float("点赞概率 prob_like (0-1)", inter.get("prob_like_solo", 0.5))
    if v is not None:
        inter["prob_like_solo"] = v

    v = _ask_float("投币概率 prob_coin (0-1)", inter.get("prob_coin", 0.25))
    if v is not None:
        inter["prob_coin"] = v

    v = _ask_float("评论概率 prob_comment (0-1)", inter.get("prob_comment_others", 0.15))
    if v is not None:
        inter["prob_comment_others"] = v

    # v2.2 审核新增：收藏概率 + 启用开关
    v = _ask_float("收藏概率 prob_favorite (0-1)", inter.get("prob_favorite", 0.10))
    if v is not None:
        inter["prob_favorite"] = v

    # v3: ai_marker 由 OpenClaw 自管理；本 skill 不再配置
    _save(c)


def _menu_autonomous() -> None:
    c = cfg.load_app_config()
    a = c["autonomous"]
    print(f"\n{Fore.CYAN}--- Run 节奏 ---{Style.RESET_ALL}")

    v = _ask_int("每次刷视频数 min", a.get("watch_min", 3), 1, 20)
    if v is not None:
        a["watch_min"] = v
    v = _ask_int("每次刷视频数 max", a.get("watch_max", 5), 1, 20)
    if v is not None:
        a["watch_max"] = v

    raw = _ask("视频间隔最小 (秒)", a.get("interval_min_seconds", 5.0))
    if raw:
        try: a["interval_min_seconds"] = float(raw)
        except ValueError: _warn("不是数字，保持原样")
    raw = _ask("视频间隔最大 (秒)", a.get("interval_max_seconds", 15.0))
    if raw:
        try: a["interval_max_seconds"] = float(raw)
        except ValueError: _warn("不是数字，保持原样")

    v = _ask_bool("启用评论", a.get("enable_comment", True))
    if v is not None:
        a["enable_comment"] = v
    v = _ask_bool("启用弹幕", a.get("enable_danmaku", True))
    if v is not None:
        a["enable_danmaku"] = v
    v = _ask_bool("启用收藏", a.get("enable_favorite", True))
    if v is not None:
        a["enable_favorite"] = v
    v = _ask_bool("启用自动视频理解", a.get("enable_understand", True))
    if v is not None:
        a["enable_understand"] = v
    v = _ask_float("理解概率 (0-1)", a.get("prob_understand", 0.2))
    if v is not None:
        a["prob_understand"] = v

    _save(c)


def _menu_safety() -> None:
    c = cfg.load_app_config()
    s = c["reply_safety"]
    print(f"\n{Fore.CYAN}--- 安全过滤 ---{Style.RESET_ALL}")

    v = _ask_bool("启用敏感词过滤", s.get("enabled", True))
    if v is not None:
        s["enabled"] = v
    v = _ask_bool("拦截发出的内容", s.get("block_on_outgoing", True))
    if v is not None:
        s["block_on_outgoing"] = v

    print(f"\n  当前 blocked_keywords: {len(s.get('blocked_keywords', []))} 个")
    raw = _ask("替换关键词列表（每行一个，空则跳过）", "")
    if raw:
        new_kws = [line.strip() for line in raw.split("\n") if line.strip()]
        if new_kws:
            s["blocked_keywords"] = new_kws
            _ok(f"已更新 {len(new_kws)} 个关键词")

    print(f"\n  当前 political_video_keywords: {len(s.get('political_video_keywords', []))} 个")
    raw = _ask("替换政治视频关键词列表（每行一个，空则跳过）", "")
    if raw:
        new_kws = [line.strip() for line in raw.split("\n") if line.strip()]
        if new_kws:
            s["political_video_keywords"] = new_kws
            _ok(f"已更新 {len(new_kws)} 个政治关键词")

    _save(c)


def _menu_dm() -> None:
    c = cfg.load_app_config()
    d = c.setdefault("dm", {})
    print(f"\n{Fore.CYAN}--- 私信 (v2.5) ---{Style.RESET_ALL}")

    v = _ask_bool("启用私信", d.get("enabled", True))
    if v is not None: d["enabled"] = v
    v = _ask_bool("自动回私信", d.get("auto_reply", True))
    if v is not None: d["auto_reply"] = v
    v = _ask_bool("主动私信（默认关闭）", d.get("enable_proactive_dm", False))
    if v is not None: d["enable_proactive_dm"] = v

    v = _ask_int("私信检查间隔 (秒)", d.get("check_interval", 120), 10, 3600)
    if v is not None: d["check_interval"] = v
    v = _ask_int("每轮最大回复数", d.get("max_replies_per_check", 3), 1, 20)
    if v is not None: d["max_replies_per_check"] = v
    v = _ask_int("单用户 cooldown (分钟)", d.get("private_reply_cooldown_minutes", 3), 1, 1440)
    if v is not None: d["private_reply_cooldown_minutes"] = v
    v = _ask_int("上下文长度 (每用户消息数)", d.get("context_len", 20), 2, 100)
    if v is not None: d["context_len"] = v
    v = _ask_int("只看最近 N 秒的新私信", d.get("only_recent_seconds", 900), 60, 86400)
    if v is not None: d["only_recent_seconds"] = v

    _save(c)


def _menu_follow() -> None:
    c = cfg.load_app_config()
    f = c.setdefault("follow", {})
    print(f"\n{Fore.CYAN}--- 关注 UP 主 (v2.5) ---{Style.RESET_ALL}")

    v = _ask_bool("启用自动关注", f.get("enabled", True))
    if v is not None: f["enabled"] = v
    v = _ask_float("关注概率 auto_follow_prob (0-1)", f.get("auto_follow_prob", 0.08))
    if v is not None: f["auto_follow_prob"] = v
    v = _ask_int("每日关注上限", f.get("max_daily_follows", 3), 0, 100)
    if v is not None: f["max_daily_follows"] = v
    v = _ask_int("单 UP 主 cooldown (分钟)", f.get("cooldown_minutes", 90), 0, 1440)
    if v is not None: f["cooldown_minutes"] = v
    v = _ask_float("关注最低评分 (0-10)", f.get("min_score", 7.0), 0, 10)
    if v is not None: f["min_score"] = v
    v = _ask_float("豁免分数 (single-view 即可关注)", f.get("exceptional_score", 8.5), 0, 10)
    if v is not None: f["exceptional_score"] = v
    v = _ask_int("最少印象数", f.get("min_impressions", 2), 0, 100)
    if v is not None: f["min_impressions"] = v

    _save(c)


def _menu_understand() -> None:
    c = cfg.load_app_config()
    u = c.setdefault("video_understanding", {})
    print(f"\n{Fore.CYAN}--- 视频理解 (v2.4) ---{Style.RESET_ALL}")

    v = _ask_bool("启用", u.get("enabled", True))
    if v is not None: u["enabled"] = v

    modes = ("auto", "subtitle", "whisper", "hybrid")
    cur = u.get("mode", "auto")
    raw = _ask(f"理解模式 ({'/'.join(modes)})", cur)
    if raw and raw in modes:
        u["mode"] = raw

    engines = ("auto", "whisper", "funasr")
    raw = _ask(f"ASR 引擎 ({'/'.join(engines)})", u.get("asr_engine", "auto"))
    if raw and raw in engines:
        u["asr_engine"] = raw

    whisper_models = ("tiny", "base", "small", "medium", "large")
    raw = _ask(f"whisper 模型 ({'/'.join(whisper_models)})", u.get("whisper_model", "base"))
    if raw and raw in whisper_models:
        u["whisper_model"] = raw

    v = _ask_int("摘要最大字符数", u.get("max_summary_chars", 600), 100, 2000)
    if v is not None: u["max_summary_chars"] = v

    _save(c)


def _menu_web() -> None:
    c = cfg.load_app_config()
    w = c.setdefault("web_panel", {})
    print(f"\n{Fore.CYAN}--- Web 面板 (v2.2) ---{Style.RESET_ALL}")

    raw = _ask("绑定地址", w.get("bind", "127.0.0.1"))
    if raw:
        w["bind"] = raw
    v = _ask_int("端口", w.get("port", 8765), 1, 65535)
    if v is not None:
        w["port"] = v

    print(f"\n{Fore.CYAN}提示: 用户名密码首次打开 Web 时在浏览器设（bcrypt 存 Data/web_auth.json）{Style.RESET_ALL}")
    _save(c)


def _menu_status() -> None:
    print(f"\n{Fore.CYAN}--- 限流 & 状态 ---{Style.RESET_ALL}")
    from . import state_view  # late import to avoid cycle
    state_view.print_full_status()


def _menu_cookie() -> None:
    """v5.9 新增 — 设置/查看/清空 B 站 Cookie（指定账号刷取）.

    输入：浏览器 Network → Headers → Cookie 整段字符串。
    解析后写入 Data/bilibili_cookies.json（兼容 symlink → bilibili_agent）。
    """
    while True:
        _hr()
        _info("🍪 B 站 Cookie 设置（v5.9 指定账号）")
        print()
        st = cfg.cookie_status()
        masked = cfg.mask_cookies_dict(st["dict"])
        if st["complete"]:
            _ok(f"已配置完整（{st['count']}/4 个字段）")
        elif st["count"] > 0:
            _warn(f"部分配置（{st['count']}/4 个，缺: {st['missing']}）")
        else:
            _warn("尚未配置")
        for k in ("SESSDATA", "bili_jct", "DedeUserID", "ac_time_value"):
            mark = "✅" if k in st["present"] else "❌"
            print(f"   {mark} {k:14s} = {masked.get(k, '(未配置)')}")
        print()
        print(f"  {Fore.CYAN}[1] 📝 粘贴 Cookie 字符串更新{Style.RESET_ALL}")
        print(f"  {Fore.CYAN}[2] 🗑  清空 Cookie（恢复未登录）{Style.RESET_ALL}")
        print(f"  {Fore.CYAN}[3] 🔍  手动填 4 个字段{Style.RESET_ALL}")
        print("  [b] ↩️  返回上级")
        print()
        try:
            choice = input(f"{Fore.CYAN}请选择: {Style.RESET_ALL}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if choice in ("b", "q", "back", "0"):
            return
        if choice == "1":
            _cookie_paste_flow()
        elif choice == "2":
            _cookie_clear_flow()
        elif choice == "3":
            _cookie_manual_flow()
        else:
            _warn(f"未知选项: {choice}")


def _cookie_paste_flow() -> None:
    """粘贴整段 Cookie 字符串."""
    print()
    _info("粘贴：浏览器 Network → 任意 B 站请求 → Headers → Cookie 整段")
    print(f"  {Fore.CYAN}格式: SESSDATA=xxx; bili_jct=yyy; DedeUserID=123; ac_time_value=zzz{Style.RESET_ALL}")
    try:
        raw = input(f"{Fore.YELLOW}Cookie 字符串 (回车取消): {Style.RESET_ALL}")
    except (EOFError, KeyboardInterrupt):
        return
    raw = raw.strip()
    if not raw:
        _warn("取消"); return
    parsed = cfg.parse_cookie_header(raw)
    if not parsed:
        _warn("未能解析出任何 4 个字段，放弃")
        _info("示例: SESSDATA=4xxx%2C17xxx; bili_jct=e7xx; DedeUserID=39xxx; ac_time_value=73xxx")
        return
    _ok(f"解析到 {len(parsed)}/4 个字段: {list(parsed.keys())}")
    if set(parsed.keys()) != set(("SESSDATA", "bili_jct", "DedeUserID", "ac_time_value")):
        missing = [k for k in ("SESSDATA", "bili_jct", "DedeUserID", "ac_time_value")
                   if k not in parsed]
        _warn(f"缺字段: {missing}")
        try:
            yn = input(f"{Fore.YELLOW}仍要保存？只覆盖已解析字段 (y/n): {Style.RESET_ALL}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if yn not in ("y", "yes"):
            _warn("放弃保存"); return
    # 合并：保留原文件中没解析到的字段（不全覆盖）
    existing = cfg.load_cookies_raw()
    existing.update(parsed)
    if cfg.save_cookies(existing):
        _ok("Cookie 已保存")
        cfg.ensure_dirs()
        # 让 user 立刻看到状态
        new_st = cfg.cookie_status()
        if new_st['complete']:
            tail = "完整"
        else:
            tail = "缺 " + str(new_st['missing'])
        print(f"  当前: {new_st['count']}/4 {tail}")
    else:
        _warn("保存失败，检查 Data/bilibili_cookies.json 权限")


def _cookie_clear_flow() -> None:
    if not cfg.cookie_status()["count"]:
        _warn("当前无 cookie，跳过"); return
    try:
        yn = input(f"{Fore.RED}确认清空所有 cookie？(y/N): {Style.RESET_ALL}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if yn in ("y", "yes"):
        if cfg.save_cookies({}):
            _ok("已清空")
        else:
            _warn("清空失败")


def _cookie_manual_flow() -> None:
    """逐个填 4 个字段（避免粘贴出错时手工修）."""
    print()
    fields = ("SESSDATA", "bili_jct", "DedeUserID", "ac_time_value")
    updates: dict = {}
    for f in fields:
        cur = cfg.load_cookies_raw().get(f, "")
        prompt = f"{Fore.YELLOW}{f}{Style.RESET_ALL} (当前 {cfg.mask_secret(cur) if cur else '未配置'}, 留空跳过): "
        try:
            val = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if val:
            updates[f] = val
    if not updates:
        _warn("未输入"); return
    existing = cfg.load_cookies_raw()
    existing.update(updates)
    if cfg.save_cookies(existing):
        _ok(f"已更新 {len(updates)} 个字段")
    else:
        _warn("保存失败")


def run() -> None:
    """main.py 调这个."""
    try:
        show_main_menu()
    except KeyboardInterrupt:
        print()
        _warn("已取消")