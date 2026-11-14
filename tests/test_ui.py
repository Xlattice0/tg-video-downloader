# -*- coding: utf-8 -*-
"""Light Fluent tokens and 1.3.16 copy that can be checked without a Windows desktop."""

from __future__ import annotations

import ast
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tkinter as tk
from PIL import Image

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


def _app_methods() -> dict[str, ast.FunctionDef]:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "App":
            return {item.name: item for item in node.body if isinstance(item, ast.FunctionDef)}
    raise AssertionError("App class not found")


class ChromePolicyTests(unittest.TestCase):
    def test_minimize_keeps_taskbar_and_close_hides_to_tray(self) -> None:
        self.assertEqual(app.chrome_action("minimize"), "taskbar")
        self.assertEqual(app.chrome_action("close"), "tray")
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        methods = _app_methods()
        minimize = ast.get_source_segment(source, methods["_minimize"]) or ""
        unmap = ast.get_source_segment(source, methods["_on_window_unmap"]) or ""
        close = ast.get_source_segment(source, methods["_on_close"]) or ""
        self.assertIn("iconify", minimize)
        self.assertNotIn("_hide_to_tray", minimize)
        self.assertNotIn("_on_close", minimize)
        self.assertNotIn("_hide_to_tray", unmap)
        self.assertIn("_hide_to_tray", close)

    def test_chat_download_is_not_refused_while_busy(self) -> None:
        self.assertEqual(app.chat_download_action(False), "start")
        self.assertEqual(app.chat_download_action(True), "enqueue")
        self.assertEqual(app.merge_queue_keys(["a", "b"], ["b", "c"]), ["a", "b", "c"])
        self.assertEqual(app.merge_queue_keys(["a"], []), ["a"])


class FontAndDpiTests(unittest.TestCase):
    def test_font_choice_prefers_yahei_then_segoe(self) -> None:
        self.assertEqual(
            app.choose_ui_font(["Arial", "Segoe UI", "Microsoft YaHei UI"]),
            "Microsoft YaHei UI",
        )
        self.assertEqual(app.choose_ui_font(["Segoe UI", "Arial"]), "Segoe UI")
        self.assertEqual(app.choose_ui_font(["Noto Sans CJK SC"]), "Noto Sans CJK SC")
        self.assertEqual(app.choose_ui_font(["Some Other Face"]), "TkDefaultFont")
        self.assertEqual(app.UI_FONT_CANDIDATES[0], "Microsoft YaHei UI")
        self.assertIn("Segoe UI", app.UI_FONT_CANDIDATES)

    def test_text_snaps_to_whole_pixels_and_dpi_helpers(self) -> None:
        self.assertEqual(app.snap_text_xy(10.6, 11.2), (11, 11))
        self.assertEqual(app.sharp_px(13, 1), 13)
        self.assertEqual(app.sharp_px(13, 1.5), 20)
        self.assertGreaterEqual(app.sharp_px(1, 0.2), 1)
        if sys.platform == "win32":
            self.assertIn(
                app.enable_windows_dpi_awareness(),
                {"", "per-monitor-v2", "per-monitor", "system"},
            )
        else:
            self.assertEqual(app.enable_windows_dpi_awareness(), "")

    def test_tk_uses_pixel_font_and_monitor_scaling(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            scale = app.apply_tk_dpi_scaling(root)
            self.assertGreaterEqual(scale, 1.0)
            scaling = float(root.tk.call("tk", "scaling"))
            self.assertGreater(scaling, 0.5)
            app.Fonts.init(root)
            self.assertIn(app.Fonts.family, (*app.UI_FONT_CANDIDATES, "TkDefaultFont"))
            self.assertLess(int(app.Fonts.body.cget("size")), 0)
            self.assertEqual(int(app.Fonts.body.cget("size")), -app.ui_px(13))
        finally:
            root.destroy()


class TaskChromeTests(unittest.TestCase):
    def _root(self) -> tk.Tk:
        root = tk.Tk()
        root.withdraw()
        app.Fonts.init(root)
        return root

    def test_header_pause_label_and_no_folder_button(self) -> None:
        root = self._root()
        try:
            opened: list[str] = []
            tasks = app.TaskList(root, on_stop=lambda: opened.append("pause"), on_open_folder=lambda: opened.append("folder"))
            tasks.pack()
            tasks.set_header("下载中 1/1", show_stop=True, show_open=True)
            root.update_idletasks()
            self.assertEqual(tasks.btn_stop._text, "全部暂停")
            self.assertEqual(tasks.btn_stop.winfo_manager(), "pack")
            self.assertFalse(hasattr(tasks, "btn_open_folder"))
            header_labels = []
            for child in tasks._header.winfo_children():
                try:
                    header_labels.append(str(child.cget("text")))
                except tk.TclError:
                    continue
            self.assertNotIn("打开文件夹", header_labels)
            self.assertNotIn("停止", header_labels)
            tasks.btn_stop._on_click()
            self.assertEqual(opened, ["pause"])
            source = (ROOT / "app.py").read_text(encoding="utf-8")
            self.assertIn('FlatButton(path, "打开文件夹"', source)
        finally:
            root.destroy()

    def test_row_stop_button_says_pause(self) -> None:
        root = self._root()
        try:
            stopped: list[str] = []
            row = app.TaskRow(root, on_stop_one=lambda key: stopped.append(key))
            row.pack()
            row.key = "k1"
            self.assertEqual(row.btn_stop._text, "暂停")
            self.assertEqual(row.btn_remove._text, "删除")
            row.update_row("剪辑.mp4", "running", current=10, total=40, speed=1000)
            root.update_idletasks()
            self.assertEqual(row.btn_stop.winfo_manager(), "pack")
            row.btn_stop._on_click()
            self.assertEqual(stopped, ["k1"])
            row.update_row("剪辑.mp4", "done", current=40, total=40)
            self.assertEqual(row.btn_stop.winfo_manager(), "")
        finally:
            root.destroy()


class TaskHintTests(unittest.TestCase):
    def test_fly_hint_does_not_place_a_click_target(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        fly = _app_methods()["_fly_task_to_links"]
        for node in ast.walk(fly):
            if isinstance(node, ast.Attribute) and node.attr in {"place", "lift"}:
                self.fail(f"task hint still uses {node.attr}()")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Canvas":
                self.fail("task hint still builds a canvas")

        root = tk.Tk()
        root.geometry("480x320+0+0")
        try:
            listing = tk.Frame(root, width=220, height=180, bg="#FFFFFF")
            listing.place(x=140, y=80)
            chip = tk.Canvas(root, width=96, height=30, bg="#005FB8", highlightthickness=0)
            chip.place(x=200, y=200)
            # Canvas.lift is tag_raise and cannot raise the window. The old hint
            # called it anyway, which threw and left the placed chip in place.
            root.tk.call("raise", chip._w)
            root.update_idletasks()

            class Nav:
                def __init__(self) -> None:
                    self.badge = None
                    self.pulses = 0

                def set_badge(self, count: int) -> None:
                    self.badge = count

                def pulse_badge(self) -> None:
                    self.pulses += 1

            class Host:
                def __init__(self) -> None:
                    self._fly_after = None
                    self._fly_chip = chip
                    self._page = "chats"
                    self._busy = True
                    self.nav_links = Nav()
                    self.tasks = type("T", (), {"counts": lambda self: (0, 1, 1)})()

                def after_cancel(self, ident: str) -> None:
                    root.after_cancel(ident)

            host = Host()
            host._cancel_task_fly = lambda: app.App._cancel_task_fly(host)  # type: ignore[method-assign]
            host._refresh_links_badge = lambda: app.App._refresh_links_badge(host)  # type: ignore[method-assign]
            app.App._fly_task_to_links(host, 1)
            root.update_idletasks()
            self.assertIsNone(host._fly_chip)
            self.assertFalse(chip.winfo_exists())
            self.assertEqual(host.nav_links.badge, 1)
            self.assertEqual(host.nav_links.pulses, 1)
            for child in root.winfo_children():
                self.assertFalse(isinstance(child, tk.Canvas))
            # The list frame is still the widget under that point.
            root.update()
            x = listing.winfo_rootx() + 20
            y = listing.winfo_rooty() + 20
            hit = root.winfo_containing(x, y)
            self.assertTrue(hit is listing or (hit is not None and _is_under(hit, listing)))
        finally:
            root.destroy()


class CaptionButtonTests(unittest.TestCase):
    def test_titlebar_caption_buttons_draw(self) -> None:
        """Widget path must not be passed to canvas coordinates.

        CaptionButton used to keep its pixel width on self._w. Tk stores the
        widget path in that attribute, so create_rectangle raised
        "bad screen distance" while the title bar was built and the app
        exited before the window appeared.
        """
        root = tk.Tk()
        root.withdraw()
        try:
            app.Fonts.init(root)
            bar = tk.Frame(root)
            bar.pack()
            buttons = [app.CaptionButton(bar, kind, bg=app.C_TITLE) for kind in ("min", "max", "close")]
            for btn in buttons:
                btn.pack(side="right")
            root.update_idletasks()
            for btn in buttons:
                self.assertTrue(str(btn._w).startswith("."), btn._w)
                self.assertIsInstance(btn._width, int)
                self.assertIsInstance(btn._height, int)
                self.assertGreater(btn._width, 0)
                self.assertGreater(btn._height, 0)
                items = btn.find_all()
                self.assertGreaterEqual(len(items), 2)
                for item in items:
                    for value in btn.coords(item):
                        self.assertIsInstance(value, float)
            buttons[-1]._set_hover(True)
            buttons[-1]._set_hover(False)
            self.assertGreaterEqual(len(buttons[-1].find_all()), 2)
        finally:
            root.destroy()


class AppIconTests(unittest.TestCase):
    def test_packaged_icon_is_a_multisize_teal_folder(self) -> None:
        ico = ROOT / "app-icon.ico"
        installer = ROOT / "installer" / "app-icon.ico"
        self.assertEqual(ico.read_bytes(), installer.read_bytes())
        with Image.open(ico) as opened:
            self.assertEqual(
                set(opened.info["sizes"]),
                {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)},
            )
        with Image.open(ROOT / "app-icon-256.png") as png_file:
            png = png_file.convert("RGBA")
        with Image.open(ROOT / "app-icon-64.png") as small:
            self.assertEqual(small.size, (64, 64))
        self.assertEqual(png.size, (256, 256))
        # Rounded square: corners are transparent, the field is cyan, the folder is white.
        self.assertEqual(png.getpixel((0, 0))[3], 0)
        field = png.getpixel((128, 30))
        self.assertLess(field[0], 40)
        self.assertGreater(field[1], 150)
        self.assertGreater(field[2], field[0] + 100)
        self.assertEqual(png.getpixel((80, 60))[:3], (255, 255, 255))
        self.assertEqual(png.getpixel((200, 200))[:3], (255, 255, 255))


def _is_under(widget: tk.Misc, parent: tk.Misc) -> bool:
    while widget is not None:
        if widget is parent:
            return True
        widget = getattr(widget, "master", None)
    return False


if __name__ == "__main__":
    unittest.main()
