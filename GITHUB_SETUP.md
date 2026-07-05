# GitHub Setup — bilibili-autonomous v5.9

这是从本地仓库 **导出的快照**（v5.9）。已对凭据/隐私字段做了 sanitize 处理 — 不会随仓库外泄真实账号/API key。
本仓库本身只放骨架与默认配置，**真实 `Data/bilibili_cookies.json` 等应当按下面的指引重新填**。

---

## 1. 这个仓库是做什么的

B 站互动原子工具集（点赞/投币/收藏/评论/弹幕/私信/关注 + 视频理解 + Web 面板），
供 OpenClaw AI 调用 — OpenClaw 决策，skill 执行。**不内置 AI**。

## 2. 一键启动

```bash
cd bilibili-autonomous
./bin/bilibili-autonomous start       # 起 Web (127.0.0.1:8765) + 写 HEARTBEAT.md
# 或单独开 CLI 配置:
./bin/bilibili-autonomous configure   # 进入交互配置器
```

直接看子命令清单:

```bash
./bin/bilibili-autonomous --help
```

## 3. ⚠ 需要填的占位符（导出时已脱敏）

| 文件 | 字段 | 处理 |
|---|---|---|
| `Data/bilibili_cookies.json` | `SESSDATA` / `bili_jct` / `DedeUserID` / `ac_time_value` | `<PLACEHOLDER ...>` |
| `Data/web_auth.json` | `user` / `hash` | 首次开 Web 时在浏览器 setup |
| `Data/web_settings.json` | `secret_key` | 留空，Web 首次启动自动生成 |
| `Data/config.json` | `web.password` / `bilibili.refresh_token` | `<PLACEHOLDER>` |
| `Data/config.json` | 整个 `api.*` 段 | 删了（这里不需要 LLM） |

填法：

### 🍪 B 站 Cookie（最关键 — 没这个登录不上）

1. Chrome 登录 B 站 → 打开任意视频 → F12 → Network → 点任意 B 站请求
2. **Request Headers → Cookie** 那一整段，**Ctrl+C 全复制**
3. 回到本项目：
   ```bash
   ./bin/bilibili-autonomous configure   # 选 [C] Cookie 设置 → 选 [1] 粘贴
   ```
   或 Web 面板 → 「🍪 B 站 Cookie」section → 粘贴 → 保存

### 🔐 Web 登录

`bin/bilibili-autonomous start` 后浏览器开 `http://127.0.0.1:8765`，
按页面提示输用户名密码（≥6 位）。首次会自动跳到 setup。

### 🔑 API key（如需要调 LLM）

v5 起本 skill **不再内置 LLM**，按官方架构由 OpenClaw 自己管 LLM。
如仍有 LLM 调用需求，在 `Data/config.json` 加 `api.unified_api_key`：
```json
"api": {
  "unified_api_key": "sk-...",
  "unified_base_url": "https://api.example.com/v1",
  "model_brain": "...",
  "model_vision": "..."
}
```

## 4. .venv

仓库提交**不包含 .venv**（见 `.gitignore`）。新人 clone 后需要自己创建：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 5. 严禁 — 即使推到了私有仓库也别这么干

- ❌ 把填了真实 cookie 的 `Data/bilibili_cookies.json` commit 进 Git
- ❌ 把 `Data/web_auth.json` (admin bcrypt 哈希) commit
- ❌ 把 `Data/web_settings.json` 的 `secret_key` 提交（已留空防意外）
- ❌ 把 LLM API key 写进 `Data/config.json` 提交

`.gitignore` 已覆盖上面所有路径。如果临时需要把 Data/ 也提交，先用 `git status` 确认没有泄漏。

## 6. 子命令速查

```
like/coin/favorite/comment/danmaku/follow/unfollow/dm.send
feed/video/subtitles/user/gate/thresholds/understand/understand5
follow.status/follow.history/follow.inactive_scan
status/actions.list/actions.get/tools-log/configure/serve/openapi/start
energy/mood/knowledge/energy-schedule/watch
```

完整 OpenAPI：

```bash
./bin/bilibili-autonomous openapi
```

## 7. 配套

- OpenClaw 大脑 → 调本 skill 执行原子动作（HEARTBEAT.md 模板：`./bin/bilibili-autonomous start` 后写到 `Data/HEARTBEAT.md`）
- Web 面板 → http://127.0.0.1:8765

— v5.9 export (Claude Code)
