# -*- coding: utf-8 -*-
"""Light Fluent tokens and 1.3.16 copy that can be checked without a Windows desktop."""

from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tgcore import APP_VERSION, DownloadQueue, INTERRUPTED_TASK_HINT, VideoItem, VideoQuality

import app


ROOT = Path(__file__).resolve().parents[1]


class VisualTokenTests(unittest.TestCase):
    def test_light_fluent_tokens_and_window_size(self) -> None:
        self.assertEqual(app.UI_TOKENS["window"], (960, 680))
        self.assertEqual(app.WINDOW_W, 960)
        self.assertEqual(app.WINDOW_H, 680)
        self.assertEqual(app.C_BG, "#F3F4F8")
        self.assertEqual(app.C_CARD, "#FFFFFF")
        self.assertEqual(app.C_TITLE, "#F9FAFD")
        self.assertEqual(app.C_RAIL, "#F3F4F9")
        self.assertEqual(app.C_TEXT, "#1A1A1A")
        self.assertEqual(app.C_TEXT2, "#5C5C5C")
        self.assertEqual(app.C_TEXT3, "#8A8A8A")
        self.assertEqual(app.C_STROKE2, "#D0D0D0")
        self.assertEqual(app.C_ACCENT, "#005FB8")
        self.assertEqual(app.C_HOVER, "#0056A5")
        self.assertEqual(app.C_SEL, "#EBF3FC")
        self.assertEqual(app.C_OK, "#0F7B0F")
        self.assertEqual(app.C_ERR, "#C42B1C")
        self.assertEqual(app.C_WAIT_BG, "#FBF4E0")
        self.assertEqual(app.RAIL_W, 68)
        self.assertEqual(app.CTRL_H, 32)
        self.assertEqual(app.CTRL_RADIUS, 4)
        self.assertEqual(app.CARD_RADIUS, 8)
        self.assertEqual(app.UI_TOKENS["about_version"], "1.3.16")
        self.assertEqual(APP_VERSION, "1.3.16")

    def test_source_has_no_style_switch_or_dropped_chrome(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertNotIn("ui_mode", names)
        self.assertNotIn("界面风格", source)
        self.assertNotIn("打开申请页", source)
        self.assertNotIn("1.4 设计稿", source)
        self.assertNotIn("ui_mode", source)
        self.assertIn("改完需重启再生效", source)
        self.assertIn("更改 API 凭据", source)
        self.assertIn(f'geometry(f"{{WINDOW_W}}x{{WINDOW_H}}")', source)

    def test_packaging_names_stay_on_the_published_release(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
        installer = (ROOT / "build_installer.py").read_text(encoding="utf-8")
        iss = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")
        self.assertIn("TGVideoDownloader-Setup-1.3.15.exe", readme)
        self.assertIn("TGVideoDownloader-Portable-1.3.15.zip", readme)
        self.assertNotIn("TGVideoDownloader-Setup-1.3.16.exe", readme)
        self.assertNotIn("1.3.16", workflow)
        self.assertIn("TGVideoDownloader-Setup-1.3.15.exe", workflow)
        self.assertIn("TGVideoDownloader-Portable-1.3.15.zip", workflow)
        self.assertIn("TGVideoDownloader-Setup-1.3.15.exe", installer)
        self.assertIn('#define MyAppVersion "1.3.15"', iss)


class CopyTests(unittest.TestCase):
    def test_video_subtitle_includes_known_quality_only(self) -> None:
        when = datetime.now(timezone.utc).replace(hour=8, minute=8, second=0, microsecond=0)
        known = VideoItem(
            message_id=1,
            date=when,
            duration=261,
            size=10,
            filename="demo.mp4",
            caption="今日实验演示",
            kind="video",
            width=1920,
            height=1080,
            qualities=[VideoQuality(label="1080p", width=1920, height=1080, size=44_662_784, document=None)],
        )
        text = app.video_row_subtitle(known, "highest")
        self.assertTrue(text.startswith("1080p · "))
        self.assertIn("MB", text)
        self.assertIn("今天", text)

        unknown = VideoItem(
            message_id=2,
            date=when - timedelta(days=1),
            duration=10,
            size=2048,
            filename="clip.mp4",
            caption="无清晰度",
            kind="video",
        )
        plain = app.video_row_subtitle(unknown, "highest")
        self.assertFalse(plain.startswith("1080p"))
        self.assertNotIn("p ·", plain)
        self.assertIn("昨天", plain)

    def test_task_row_wait_and_restored_stop_copy(self) -> None:
        self.assertEqual(app.flood_wait_text(32), "等待 32 秒")
        self.assertEqual(app.stopped_display(INTERRUPTED_TASK_HINT), "已停止 上次未完成")
        self.assertEqual(app.stopped_display(""), "已停止")
        self.assertEqual(app.task_row_status_text("waiting", "等待 32 秒"), "等待 32 秒")
        self.assertEqual(app.task_row_status_text("queued"), "排队中")
        self.assertEqual(app.task_row_status_text("stopped", INTERRUPTED_TASK_HINT), "已停止 上次未完成")

    def test_halt_keeps_the_row_and_marks_it_stopped(self) -> None:
        queue = DownloadQueue()
        queue.upsert("a", "夜间街景.mp4", "running", current=10, total=40)
        queue.halt("a")
        self.assertTrue(queue.halted("a"))
        self.assertEqual(queue.get("a").status, "stopped")
        self.assertNotIn("a", [])
        self.assertEqual(queue.remove("a"), "removed")
        self.assertFalse(queue.halted("a"))


if __name__ == "__main__":
    unittest.main()
