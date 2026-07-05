---
name: bilibili-autonomous
description: B 站 API 工具集 v4 (OpenClaw AI 大脑调用). 精力值系统 + 关键词收藏 + 自动 watch. Trigger on "B站 API 工具", "B站 CLI", "B站自动刷", "B站互动", "bilibili-autonomous v4".
---

# bilibili-autonomous v4

**v4 = v3 + 用户系统（精力值 + 关键词收藏 + watch + Web 美化 + CLI 双模式）**

**v4.1（基于 OpenClaw 19:54 反馈的 patch）：**
- **移除** `watch --comment-cmd` / `--danmaku-cmd`（OpenClaw 在 HEARTBEAT 自己生成 text 再调 `comment --text X`）
- SKILL.md 的 HEARTBEAT 章节完全对齐 OpenClaw 19:54 描述（OpenClaw = AI 大脑，skill = 工具集）
- 推荐 OpenClaw 集成模式：用 jq 解析每个工具的 JSON 输出，自行决策

OpenClaw 是 AI 大脑，本 skill 是 B 站 API 工具集。OpenClaw 调这里的原子工具 + watch 高级工具。

## 给 OpenClaw 的快速说明

```bash
# 找本 skill 的工具清单（OpenAPI）
bin/bilibili-autonomous openapi

# v4.1 精力值管理（OpenClaw heartbeat 必查项）
bin/bilibili-autonomous energy status
bin/bilibili-autonomous energy consume --n 1
bin/bilibili-autonomous energy set-max 30

# v4.6 watch 高级工具（OpenClaw 不推荐使用 — 只能自动 like/coin/favorite；comment/danmaku OpenClaw 自己调）
bin/bilibili-autonomous watch --count 5 --no-energy  # 只刷自动动作，OpenClaw 自己处理评论

# v4.2 关键词收藏（OpenClaw 调 favorite 时若提供 score 自动判断）
bin/bilibili-autonomous favorite <bvid> --auto-check --score 8.5

# 原子动作（v3 保留）
bin/bilibili-autonomous like <bvid>
bin/bilibili-autonomous coin <bvid> --num 1
bin/bilibili-autonomous comment <bvid> --text "AI 写的"
bin/bilibili-autonomous danmaku <bvid> --text "弹幕"
bin/bilibili-autonomous follow <uid>
bin/bilibili-autonomous unfollow <uid>
bin/bilibili-autonomous dm.send <uid> --text "私信"

# 工具查询
bin/bilibili-autonomous feed --limit 10
bin/bilibili-autonomous video <bvid>
bin/bilibili-autonomous subtitles <bvid>
bin/bilibili-autonomous gate 8.5 coin

# 阈值 + 状态
bin/bilibili-autonomous thresholds
bin/bilibili-autonomous status

# Web 面板：bin/bilibili-autonomous serve 默认 8765
```

## OpenClaw Heartbeat 集成模式（v4.1 — 按 OpenClaw 19:54 改）

**核心原则：** OpenClaw = AI 大脑。bilibili-autonomous = 纯执行工具。

```
HEARTBEAT 触发（OpenClaw 内部）
    ↓
1️⃣ OpenClaw 调 bin/bilibili-autonomous feed --limit 10 拿候选视频
    ↓
2️⃣ OpenClaw 用自己的 LLM 给每个视频打分（0-10）+ 决定要不要点赞/投币/收藏/评论
    ↓
3️⃣ OpenClaw 用自己的 LLM 生成评论/弹幕/私信内容
    ↓
4️⃣ OpenClaw 调 bilibili-autonomous 的原子 CLI 工具执行动作
    ↓
5️⃣ OpenClaw 调 energy consume --n 1 消耗精力
    ↓
6️⃣ OpenClaw 把每次结果写到自己的 memory
```

### OpenClaw Bash 集成示例（参考，不是限制 OpenClaw 怎么调）

```bash
#!/bin/bash
# 这是给 OpenClaw 一个参考脚本。OpenClaw 实际可以用任意方式调。

# 1. 精力检查（HEARTBEAT 一进来必查）
ES=$(bin/bilibili-autonomous energy status | tail -1)
EXHAUSTED=$(echo "$ES" | jq -r '.exhausted_until')
if [ "$EXHAUSTED" != "null" ] && [ -n "$EXHAUSTED" ]; then
    echo "🔋 精力耗尽，下次心跳再来"; exit 0
fi

# 2. 拿推荐流
FEED_JSON=$(bin/bilibili-autonomous feed --limit 10 | tail -1)
ITEMS=$(echo "$FEED_JSON" | jq -c '.items[]')

# 3. 对每个视频：用 OpenClaw LLM 评分
echo "$ITEMS" | while read ITEM; do
    BVID=$(echo "$ITEM" | jq -r '.bvid')
    TITLE=$(echo "$ITEM" | jq -r '.title')
    UP=$(echo "$ITEM" | jq -r '.up')

    # OpenClaw 自己的 LLM 评分（不是 skill 内的 AI）
    SCORE=$(openclaw_llm_score "$TITLE" "$UP")

    # 4. gate 工具查阈值
    COIN_OK=$(bin/bilibili-autonomous gate "$SCORE" coin | jq -r '.passed')
    FAV_OK=$(bin/bilibili-autonomous gate "$SCORE" favorite | jq -r '.passed')

    [ "$COIN_OK" = "true" ] && bin/bilibili-autonomous coin "$BVID"

    # 5. 调 favorite（带 score 让 skill 自动判断）
    [ "$FAV_OK" = "true" ] && bin/bilibili-autonomous favorite "$BVID" --auto-check --score "$SCORE"

    # 6. OpenClaw LLM 生成评论
    if bin/bilibili-autonomous gate "$SCORE" comment | jq -r '.passed' | grep -q true; then
        COMMENT=$(openclaw_llm_comment "$TITLE" "$UP")
        [ -n "$COMMENT" ] && bin/bilibili-autonomous comment "$BVID" --text "$COMMENT"
    fi

    # 7. 消耗精力
    bin/bilibili-autonomous energy consume --n 1
done
```

### 关键点（OpenClaw 19:54 强调）

- **OpenClaw 自己生成 text**，不再用 skill 接外部 LLM 脚本（`watch --comment-cmd` 已废弃）
- **HEARTBEAT 由 OpenClaw 触发**，skill 不跑 daemon；只在被调用时工作
- **原子 CLI 全暴露**：OpenClaw 可以临时 `bin/... like <bvid>` 立刻点赞，无需走 watch
- **精力管理是 skill 的事**：OpenClaw 只调 `energy consume`，不需要自己算剩余


## v4 新增功能

### 🔋 v4.1 精力值系统
- `Data/energy.json` 持久化 {max_energy, current_energy, disabled, refill_seconds, exhausted_until}
- 默认 20，每视频消耗 1；≤0 时拒绝 + 设置 cooldown_until = now + 30 min
- 30 min 后自动恢复（refill_seconds 可配）
- 用户可关闭（`set-disabled on`=无限精力）
- CLI: `energy status|consume|set-max|disabled|refill`

### 🔖 v4.2 关键词收藏
- `config.favorite.keywords[]` 配置关键词列表
- `bin/bilibili-autonomous favorite <bvid> --auto-check --score X` 自动判断
- 匹配模式：`any`（任一含）/ `all`（全部含）
- 与评分结合：`score ≥ archive_min` 自动也收藏

### 🛡 v4.3 总开关 + 概率
- `config.autonomy.*` 主块收纳所有 enable + prob 字段
- v4 新增：`enable_high_quality_archive` / `enable_proactive_coin_like`
- CLI 快速配置里一行切换 on/off

### 🔧 v4.4 CLI 双模式
- `Q` 快速配置：精力值 / 关键词 / 总开关 / 收藏
- `A` 全方位配置：所有阈值 / 概率 / 安全 / 私信 / 关注 / 视频理解 / Web

### 🌐 v4.5 Web 美化
- 顶部 stat cards（观看/点赞/投币/评论/弹幕/收藏）
- 精力值独立卡片显示
- 配置按 SECTION_META 排序分组
- 新增"OpenAPI 工具清单"面板（OpenClaw 一眼看所有工具）
- 状态卡片用 `.stat-card` 风格

### 🎬 v4.6 watch 高级工具（OpenClaw 不推荐使用 — 仅供手动测试）

- `bin/bilibili-autonomous watch --count N`
- 内部流程：拉 feed → 按 autonomy.prob_* 随机动作 → 调原子 → 消耗精力 → 长间隔
- `--no-energy` 跳过精力检查
- `--long-interval N` 每 N 个视频穿插长间隔 (30-180s)
- **没有 `--comment-cmd` / `--danmaku-cmd`**（v4.1 移除，OpenClaw 19:54 说废弃）
- 注意：watch 只对 `like`/`coin`/`favorite` 自动执行；**comment/danmaku 跳过**因为 watch 不知道怎么生成 text
- OpenClaw 在 HEARTBEAT 自己生成 text 后用 `bin/... comment --text X`

## 配置字段（11 个 section）

| Section | 字段 |
|---|---|
| `behavior` | comment_mode, min/max_reply_delay_seconds |
| `interaction` | max_coins_daily, max_danmaku_daily, max_comments_daily, fav_threshold |
| `danmaku` | enabled, send_prob, max_daily_send |
| `reply_safety` | enabled, block_on_outgoing, blocked_keywords, political_video_keywords |
| `dm` | enabled, auto_reply, enable_proactive_dm, check_interval, max_replies_per_check, only_recent_seconds, private_reply_cooldown_minutes, context_len, proactive_prob, proactive_targets |
| `follow` | enabled, auto_follow_prob, max_daily_follows, cooldown_minutes, min_score, min_impressions, exceptional_score, unfollow_inactive_days |
| `scoring` | coin_min, favorite_min, comment_min, follow_min, archive_min, understand_min, follow_exceptional, follow_min_impressions |
| `energy` ⭐v4.1 | max_energy (20), refill_seconds (1800), disabled (false) |
| `favorite` ⭐v4.2 | enabled, keywords[], match_mode, min_score, auto_on_score |
| `autonomy` ⭐v4.3 | enabled, enable_*/prob_* (9 个动作) |
| `web_panel` | bind, port, secret_key |

## 用户介入方式

| 用户命令 | OpenClaw / skill 反应 |
|---|---|
| "开始刷B站" | OpenClaw heartbeat 启动 watch |
| "停止刷B站" | OpenClaw heartbeat 暂停，或用 `energy disabled on` |
| "分析理解某视频 BV1xxx" | OpenClaw 调 `understand <bvid>` |
| "收藏某视频 BV1xxx" | 用户调 `favorite` 或 OpenClaw 自动 |
| "看日志" | `tools-log` CLI / Web /tools-log API |

## Files

```
bin/bilibili-autonomous                   shell wrapper
src/
├── main.py                               v4 原子 CLI + watch + energy + openapi
├── bapi.py                               B 站 API
├── throttle.py                           -799 限流
├── safety.py                             敏感词过滤
├── config.py                             v4 DEFAULTS (11 sections) + write_energy_config
├── human.py                              真人节奏工具
├── understand.py                         v3 工具: whisper + pick_subtitle
├── scorer.py                             v3 阈值门控 (Thresholds + gate)
├── actions_log.py                        操作日志
├── archive.py                            归档
├── follow.py                             关注 + scan_inactive
├── dm.py                                 v3 DM (reply_provider from OpenClaw)
├── state_view.py                         综合状态
├── web_panel.py                          Flask + 4 个 v3 路由 + 4 个 v4 显示优化
├── cli_config.py                         v4 双模式菜单（快速/全方位）
├── energy.py ⭐v4.1                       精力值模块
└── favorite_keys.py ⭐v4.2               关键词收藏模块
templates/web.html                        v4 美化 (304→460 行)
Data/
├── cookies/config.json (软链)
├── energy.json ⭐v4.1                    精力状态
├── state.json                            配额 + 上次运行
├── follow_state.json                     关注历史
├── web_auth.json                         bcrypt 用户名密码
├── logs/operations-YYYY-MM-DD.jsonl     工具调用历史
└── actions/{comments,danmaku,dms,follows,understandings,highlights}/
```

## Dependencies

```txt
bilibili-api-python>=17.0.0
colorama>=0.4.6
flask>=3.0
bcrypt>=5.0
openai-whisper  # 可选：调 understand --mode whisper 时用
```

## Notes

- ⚠ OpenClaw 调用前请先 `bin/bilibili-autonomous openapi` 拿工具清单
- 精力值关闭模式（disabled=true）= 无限，不需要 watch 暂停
- Web 密码首次访问设，bcrypt 存 `Data/web_auth.json`
- 配置改了 config.json 后下次 watch / OpenClaw 调 自动生效
