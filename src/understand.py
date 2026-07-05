"""src/understand.py — v3 视频元信息 + ASR 转写工具.

v3 重构：只提供**工具**供 OpenClaw 调用，不自己生成 AI 内容。
- get_video_meta(bvid) → 拿视频元信息（标题/UP/时长/简介）
- get_subtitles(bvid) → 拿 B 站原生字幕
- whisper_transcribe(bvid) → 本地 whisper 转写（需要 ffmpeg）
- 总结/评分/AI 评论全部由 OpenClaw 自己负责（不在本 skill 内）

OpenClaw 调用模式：
    from src.understand import get_video_meta, get_subtitles, whisper_transcribe
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from . import actions_log, bapi as bapi_mod, config as cfg


# ===== 字幕选择 =====

def pick_subtitle(subs: list[dict], priority: list[str]) -> dict | None:
    """按优先级选第一个非空字幕.

    subs 格式: [{"lan": "ai-zh", "lan_doc": "...", "content": "...", ...}, ...]
    """
    if not subs:
        return None
    for want in priority:
        for s in subs:
            if s.get("lan") == want and s.get("content"):
                return s
    for s in subs:
        if s.get("content"):
            return s
    return None


# ===== Whisper 模型懒加载单例缓存 =====

_WHISPER_INSTANCES: dict[str, tuple] = {}


def _get_whisper_model(model_name: str) -> tuple:
    """v3: 按 model_name 缓存 whisper 模型（medium/large 加载慢）。"""
    if model_name in _WHISPER_INSTANCES:
        model_obj, _ = _WHISPER_INSTANCES[model_name]
        return model_obj, False
    import whisper  # type: ignore
    model_obj = whisper.load_model(model_name)
    _WHISPER_INSTANCES[model_name] = (model_obj, True)
    return model_obj, True


async def whisper_transcribe(bvid: str, model_name: str = "base") -> str | None:
    """v3 工具函数：用本地 whisper 转写某个视频的音频。

    OpenClaw 调用示例：
        text = await src.understand.whisper_transcribe("BV1xx", "base")
    """
    if not shutil.which("ffmpeg"):
        print("   [ERROR] ffmpeg 未安装（brew install ffmpeg）", file=sys.stderr)
        return None

    cfg_understand = cfg.load_app_config().get("video_understanding", {})

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        try:
            # 拿直链（旧 bilibili_api 用 get_download_url）
            import asyncio
            from bilibili_api import video
            v = video.Video(bvid=bvid)
            try:
                url_obj = await v.get_download_url(aid=v.get_aid(), qn=32)
            except (TypeError, AttributeError):
                url_obj = await v.get_download_url(quality=32)
            if not url_obj:
                # 拿不到链接时返回 None（不抛异常）
                return None
            # url_obj 可能是 dict 或 str，提取第一个 URL
            url = url_obj if isinstance(url_obj, str) else (
                url_obj.get("url") or url_obj.get("data", {}).get("durl", [{}])[0].get("url", "")
            )
            if not url:
                return None

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw_audio = td_path / "raw.bin"
                raw_audio.write_bytes(resp.content)
            wav_path = td_path / "audio.wav"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(raw_audio),
                "-ar", "16000", "-ac", "1",
                str(wav_path),
            ], check=True, capture_output=True)

            model, was_first = _get_whisper_model(model_name)
            if was_first:
                actions_log.append_operation_log({
                    "action": "whisper_load",
                    "model": model_name,
                    "bvid": bvid,
                })
            # whisper 是同步阻塞调用，放到 thread pool
            loop = asyncio.get_event_loop()
            tr = await loop.run_in_executor(
                None,
                lambda: model.transcribe(str(wav_path), language="zh", fp16=False),
            )
            text = (tr.get("text") or "").strip()
            actions_log.append_operation_log({
                "action": "whisper_transcribe",
                "bvid": bvid,
                "chars": len(text),
            })
            return text
        except Exception as e:
            print(f"   [WARN] whisper transcribe 失败: {e}", file=sys.stderr)
            return None
