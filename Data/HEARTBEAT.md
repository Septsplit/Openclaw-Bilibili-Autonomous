# OpenClaw HEARTBEAT — bilibili-autonomous v4.9

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

Web 面板: http://127.0.0.1:8765/
skill 路径: ./
OpenAPI: `bin/bilibili-autonomous openapi`
