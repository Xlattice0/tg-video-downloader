# -*- coding: utf-8 -*-
"""核心逻辑测试：不登录 Telegram，也不碰本机会话。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon.errors import FloodWaitError
from telethon.tl.types import (
    Document,
    PeerChannel,
    DocumentAttributeAnimated,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageExtendedMediaPreview,
    MessageMediaDocument,
    MessageMediaPaidMedia,
    MessageMediaPhoto,
    MessageMediaUnsupported,
    MessageMediaVideoStream,
    MessageMediaWebPage,
    RestrictionReason,
    WebPage,
)

from tgcore import (
    DELETE_DIALOG_TITLE,
    DialogItem,
    DownloadQueue,
    INTERRUPTED_TASK_HINT,
    UI_MODE_CLASSIC,
    UI_MODE_PROFESSIONAL,
    UserFacingError,
    VideoItem,
    VideoQuality,
    VideoScanState,
    _normalize_phone,
    appearance_choices,
    apply_downloaded_marks,
    build_save_name,
    canonical_peer_id,
    classify_video_message,
    clear_credentials,
    config_with_ui_mode,
    delete_task_message,
    dialog_kind,
    dialog_title,
    explain_error,
    filter_dialogs,
    format_bytes,
    format_duration_short,
    gather_chat_video_page,
    has_api_credentials,
    is_user_stop,
    link_failure_text,
    list_video_qualities,
    load_config,
    load_download_records,
    load_unfinished_tasks,
    login_dialog_after_session_loss,
    normalize_ui_mode,
    parse_telegram_link,
    pick_best_proxy,
    pick_video_quality,
    plan_link_jobs,
    present_task,
    proxy_from_config,
    quality_pref_label,
    record_download,
    restored_link_text,
    quality_pref_value,
    sanitize_filename,
    snap_quality_height,
    split_link_text,
    startup_login_target,
    summarize_tasks,
    task_matches_filter,
    ui_mode_from_config,
    unique_path,
    update_config,
    validate_proxy_settings,
    video_downloaded_label,
    video_row_subtitle,
    visible_chat_selection,
)


def _doc(doc_id: int, mime: str, attrs: list, size: int = 1000) -> Document:
    return Document(
        id=doc_id,
        access_hash=1,
        file_reference=b"x",
        date=None,
        mime_type=mime,
        size=size,
        dc_id=2,
        attributes=attrs,
    )


def _video_attr(duration: float = 5, w: int = 1920, h: int = 1080, round_message: bool = False) -> DocumentAttributeVideo:
    return DocumentAttributeVideo(duration=duration, w=w, h=h, round_message=round_message)


def _msg(msg_id: int, media=None, text: str = "", restriction: str = "", pending: bool = False):
    reasons = [RestrictionReason(platform="all", reason="restricted", text=restriction)] if restriction else None
    return SimpleNamespace(
        id=msg_id,
        media=media,
        message=text,
        raw_text=text,
        date=datetime(2024, 5, 1, 8, 0, tzinfo=timezone.utc),
        restriction_reason=reasons,
        video_processing_pending=pending,
        noforwards=False,
        document=None,
    )


def _video_media(doc_id: int, mime: str = "video/mp4", attrs=None, size: int = 2000, alts=None):
    doc = _doc(doc_id, mime, attrs if attrs is not None else [_video_attr()], size=size)
    return MessageMediaDocument(document=doc, video=True, alt_documents=alts)


class _Source:
    def __init__(self, rows):
        self.rows = list(rows)

    def matching(self, offset_id: int):
        for msg in self.rows:
            if offset_id and msg.id >= offset_id:
                continue
            yield msg


def _iter_factory(history, filters=None, errors=None):
    filters = filters or {}
    errors = errors or {}

    async def iter_messages(offset_id=0, limit=None, filter=None):
        if filter is None:
            key = "history"
            rows = history
        else:
            key = type(filter).__name__
            rows = filters.get(key, [])
        if key in errors:
            raise errors[key]
        for msg in rows:
            if offset_id and msg.id >= offset_id:
                continue
            yield msg

    return iter_messages


def _pages(history, *, limit=30, scan_budget=50, filter_budget=50, filters=None, errors=None):
    async def _run():
        state = VideoScanState()
        found = []
        notices = []
        guard = 0
        while True:
            page = await gather_chat_video_page(
                _iter_factory(history, filters, errors),
                limit=limit,
                state=state,
                scan_budget=scan_budget,
                filter_budget=filter_budget,
            )
            ids = [item.message_id for item in page.items]
            overlap = set(ids) & set(found)
            if overlap:
                raise AssertionError(f"重复列出 {sorted(overlap)}")
            found.extend(ids)
            notices.extend(page.notices)
            state = page.state
            guard += 1
            if guard > 30:
                raise AssertionError(f"翻页没有结束，已得到 {found}")
            if not page.has_more:
                return found, notices, page

        return found, notices, None

    return asyncio.run(_run())


class VideoDiscoveryTests(unittest.TestCase):
    def test_kinds_that_used_to_be_easy_to_skip(self) -> None:
        plain = _msg(1, _video_media(11))
        note = _msg(2, _video_media(12, attrs=[_video_attr(round_message=True)]))
        gif = _msg(
            3,
            _video_media(13, attrs=[DocumentAttributeAnimated(), _video_attr(h=320, w=320)]),
        )
        image_gif = _msg(
            4,
            MessageMediaDocument(
                document=_doc(14, "image/gif", [DocumentAttributeAnimated(), DocumentAttributeFilename("a.gif")]),
            ),
        )
        as_file = _msg(
            5,
            MessageMediaDocument(
                document=_doc(15, "video/mp4", [DocumentAttributeFilename("clip.mp4")], size=3000),
            ),
        )
        ext_only = _msg(
            6,
            MessageMediaDocument(
                document=_doc(16, "application/octet-stream", [DocumentAttributeFilename("clip.mkv")], size=4000),
            ),
        )
        low = _doc(21, "video/mp4", [_video_attr(w=1280, h=720)], size=100)
        high = _doc(22, "video/mp4", [_video_attr(w=1920, h=1080)], size=500)
        alt = _msg(7, MessageMediaDocument(document=high, video=True, alt_documents=[low]))
        web_doc = _doc(23, "video/mp4", [_video_attr()], size=800)
        webpage_file = _msg(
            8,
            MessageMediaWebPage(
                webpage=WebPage(
                    id=1,
                    url="https://t.me/example/1",
                    display_url="t.me",
                    hash=1,
                    document=web_doc,
                    type="video",
                )
            ),
        )
        embed = _msg(
            9,
            MessageMediaWebPage(
                webpage=WebPage(
                    id=2,
                    url="https://youtu.be/abc",
                    display_url="youtu.be",
                    hash=2,
                    type="video",
                    embed_url="https://www.youtube.com/embed/abc",
                    embed_type="video",
                )
            ),
        )
        live = _msg(
            10,
            MessageMediaPhoto(video=_doc(24, "video/mp4", [_video_attr(duration=2, w=720, h=1280)], size=50)),
        )
        paid = _msg(11, MessageMediaPaidMedia(stars_amount=5, extended_media=[MessageExtendedMediaPreview(video_duration=8)]))
        stream = _msg(12, MessageMediaVideoStream(call=None))
        sticker = _msg(
            13,
            MessageMediaDocument(
                document=_doc(
                    25,
                    "video/webm",
                    [DocumentAttributeSticker(alt="😀", stickerset=None)],
                )
            ),
        )
        text = _msg(14, text="只是文字")
        blocked = _msg(15, MessageMediaUnsupported(), restriction="当地法规限制查看")

        self.assertEqual(classify_video_message(plain).kind, "video")
        self.assertTrue(classify_video_message(plain).downloadable)
        self.assertEqual(classify_video_message(note).kind, "video_note")
        self.assertEqual(classify_video_message(gif).kind, "gif")
        self.assertEqual(classify_video_message(image_gif).kind, "gif")
        self.assertEqual(classify_video_message(as_file).kind, "video")
        self.assertEqual(classify_video_message(ext_only).kind, "video")
        self.assertEqual(classify_video_message(webpage_file).kind, "video")
        self.assertTrue(classify_video_message(webpage_file).downloadable)
        embed_info = classify_video_message(embed)
        self.assertEqual(embed_info.kind, "webpage")
        self.assertFalse(embed_info.downloadable)
        self.assertIn("消息 #9", embed_info.note)
        self.assertIn("youtube.com", embed_info.note)
        self.assertEqual(classify_video_message(live).kind, "live")
        self.assertTrue(classify_video_message(live).downloadable)
        paid_info = classify_video_message(paid)
        self.assertFalse(paid_info.downloadable)
        self.assertIn("付费", paid_info.note)
        self.assertIn("消息 #11", paid_info.note)
        self.assertFalse(classify_video_message(stream).downloadable)
        self.assertIn("直播", classify_video_message(stream).note)
        self.assertIsNone(classify_video_message(sticker))
        self.assertIsNone(classify_video_message(text))
        self.assertIsNone(classify_video_message(blocked))

        qualities = list_video_qualities(alt)
        self.assertEqual([q.label for q in qualities], ["1080p", "720p"])
        self.assertEqual(pick_video_quality(qualities, "720").label, "720p")
        self.assertEqual(pick_video_quality(qualities, "highest").label, "1080p")

        history = [
            plain, note, gif, image_gif, as_file, ext_only, alt, webpage_file,
            embed, live, paid, stream, sticker, text, blocked,
        ]
        # 新的在前
        history = sorted(history, key=lambda m: m.id, reverse=True)
        found, notices, _page = _pages(history, limit=4, scan_budget=6, filter_budget=4)
        expected = [15, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        # 15 是无法识别的受限媒体，不进列表，但说明里要有它
        self.assertEqual(found, [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
        self.assertTrue(any("消息 #15" in note for note in notices), notices)
        self.assertNotIn(13, found)
        self.assertNotIn(14, found)
        self.assertEqual(expected[0], 15)

    def test_image_gifs_do_not_hide_later_videos(self) -> None:
        """旧逻辑用 GIF 过滤器的前 N 条原始结果做额度，静态图会把后面的视频 GIF 挤掉，并且不再翻页。"""
        image = [
            _msg(
                100 - i,
                MessageMediaDocument(
                    document=_doc(500 + i, "image/gif", [DocumentAttributeAnimated(), DocumentAttributeFilename(f"{i}.gif")]),
                ),
            )
            for i in range(6)
        ]
        real = _msg(
            50,
            _video_media(900, attrs=[DocumentAttributeAnimated(), _video_attr(), DocumentAttributeFilename("real.mp4")]),
        )
        history = image + [real]
        found, _notices, page = _pages(history, limit=3, scan_budget=3, filter_budget=2)
        self.assertEqual(found, [100, 99, 98, 97, 96, 95, 50])
        self.assertFalse(page.has_more)

    def test_file_videos_beyond_a_short_document_scan_are_not_dropped(self) -> None:
        pdfs = [
            _msg(80 - i, MessageMediaDocument(document=_doc(700 + i, "application/pdf", [DocumentAttributeFilename(f"{i}.pdf")])))
            for i in range(4)
        ]
        movie = _msg(
            10,
            MessageMediaDocument(document=_doc(800, "video/mp4", [DocumentAttributeFilename("late.mp4")], size=9)),
        )
        # 主时间线里没有这条，只出现在「文件」搜索里（话题中常见）。
        found, notices, _page = _pages(
            [],
            limit=5,
            scan_budget=5,
            filter_budget=2,
            filters={"InputMessagesFilterDocument": pdfs + [movie]},
        )
        self.assertEqual(found, [10])
        self.assertTrue(any("视频文件" in note and "加载更多" in note for note in notices))

    def test_older_search_hits_are_not_listed_twice(self) -> None:
        """搜索索引会一次看到很旧的视频。主时间线还没扫到时不能先列出来，否则下一页会再出现一次。"""
        history = [
            _msg(100, text="a"),
            _msg(99, text="b"),
            _msg(50, _video_media(1)),
            _msg(40, _video_media(2)),
        ]
        topic = _msg(20, _video_media(3))
        indexed = [_msg(50, _video_media(1)), _msg(40, _video_media(2)), topic]
        found, _notices, page = _pages(
            history,
            limit=5,
            scan_budget=2,
            filter_budget=10,
            filters={"InputMessagesFilterVideo": indexed},
        )
        self.assertEqual(found, [50, 40, 20])
        self.assertFalse(page.has_more)

    def test_topic_video_missing_from_history_is_merged(self) -> None:
        history = [
            _msg(30, _video_media(1)),
            _msg(10, text="普通文字"),
        ]
        topic = _msg(20, _video_media(2))
        found, _notices, _page = _pages(
            history,
            filters={"InputMessagesFilterVideo": [topic, history[0]]},
        )
        self.assertEqual(found, [30, 20])

    def test_filter_error_is_reported_and_other_videos_remain(self) -> None:
        history = [_msg(5, _video_media(1)), _msg(4, text="x")]
        found, notices, _page = _pages(
            history,
            errors={"InputMessagesFilterGif": RuntimeError("gif index down")},
        )
        self.assertEqual(found, [5])
        gif_notes = [note for note in notices if "动态 GIF" in note]
        self.assertTrue(gif_notes, notices)
        self.assertTrue(all("Traceback" not in note for note in gif_notes))
        self.assertIn("不完整", gif_notes[0])

    def test_pagination_is_complete_and_ordered(self) -> None:
        history = []
        for mid in range(40, 0, -1):
            if mid % 4 == 0:
                history.append(_msg(mid, text="text"))
            elif mid % 4 == 1:
                history.append(_msg(mid, _video_media(1000 + mid, attrs=[_video_attr(round_message=True)])))
            elif mid % 4 == 2:
                history.append(
                    _msg(mid, _video_media(1000 + mid, attrs=[DocumentAttributeAnimated(), _video_attr()]))
                )
            else:
                history.append(_msg(mid, _video_media(1000 + mid)))
        found, _notices, page = _pages(history, limit=5, scan_budget=7, filter_budget=3)
        expected = [mid for mid in range(40, 0, -1) if mid % 4 != 0]
        self.assertEqual(found, expected)
        self.assertFalse(page.has_more)


class LinkQueueTests(unittest.TestCase):
    def test_invalid_link_error_names_the_url_and_does_not_block_the_rest(self) -> None:
        jobs = plan_link_jobs(
            [
                "https://example.com/not-telegram",
                "https://t.me/somechannel/15",
                "https://t.me/joinchat/AAAA",
            ]
        )
        self.assertEqual(len(jobs), 3)
        self.assertIn("https://example.com/not-telegram", jobs[0].error)
        self.assertIsNone(jobs[0].parsed)
        self.assertEqual(jobs[1].parsed.entity, "somechannel")
        self.assertEqual(jobs[1].parsed.message_id, 15)
        self.assertIn("https://t.me/joinchat/AAAA", jobs[2].error)
        self.assertIn("邀请", jobs[2].error)

    def test_failure_text_names_link_and_message(self) -> None:
        text = link_failure_text(
            "https://t.me/demo/9",
            UserFacingError("该消息不含视频、圆形视频或动态 GIF。"),
            message_id=9,
            title="示例频道",
        )
        self.assertIn("https://t.me/demo/9", text)
        self.assertIn("消息 #9", text)
        self.assertIn("示例频道", text)
        self.assertIn("不含视频", text)

    def test_add_start_pause_delete_for_each_state(self) -> None:
        queue = DownloadQueue()
        queue.upsert("bad", "https://t.me/bad", "error", url="https://t.me/bad", error="链接 https://t.me/bad：无法解析")
        queue.upsert("wait", "https://t.me/wait/1", "queued", url="https://t.me/wait/1")
        queue.upsert("run", "https://t.me/run/2", "running", url="https://t.me/run/2")
        queue.upsert("ok", "https://t.me/ok/3", "done", url="https://t.me/ok/3")

        self.assertEqual(queue.remove("run"), "cancelled")
        self.assertTrue(queue.cancelled("run"))
        self.assertIsNone(queue.get("run"))
        # 取消后不能又被进度更新加回来
        queue.upsert("run", "https://t.me/run/2", "running", url="https://t.me/run/2")
        self.assertIsNone(queue.get("run"))

        queue.mark_stopped()
        self.assertEqual(queue.get("wait").status, "stopped")
        self.assertEqual(queue.get("ok").status, "done")
        self.assertEqual(queue.get("bad").status, "error")

        self.assertEqual(queue.remove("wait"), "removed")
        self.assertEqual(queue.remove("bad"), "removed")
        self.assertEqual(queue.remove("ok"), "removed")
        self.assertEqual(queue.remove("missing"), "missing")
        self.assertEqual(queue.keys(), [])

    def test_replace_keeps_url_and_cancel_removes_children(self) -> None:
        queue = DownloadQueue()
        queue.upsert("link-0", "https://t.me/demo/4", "running", url="https://t.me/demo/4")
        queue.replace(
            "link-0",
            [
                ("link-0-msg-4", "a.mp4", 10, "https://t.me/demo/4"),
                ("link-0-msg-5", "b.mp4", 20, "https://t.me/demo/4"),
            ],
        )
        self.assertEqual(queue.keys(), ["link-0-msg-4", "link-0-msg-5"])
        self.assertEqual(queue.get("link-0-msg-4").url, "https://t.me/demo/4")
        self.assertEqual(queue.remove("link-0-msg-4"), "cancelled")
        self.assertEqual(queue.keys(), ["link-0-msg-5"])

        queue.upsert("link-1", "https://t.me/demo/8", "queued", url="https://t.me/demo/8")
        queue.remove("link-1")
        queue.replace("link-1", [("link-1-msg-8", "c.mp4", 1, "https://t.me/demo/8")])
        self.assertTrue(queue.cancelled("link-1-msg-8"))
        self.assertNotIn("link-1-msg-8", queue.keys())


class RegressionTests(unittest.TestCase):
    def test_link_parsing(self) -> None:
        public = parse_telegram_link("t.me/some_channel/42")
        self.assertEqual(public.entity, "some_channel")
        self.assertEqual(public.message_id, 42)
        private = parse_telegram_link("https://t.me/c/123456/99")
        self.assertEqual(private.entity, -100123456)
        self.assertEqual(private.message_id, 99)
        comment = parse_telegram_link("https://t.me/some_channel/7?comment=3")
        self.assertEqual(comment.comment_id, 3)
        topic = parse_telegram_link("https://t.me/c/123456/55/77")
        self.assertEqual(topic.message_id, 77)
        preview = parse_telegram_link("https://t.me/s/some_channel/8")
        self.assertEqual(preview.message_id, 8)
        tg = parse_telegram_link("tg://resolve?domain=some_channel&post=6")
        self.assertEqual((tg.entity, tg.message_id), ("some_channel", 6))
        with self.assertRaises(UserFacingError):
            parse_telegram_link("https://t.me/some_channel")
        self.assertEqual(
            split_link_text("https://t.me/a/1，https://t.me/a/1 https://t.me/b/2"),
            ["https://t.me/a/1", "https://t.me/b/2"],
        )

    def test_quality_and_names(self) -> None:
        self.assertEqual(snap_quality_height(647), 720)
        self.assertEqual(quality_pref_value("最高"), "highest")
        self.assertEqual(quality_pref_label("1080"), "1080p")
        today = datetime.now(timezone.utc)
        high = VideoQuality(label="1080p", width=1920, height=1080, size=44_669_338, document=None, key="1080")
        low = VideoQuality(label="720p", width=1280, height=720, size=8_000_000, document=None, key="720")
        item = VideoItem(
            message_id=1,
            date=today,
            duration=12,
            size=high.size,
            filename="今日实验演示.mp4",
            caption="今日实验演示",
            kind="video",
            qualities=[high, low],
        )
        shown = video_row_subtitle(item, "highest")
        self.assertTrue(shown.startswith("1080p · "))
        self.assertIn(format_bytes(high.size), shown)
        self.assertIn("今天", shown)
        picked = video_row_subtitle(item, "720")
        self.assertTrue(picked.startswith("720p · "))
        self.assertIn(format_bytes(low.size), picked)
        unknown = VideoQuality(label="原画", width=0, height=0, size=2048, document=None, key="highest")
        bare = VideoItem(
            message_id=2,
            date=today,
            duration=1,
            size=2048,
            filename="clip.mp4",
            caption="",
            kind="video",
            qualities=[unknown],
        )
        bare_text = video_row_subtitle(bare, "highest")
        self.assertNotIn("原画", bare_text)
        self.assertNotIn("1080p", bare_text)
        self.assertTrue(bare_text.startswith(format_bytes(2048)))
        empty = VideoItem(
            message_id=3,
            date=today,
            duration=1,
            size=100,
            filename="plain.mp4",
            caption="",
            kind="video",
        )
        self.assertNotIn("p ·", video_row_subtitle(empty))
        self.assertEqual(sanitize_filename('a<>:"b'), "a_b")
        self.assertTrue(sanitize_filename("CON").startswith("_"))
        msg = _msg(12, _video_media(1, attrs=[_video_attr(), DocumentAttributeFilename("clip:1.mp4")]))
        name = build_save_name(msg, 'bad<>name')
        self.assertNotIn("<", name)
        self.assertIn("12", name)
        self.assertTrue(name.endswith(".mp4"))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = unique_path(folder, "a.mp4")
            first.write_bytes(b"x")
            second = unique_path(folder, "a.mp4")
            self.assertNotEqual(first, second)
            self.assertFalse(second.exists())

    def test_proxy_validation(self) -> None:
        self.assertEqual(validate_proxy_settings("direct", "nope"), ("none", "nope"))
        self.assertEqual(validate_proxy_settings("socks5", "7891"), ("socks5", 7891))
        with self.assertRaises(UserFacingError):
            validate_proxy_settings("ftp", "1")
        with self.assertRaises(UserFacingError):
            validate_proxy_settings("http", "70000")
        cfg = proxy_from_config({"proxy_type": "none"})
        self.assertIsNone(cfg)
        socks = proxy_from_config({"proxy_type": "socks5", "proxy_host": "127.0.0.1", "proxy_port": 7891})
        self.assertEqual(socks[1:], ("127.0.0.1", 7891))
        best = pick_best_proxy(
            [
                {"type": "http", "host": "127.0.0.1", "port": 7890},
                {"type": "socks5", "host": "127.0.0.1", "port": 7891},
            ]
        )
        self.assertEqual(best["port"], 7891)

    def test_chat_filter_and_selection(self) -> None:
        dialogs = [
            DialogItem(entity=None, title="新闻", kind="频道", id=1),
            DialogItem(entity=None, title="同学", kind="群组", id=2),
            DialogItem(entity=None, title="小明", kind="私聊", id=3),
            DialogItem(entity=None, title="助手", kind="机器人", id=4),
        ]
        self.assertEqual([d.id for d in filter_dialogs(dialogs, "频道", "")], [1])
        self.assertEqual([d.id for d in filter_dialogs(dialogs, "全部", "小")], [3])
        self.assertIsNone(visible_chat_selection(dialogs[0], filter_dialogs(dialogs, "群组")))
        self.assertEqual(visible_chat_selection(dialogs[1], filter_dialogs(dialogs, "群组")).id, 2)
        channel = SimpleNamespace(is_user=False, is_group=False, is_channel=True, entity=SimpleNamespace(broadcast=True, bot=False))
        group = SimpleNamespace(is_user=False, is_group=True, is_channel=True, entity=SimpleNamespace(broadcast=False, bot=False))
        bot = SimpleNamespace(is_user=True, is_group=False, is_channel=False, entity=SimpleNamespace(broadcast=False, bot=True))
        user = SimpleNamespace(
            is_user=True,
            is_group=False,
            is_channel=False,
            name="",
            title="",
            entity=SimpleNamespace(broadcast=False, bot=False, first_name="王", last_name="小明", username=None),
        )
        self.assertEqual(dialog_kind(channel), "频道")
        self.assertEqual(dialog_kind(group), "群组")
        self.assertEqual(dialog_kind(bot), "机器人")
        self.assertEqual(dialog_title(user), "王 小明")

    def test_login_state_and_errors(self) -> None:
        self.assertEqual(startup_login_target(has_credentials=False, authorized=True), "api")
        self.assertEqual(startup_login_target(has_credentials=True, authorized=True), "ready")
        self.assertEqual(startup_login_target(has_credentials=True, authorized=False, phone_pending=True), "stay")
        self.assertEqual(startup_login_target(has_credentials=True, authorized=False, code_dialog=True), "stay")
        self.assertEqual(startup_login_target(has_credentials=True, authorized=False), "phone")
        self.assertEqual(login_dialog_after_session_loss(True), "phone")
        self.assertEqual(login_dialog_after_session_loss(False), "api")
        self.assertTrue(has_api_credentials({"api_id": "123", "api_hash": "abc"}))
        self.assertFalse(has_api_credentials({"api_id": "abc", "api_hash": "abc"}))
        self.assertEqual(_normalize_phone("13800138000"), "+8613800138000")
        with self.assertRaises(UserFacingError):
            _normalize_phone("123")
        self.assertIn("12", explain_error(FloodWaitError(None, 12)))
        self.assertEqual(explain_error(UserFacingError("请先粘贴")), "请先粘贴")
        self.assertTrue(is_user_stop(UserFacingError("已停止。")))
        self.assertFalse(is_user_stop(UserFacingError("别的错误")))
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_duration_short(None), "—")
        self.assertEqual(format_duration_short(90), "01:30")
        self.assertEqual(format_duration_short(3661), "1:01:01")


def _video_item(message_id: int, document_id: int | None = None) -> VideoItem:
    return VideoItem(
        message_id=message_id,
        date=None,
        duration=3,
        size=100,
        filename="clip.mp4",
        caption="标题",
        kind="video",
        document_id=document_id,
    )


class PersistenceTests(unittest.TestCase):
    def test_unfinished_queue_survives_restart_and_running_becomes_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download_queue.json"
            queue = DownloadQueue(persist_path=path)
            queue.upsert("bad", "https://t.me/nope", "error", url="https://t.me/nope", error="链接 https://t.me/nope：不是帖子链接")
            queue.upsert("wait", "https://t.me/demo/1", "queued", url="https://t.me/demo/1")
            queue.upsert("run", "https://t.me/demo/2", "running", url="https://t.me/demo/2", current=40, total=100)
            queue.note_identity("run", peer_id=-100123456, message_id=2, document_id=77)
            queue.upsert("pause", "https://t.me/demo/3", "paused", url="https://t.me/demo/3")
            queue.upsert("stop", "https://t.me/demo/4", "stopped", url="https://t.me/demo/4")
            queue.upsert("done", "https://t.me/demo/5", "done", url="https://t.me/demo/5", save_path=str(Path(tmp) / "done.mp4"))

            # 进度数字变化不应把「正在下载」写成已完成
            queue.upsert("run", "https://t.me/demo/2", "running", url="https://t.me/demo/2", current=80, total=100)
            restored = DownloadQueue(persist_path=path)
            self.assertEqual(restored.keys(), ["bad", "wait", "run", "pause", "stop"])
            self.assertNotIn("done", restored.keys())
            self.assertEqual(restored.get("wait").status, "queued")
            self.assertEqual(restored.get("run").status, "stopped")
            self.assertEqual(restored.get("run").error, INTERRUPTED_TASK_HINT)
            self.assertEqual(restored.get("run").peer_id, -100123456)
            self.assertEqual(restored.get("run").message_id, 2)
            self.assertEqual(restored.get("run").document_id, 77)
            self.assertNotEqual(restored.get("run").status, "running")
            self.assertEqual(restored.get("pause").status, "stopped")
            self.assertEqual(restored.get("stop").status, "stopped")
            self.assertIn("https://t.me/nope", restored.get("bad").error)
            self.assertEqual(restored.get("bad").status, "error")
            text = restored_link_text([restored.get(key) for key in restored.keys()])
            self.assertIn("https://t.me/demo/2", text)
            self.assertIn("https://t.me/nope", text)

            queue.remove("wait")
            again = load_unfinished_tasks(path)
            self.assertNotIn("wait", [task.key for task in again])

            path.write_text("{", encoding="utf-8")
            self.assertEqual(load_unfinished_tasks(path), [])

    def test_prepare_for_exit_stops_the_active_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download_queue.json"
            queue = DownloadQueue(persist_path=path)
            queue.upsert("run", "https://t.me/demo/9", "running", url="https://t.me/demo/9")
            queue.prepare_for_exit()
            self.assertEqual(queue.get("run").status, "stopped")
            self.assertIn("未下完", queue.get("run").error)
            reloaded = DownloadQueue(persist_path=path)
            self.assertEqual(reloaded.get("run").status, "stopped")

    def test_downloaded_mark_from_link_and_chat_and_deleted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "downloads_index.json"
            folder = Path(tmp)
            link_file = folder / "from-link.mp4"
            chat_file = folder / "from-chat.mp4"
            link_file.write_bytes(b"link-bytes")
            chat_file.write_bytes(b"chat-bytes")
            peer = canonical_peer_id(PeerChannel(channel_id=123456))
            # Telethon 的频道 id 是 -(10**12 + 频道号)，对话列表和解析后的实体都用这个值。
            self.assertEqual(peer, -(10**12 + 123456))
            self.assertEqual(canonical_peer_id(peer), peer)
            self.assertEqual(canonical_peer_id(DialogItem(entity=None, title="频道", kind="频道", id=peer)), peer)

            # 粘贴链接下载
            record_download(
                index,
                peer_id=peer,
                message_id=8,
                save_path=str(link_file),
                document_id=42,
                url="https://t.me/c/123456/8",
            )
            # 从对话列表下载的是另一条消息
            record_download(
                index,
                peer_id=DialogItem(entity=None, title="频道", kind="频道", id=peer),
                message_id=9,
                save_path=str(chat_file),
                document_id=43,
                url="",
            )
            records = load_download_records(index)
            items = [_video_item(8, 42), _video_item(9, 43), _video_item(10, 99), _video_item(77, 42)]
            apply_downloaded_marks(items, peer, records)
            self.assertTrue(items[0].downloaded)
            self.assertTrue(items[1].downloaded)
            self.assertFalse(items[2].downloaded)
            # 同一对话里文档 id 相同，也算已下载（链接和列表看到的是同一个文件）
            self.assertTrue(items[3].downloaded)
            self.assertEqual(video_downloaded_label(items[0]), "已下载")
            self.assertEqual(video_downloaded_label(items[2]), "")

            other_chat = [_video_item(8, 42)]
            apply_downloaded_marks(other_chat, -100999, records)
            self.assertFalse(other_chat[0].downloaded)

            link_file.unlink()
            apply_downloaded_marks(items, peer, load_download_records(index))
            self.assertFalse(items[0].downloaded)
            # 对话里另存的那条文件还在
            self.assertTrue(items[1].downloaded)
            for item in items:
                label = video_downloaded_label(item)
                self.assertNotIn("peer", label.lower())
                self.assertNotIn("message", label.lower())


class UiModeTests(unittest.TestCase):
    def test_missing_mode_is_classic_and_switch_keeps_other_settings(self) -> None:
        self.assertEqual(normalize_ui_mode(None), UI_MODE_CLASSIC)
        self.assertEqual(normalize_ui_mode(""), UI_MODE_CLASSIC)
        self.assertEqual(normalize_ui_mode("nope"), UI_MODE_CLASSIC)
        self.assertEqual(ui_mode_from_config({}), UI_MODE_CLASSIC)
        self.assertEqual(ui_mode_from_config(None), UI_MODE_CLASSIC)
        self.assertEqual(ui_mode_from_config({"ui_mode": "professional"}), UI_MODE_PROFESSIONAL)

        original = {
            "api_id": "100",
            "api_hash": "hash-value",
            "proxy_type": "socks5",
            "proxy_host": "127.0.0.1",
            "proxy_port": 7891,
            "last_save_folder": "D:/Videos",
        }
        professional = config_with_ui_mode(original, "专业")
        self.assertEqual(professional["ui_mode"], UI_MODE_PROFESSIONAL)
        for key, value in original.items():
            self.assertEqual(professional[key], value)
        classic = config_with_ui_mode(professional, "classic")
        self.assertEqual(classic["ui_mode"], UI_MODE_CLASSIC)
        self.assertEqual(classic["proxy_host"], "127.0.0.1")
        self.assertEqual(classic["last_save_folder"], "D:/Videos")
        self.assertEqual(classic["api_hash"], "hash-value")
        # 队列不在配置里，切换函数也不会去碰它
        self.assertNotIn("tasks", classic)

        choices = appearance_choices()
        self.assertEqual([item["mode"] for item in choices], [UI_MODE_CLASSIC, UI_MODE_PROFESSIONAL])
        self.assertEqual(choices[0]["title"], "经典")
        self.assertIn("侧栏", choices[0]["description"])
        self.assertEqual(choices[1]["title"], "专业")
        self.assertIn("任务列表", choices[1]["description"])

    def test_logout_keeps_ui_mode_proxy_and_save_path(self) -> None:
        import tgcore

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            previous = (tgcore.DATA_DIR, tgcore.CONFIG_PATH, tgcore.SESSION_PATH)
            tgcore.DATA_DIR = folder
            tgcore.CONFIG_PATH = folder / "config.json"
            tgcore.SESSION_PATH = folder / "tg_session"
            try:
                update_config(
                    api_id="100",
                    api_hash="hash-value",
                    ui_mode=UI_MODE_PROFESSIONAL,
                    proxy_type="socks5",
                    proxy_host="127.0.0.1",
                    proxy_port=7891,
                    last_save_folder=str(folder / "saved"),
                    video_quality="highest",
                )
                clear_credentials(keep_folder=True)
                cfg = load_config()
            finally:
                tgcore.DATA_DIR, tgcore.CONFIG_PATH, tgcore.SESSION_PATH = previous
        self.assertNotIn("api_id", cfg)
        self.assertNotIn("api_hash", cfg)
        self.assertEqual(cfg.get("ui_mode"), UI_MODE_PROFESSIONAL)
        self.assertEqual(cfg.get("proxy_type"), "socks5")
        self.assertEqual(cfg.get("proxy_host"), "127.0.0.1")
        self.assertEqual(cfg.get("proxy_port"), 7891)
        self.assertTrue(str(cfg.get("last_save_folder") or "").endswith("saved"))


class TaskPresentationTests(unittest.TestCase):
    def test_row_copy_for_wait_stopped_done_and_filters(self) -> None:
        waiting = present_task("采访片段_02.mp4", "running", current=4_200_000, total=28_500_000, wait_seconds=32)
        self.assertEqual(waiting["speed_text"], "等待 32 秒")
        self.assertEqual(waiting["subtitle"], "频率限制，可停止")
        self.assertTrue(waiting["show_stop"])
        self.assertTrue(waiting["show_delete"])
        self.assertEqual(waiting["bar"], "wait")

        stopped = present_task(
            "街访成片.mp4",
            "stopped",
            current=36_000_000,
            total=128_000_000,
            error=INTERRUPTED_TASK_HINT,
        )
        self.assertEqual(stopped["speed_text"], "已停止")
        self.assertEqual(stopped["subtitle"], "上次未完成")
        self.assertEqual(stopped["percent"], int(36_000_000 * 100 / 128_000_000))
        self.assertFalse(stopped["show_stop"])
        self.assertTrue(stopped["show_delete"])
        self.assertEqual(stopped["bar"], "stopped")
        self.assertNotEqual(stopped["speed_text"], "已完成")

        done = present_task("片头.mp4", "done", current=12_000_000, total=12_000_000)
        self.assertEqual(done["speed_text"], "已完成")
        self.assertEqual(done["percent"], 100)
        self.assertFalse(done["show_stop"])
        self.assertTrue(done["show_delete"])

        self.assertTrue(task_matches_filter("stopped", "全部"))
        self.assertFalse(task_matches_filter("stopped", "已完成"))
        self.assertFalse(task_matches_filter("stopped", "下载中"))
        self.assertTrue(task_matches_filter("done", "已完成"))
        self.assertFalse(task_matches_filter("done", "下载中"))
        self.assertTrue(task_matches_filter("running", "下载中"))
        self.assertTrue(task_matches_filter("queued", "下载中"))

        summary = summarize_tasks(
            [
                {"status": "running", "wait_seconds": 0},
                {"status": "running", "wait_seconds": 32},
                {"status": "stopped", "wait_seconds": 0},
                {"status": "done", "wait_seconds": 0},
            ]
        )
        self.assertEqual(summary, "下载中 1 · 等待 1 · 已停止 1")
        self.assertNotIn("已完成", summary)

        self.assertEqual(DELETE_DIALOG_TITLE, "删除任务")
        self.assertEqual(delete_task_message("街访成片.mp4"), "确定删除「街访成片.mp4」？")

    def test_stop_one_row_keeps_it_and_does_not_drop_the_queue_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download_queue.json"
            queue = DownloadQueue(persist_path=path)
            queue.upsert("run", "夜间街景.mp4", "running", current=10, total=40)
            queue.upsert("wait", "采访片段_02.mp4", "queued")
            self.assertEqual(queue.stop("run"), "stopped")
            self.assertEqual(queue.get("run").status, "stopped")
            self.assertEqual(queue.stop("run"), "ignored")
            self.assertEqual(queue.stop("missing"), "missing")
            self.assertIn("wait", queue.keys())
            # 停掉的一行不再接受“还在下载”的进度回写
            queue.upsert("run", "夜间街景.mp4", "running", current=20, total=40)
            self.assertEqual(queue.get("run").status, "stopped")
            reloaded = DownloadQueue(persist_path=path)
            self.assertEqual(reloaded.get("run").status, "stopped")
            self.assertEqual(reloaded.get("wait").status, "queued")


if __name__ == "__main__":
    unittest.main()
