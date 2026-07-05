#!/bin/bash
# openclaw_comment_gen.sh — 调用 OpenClaw LLM 生成 B站 评论
# 输入：video JSON via stdin  {"bvid":"BV1xxx","title":"...","up":"...","duration":123}
# 输出：评论文本到 stdout

set -e

cd ~/.openclaw/workspace/skills/bilibili-autonomous

python3 << 'PYEOF'
import sys, json, os

# 读取 stdin
input_json = json.load(sys.stdin)
bvid = input_json.get("bvid", "")
title = input_json.get("title", "")[:100]
up = input_json.get("up", "")

# 读 API key（IMA skill 的 key）
key_file = os.path.expanduser("~/.config/ima/api_key")
if not os.path.exists(key_file):
    print("# ERROR: API key not found", file=sys.stderr)
    sys.exit(1)

api_key = open(key_file).read().strip()

# MiniMax OpenAI-compatible API
import urllib.request
req = {
    "model": "MiniMax-M2.7",
    "messages": [
        {"role": "system", "content": "你是一个B站用户，正在看视频。请根据视频标题和UP主名为该视频写一条评论。要求：1）不超过35字 2）真实自然，像真人写的 3）不要重复"},
        {"role": "user", "content": f"视频标题: {title}\nUP主: {up}\n请写一条评论："}
    ],
    "temperature": 0.9,
    "max_tokens": 80
}

data = json.dumps(req).encode()
req_obj = urllib.request.Request(
    "https://api.minimaxi.chat/v1/chat/completions",
    data=data,
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req_obj, timeout=30) as resp:
        result = json.load(resp)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        # 清理并截断到35字
        text = content.replace("*", "").replace("**", "").strip()[:35]
        print(text)
except Exception as e:
    print(f"# ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
