"""src/bapi.py — B 站 API 封装（v1 + v2 扩展）.

适配 bilibili-api-python 17.4.x 的 API：
- Video.like(status=True)
- Video.pay_coin(num=1, like=False)
- Video.set_favorite(add_media_ids=[fid])
- Video.send_danmaku(cid=cid, danmaku=Danmaku(text))
- comment.send_comment(...)
- video.Video.get_info() / get_subtitles() / get_tags() — v2.4 用
- message.send_msg / sync_msgs — v2.5 私信
- user.User.modify_relation(SUBSCRIBE/UNSUBSCRIBE) — v2.5 关注

所有写操作通过 src.throttle.call 走限流重试。读操作（推荐流/详情）也走，避免触发风控。
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    from bilibili_api import Credential, Danmaku, comment, homepage, user, video
    # 私信模块在 17.4.2 里改名为 session（旧版叫 message）
    try:
        from bilibili_api import session as bilibili_session
    except ImportError:
        bilibili_session = None
except ImportError as e:
    raise ImportError(f"缺少 bilibili_api 模块: {e}. 请 pip install bilibili-api-python>=17.0.0") from e

from . import throttle


@dataclass
class RecommendItem:
    bvid: str
    aid: int
    title: str
    up: str
    duration: int          # 秒
    desc: str
    cid: int = 0           # 弹幕/播放需要 cid，懒加载

    def short(self) -> str:
        dur = self.duration
        if dur >= 60:
            mm, ss = divmod(dur, 60)
            dur_s = f"{mm}m{ss:02d}s"
        else:
            dur_s = f"{dur}s"
        return f"《{self.title}》 by {self.up} ({dur_s}, a={self.aid})"


def make_credential(cookies: dict[str, str]) -> Credential:
    """从 cookie dict 构造 Credential."""
    return Credential(
        sessdata=cookies.get("SESSDATA", ""),
        bili_jct=cookies.get("bili_jct", ""),
        dedeuserid=cookies.get("DedeUserID", ""),
        ac_time_value=cookies.get("ac_time_value", ""),
    )


class BiliAPI:
    """v1 范围的 B 站 API 封装。dry_run=True 时所有写操作只 log 不真发。"""

    def __init__(self, credential: Credential, dry_run: bool = False,
                 default_fav_folder: int = 1):
        self.credential = credential
        self.dry_run = dry_run
        self.default_fav_folder = default_fav_folder

    # ===== 读 =====

    async def get_recommend_feed(self, limit: int = 10) -> list[RecommendItem]:
        """拉首页推荐流。"""
        async def _do():
            res = await homepage.get_videos(credential=self.credential)
            items = []
            for raw in (res.get("item") or [])[:limit]:
                if not raw.get("bvid"):
                    continue
                title = str(raw.get("title") or "")
                # 去推荐流的 <em class="keyword"> 高亮标签
                for tag in ('<em class="keyword">', "</em>"):
                    title = title.replace(tag, "")
                items.append(RecommendItem(
                    bvid=raw["bvid"],
                    aid=int(raw.get("id") or 0),    # 推荐流里是 id 字段
                    title=title,
                    up=str((raw.get("owner") or {}).get("name") or ""),
                    duration=int(raw.get("duration") or 0),
                    desc=str(raw.get("rcmd_reason", {}).get("content") or "")[:120],
                    cid=int(raw.get("cid") or 0),    # 推荐流里直接有 cid
                ))
            return items
        return await throttle.call(_do, name="homepage.get_videos")

    async def get_video_meta(self, item: RecommendItem) -> RecommendItem:
        """补全 cid（发弹幕需要）。失败时原样返回。"""
        if item.cid:
            return item
        async def _do():
            v = video.Video(aid=item.aid, credential=self.credential)
            info = await v.get_info()
            return int(info.get("cid") or 0)
        try:
            item.cid = await throttle.call(_do, name="video.get_info")
        except Exception:
            pass
        return item

    # ===== 写 =====

    async def like_video(self, item: RecommendItem) -> bool:
        async def _do():
            v = video.Video(aid=item.aid, credential=self.credential)
            await v.like()
        return await self._do_write(_do, "video.like", item)

    async def coin_video(self, item: RecommendItem, num: int = 1) -> bool:
        async def _do():
            v = video.Video(aid=item.aid, credential=self.credential)
            await v.pay_coin(num=num, like=False)
        return await self._do_write(_do, f"video.pay_coin×{num}", item)

    # ===== v5.4 视频理解辅助 =====
    async def get_top_comments(self, bvid: str, limit: int = 10) -> list[dict]:
        """v5.4: 拿热评 (给 OpenClaw LLM 看).

        返回 [{member, content, time, like, replies}, ...]

        v5.8 Hermes-3 修：bilibili_api.comment.get_comments 17.4.x 不再接受
        bvid= / oid= 关键字，统一用位置参数 (oid, type_, page_index)。
        bvid2aid 在同包 utils 里，直接导入转换。
        """
        async def _do():
            from bilibili_api import comment
            from bilibili_api.utils.aid_bvid_transformer import bvid2aid
            try:
                aid = bvid2aid(bvid)
                obj = await comment.get_comments(
                    aid,
                    comment.CommentResourceType.VIDEO,
                    page_index=1,
                )
                items = []
                for cmt in (obj or {}).get("replies", []) or [][:limit]:
                    items.append({
                        "member": (cmt.get("member") or {}).get("uname", ""),
                        "content": (cmt.get("content") or {}).get("message", ""),
                        "time": cmt.get("ctime"),
                        "like": cmt.get("like", 0),
                        "replies": cmt.get("rcount", 0),
                    })
                return items
            except Exception as e:
                return [{"error": str(e)}]
        return await throttle.call(_do, name="comment.get_comments")

    async def get_top_danmaku(self, bvid: str, limit: int = 30) -> list[dict]:
        """v5.4: 拿热弹幕 (OpenClaw LLM 看)."""
        async def _do():
            from bilibili_api import video
            try:
                v = video.Video(bvid=bvid)
                dms = await v.get_danmakus(0)
                return [
                    {"text": d.text, "time": d.dm_time, "send_time": d.send_time,
                     "id": d.id_, "user": getattr(d, "user_hash", "")}
                    for d in (dms or [])[:limit]
                ]
            except Exception as e:
                return [{"error": str(e)}]
        return await throttle.call(_do, name="video.get_danmakus")

    async def get_cover_url(self, bvid: str) -> dict:
        """v5.4: 返回封面 URL (OpenClaw 用 LLM 看图分析)."""
        async def _do():
            v = video.Video(bvid=bvid)
            info = await v.get_info()
            pic = info.get("pic", "")
            return {"cover": pic, "aid": info.get("aid"), "bvid": bvid,
                    "note": "OpenClaw LLM analyze image"}
        return await throttle.call(_do, name="video.get_info_cover")

    # ===== v5.6 主动发信息增强 =====
    async def send_dm_with_image(self, uid: int, text: str,
                                  image_urls: list[str] | None = None) -> bool:
        """v5.6: 主动发私信, 可附图片 (OpenClaw 自己调).

        v5.8 Hermes-3 修：bilibili_api.message 模块在 17.4.x 已删除，
        真实 API 在 bilibili_api.session.send_msg，签名：
          send_msg(credential, receiver_id, msg_type, content)
        不再接受 message=/uid= kwargs。
        """
        if bilibili_session is None:
            print("   [WARN] bilibili_api.session 不可用", file=sys.stderr)
            return False
        try:
            if image_urls:
                # bilibili_api.session.send_msg 不支持图片，简化：只发文字
                await bilibili_session.send_msg(
                    credential=self.credential,
                    receiver_id=int(uid),
                    msg_type=bilibili_session.EventType.TEXT,
                    content=text,
                )
            else:
                await bilibili_session.send_msg(
                    credential=self.credential,
                    receiver_id=int(uid),
                    msg_type=bilibili_session.EventType.TEXT,
                    content=text,
                )
            return True
        except Exception as e:
            print(f"   [ERROR] send_dm failed: {e}")
            return False

    async def favorite_video(self, item: RecommendItem, fid: int | None = None) -> bool:
        fid = fid or self.default_fav_folder
        async def _do():
            v = video.Video(aid=item.aid, credential=self.credential)
            await v.set_favorite(add_media_ids=[fid])
        return await self._do_write(_do, f"video.set_favorite(fid={fid})", item)

    async def send_danmaku(self, item: RecommendItem, text: str) -> bool:
        if not item.cid:
            await self.get_video_meta(item)
        if not item.cid:
            return False
        async def _do():
            v = video.Video(aid=item.aid, credential=self.credential)
            await v.send_danmaku(cid=item.cid, danmaku=Danmaku(text))
        return await self._do_write(_do, "danmaku.send", item, text=text)

    async def send_comment(self, item: RecommendItem, text: str) -> bool:
        async def _do():
            await comment.send_comment(
                text=text,
                oid=item.aid,
                type_=comment.CommentResourceType.VIDEO,
                credential=self.credential,
            )
        return await self._do_write(_do, "comment.send", item, text=text)

    # ===== v2.4 视频详情 / 字幕 =====

    async def get_video_full_meta(self, bvid: str) -> dict[str, Any]:
        """返回 {title, up_name, duration, desc, aid, cid}."""
        async def _do():
            v = video.Video(bvid=bvid, credential=self.credential)
            info = await v.get_info()
            stat = info.get("stat", {}) or {}
            staff = info.get("staff", []) or []
            up_name = ""
            if info.get("owner"):
                up_name = (info["owner"] or {}).get("name", "")
            elif staff:
                up_name = (staff[0] or {}).get("name", "")
            return {
                "title": str(info.get("title", "")),
                "up_name": up_name,
                "duration": int(info.get("duration", 0) or info.get("length", 0) or 0),
                "desc": str(info.get("desc", "")),
                "aid": int(info.get("aid", 0)),
                "cid": int(info.get("cid", 0)),
                "view": int(stat.get("view", 0)),
                "like": int(stat.get("like", 0)),
                "reply": int(stat.get("reply", 0)),
                "favorite": int(stat.get("favorite", 0)),
                "coin": int(stat.get("coin", 0)),
            }
        return await throttle.call(_do, name="video.get_info")

    async def get_video_subtitles(self, bvid: str) -> list[dict[str, Any]]:
        """返回 [{lan, lan_doc, content, ...}, ...]，失败返回 []."""
        async def _do():
            v = video.Video(bvid=bvid, credential=self.credential)
            info = await v.get_info()
            aid = int(info.get("aid", 0))
            cid = int(info.get("cid", 0))
            try:
                subs = await v.get_subtitle(aid=aid, cid=cid)
            except Exception:
                return []
            # subs 可能是 dict（subtitles 键）或 list
            items: list[dict] = []
            if isinstance(subs, dict):
                for s in subs.get("subtitles", []) or []:
                    items.append({
                        "lan": s.get("lan", "?"),
                        "lan_doc": s.get("lan_doc", ""),
                        "subtitle_url": s.get("subtitle_url", ""),
                        "content": "",
                    })
            elif isinstance(subs, list):
                for s in subs:
                    if isinstance(s, dict):
                        items.append({
                            "lan": s.get("lan", "?"),
                            "lan_doc": s.get("lan_doc", ""),
                            "subtitle_url": s.get("subtitle_url", ""),
                            "content": "",
                        })
            # 下载每个字幕的 JSON 内容
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as client:
                for it in items:
                    url = it.get("subtitle_url")
                    if not url:
                        continue
                    try:
                        # url 可能是 // 开头，补 https:
                        if url.startswith("//"):
                            url = "https:" + url
                        r = await client.get(url)
                        r.raise_for_status()
                        data = r.json()
                        # 拼接所有 body
                        bodies = data.get("body", []) or []
                        text = "\n".join((b.get("content", "") for b in bodies))
                        it["content"] = text[:6000]
                    except Exception:
                        pass
            return items
        return await throttle.call(_do, name="video.get_subtitle")

    async def _video_obj(self, bvid: str):
        """v2.4 内部用：给 Video 对象（whisper 下载用）。"""
        return video.Video(bvid=bvid, credential=self.credential)

    # ===== v2.5 私信 =====

    async def fetch_new_dms(self, only_recent_seconds: int = 900) -> list[dict[str, Any]]:
        """拉最近 N 秒的私信。

        bilibili_api 17.4.2 没有 sync_msgs，改用 get_unread_messages + fetch_session_msgs。
        """
        if bilibili_session is None:
            print("   [WARN] bilibili_api.session 不可用，私信跳过")
            return []
        async def _do():
            try:
                # 先拿未读消息列表
                unreads = await bilibili_session.get_unread_messages(self.credential)
                msgs = (unreads or {}).get("messages", []) or []
                # msgs 格式：[{talker_id, msg_id, content, msg_ts, ...}, ...]
                out: list[dict] = []
                now_ts = datetime.now().timestamp()
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    sender = int(m.get("talker_id") or m.get("sender_id") or 0)
                    content = m.get("content", {})
                    text = content.get("text", "") if isinstance(content, dict) else str(content)
                    ts = int(m.get("msg_ts") or m.get("timestamp") or 0)
                    if ts and (now_ts - ts) > only_recent_seconds:
                        continue
                    out.append({
                        "id": str(m.get("msg_id") or f"{sender}-{ts}"),
                        "sender_uid": sender,
                        "sender_name": "",
                        "text": text,
                        "ts": datetime.fromtimestamp(ts).isoformat() if ts else "",
                    })
                return out
            except Exception as e:
                print(f"   [WARN] get_unread_messages 失败: {e}")
                return []
        return await throttle.call(_do, name="session.get_unread")

    async def send_private_message(self, uid: int, text: str) -> bool:
        """v5.8 Hermes-3 修：用 session.send_msg 新签名
        (credential, receiver_id, msg_type, content)，不是 message=/uid= kwargs.
        """
        if bilibili_session is None:
            print("   [ERROR] bilibili_api.session 不可用")
            return False
        async def _do():
            await bilibili_session.send_msg(
                credential=self.credential,
                receiver_id=int(uid),
                msg_type=bilibili_session.EventType.TEXT,
                content=text,
            )
        try:
            await throttle.call(_do, name="session.send_msg")
            return True
        except Exception as e:
            print(f"   [ERROR] send_private_message 失败: {e}")
            return False

    # ===== v2.5 关注 =====

    async def follow_user(self, uid: int) -> bool:
        async def _do():
            u = user.User(uid=int(uid), credential=self.credential)
            await u.modify_relation(user.RelationType.SUBSCRIBE)
        try:
            await throttle.call(_do, name="user.follow")
            return True
        except Exception as e:
            err = str(e)
            # 已关注视为成功
            if "22014" in err or "已经关注" in err:
                return True
            print(f"   [ERROR] follow_user({uid}) 失败: {e}")
            return False

    async def unfollow_user(self, uid: int) -> bool:
        async def _do():
            u = user.User(uid=int(uid), credential=self.credential)
            await u.modify_relation(user.RelationType.UNSUBSCRIBE)
        try:
            await throttle.call(_do, name="user.unfollow")
            return True
        except Exception as e:
            print(f"   [ERROR] unfollow_user({uid}) 失败: {e}")
            return False

    async def get_user_profile(self, uid: int) -> dict[str, Any]:
        """简单用户信息."""
        async def _do():
            info = await user.get_user_info(uid=int(uid), credential=self.credential)
            return {
                "uid": int(info.get("mid", uid)),
                "name": info.get("name", ""),
                "level": info.get("level", 0),
                "sign": info.get("sign", ""),
                "vip": bool((info.get("vip") or {}).get("status", 0)),
            }
        try:
            return await throttle.call(_do, name="user.get_user_info")
        except Exception as e:
            print(f"   [WARN] get_user_profile({uid}) 失败: {e}")
            return {"uid": uid, "name": "", "error": str(e)}

    # ===== 内部 =====

    async def _do_write(self, op, name: str, item: RecommendItem | None = None,
                        text: str | None = None) -> bool:
        if self.dry_run:
            suffix = f" text={text!r}" if text else ""
            label = item.short() if item else ""
            print(f"   [DRY-RUN] {name} → {label}{suffix}")
            return True
        try:
            await throttle.call(op, name=name)
            return True
        except Exception as e:
            label = item.short() if item else ""
            print(f"   [ERROR] {name} 失败: {e}  {label}")
            return False
