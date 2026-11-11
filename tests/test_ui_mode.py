# -*- coding: utf-8 -*-
"""界面风格：默认经典，切换不丢队列、代理和保存路径。不登录 Telegram。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tgcore


def _texts(widget) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        for child in node.winfo_children():
            try:
                found.append(str(child.cget("text")))
            except Exception:
                pass
            walk(child)

    walk(widget)
    return found


class UiShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name)
        self._previous = (
            tgcore.DATA_DIR,
            tgcore.CONFIG_PATH,
            tgcore.SESSION_PATH,
            tgcore.QUEUE_PATH,
            tgcore.DOWNLOAD_INDEX_PATH,
            tgcore.AVATAR_DIR,
        )
        tgcore.DATA_DIR = folder
        tgcore.CONFIG_PATH = folder / "config.json"
        tgcore.SESSION_PATH = folder / "tg_session"
        tgcore.QUEUE_PATH = folder / "download_queue.json"
        tgcore.DOWNLOAD_INDEX_PATH = folder / "downloads_index.json"
        tgcore.AVATAR_DIR = folder / "avatars"
        import app as appmod

        self.appmod = appmod
        appmod.QUEUE_PATH = tgcore.QUEUE_PATH
        appmod.DOWNLOAD_INDEX_PATH = tgcore.DOWNLOAD_INDEX_PATH
        tgcore.update_config(
            noticed_personal_use=True,
            proxy_type="socks5",
            proxy_host="127.0.0.1",
            proxy_port=7891,
            last_save_folder=str(folder / "saved"),
        )
        queue = tgcore.DownloadQueue(persist_path=tgcore.QUEUE_PATH)
        queue.upsert("run", "街访成片.mp4", "running", current=36, total=128, url="https://t.me/demo/1")
        queue.prepare_for_exit()
        self.app = appmod.App()
        self.app.update_idletasks()

    def tearDown(self) -> None:
        try:
            for after_id in list(self.app.tk.call("after", "info")):
                try:
                    self.app.after_cancel(after_id)
                except Exception:
                    pass
            self.app.destroy()
        except Exception:
            pass
        (
            tgcore.DATA_DIR,
            tgcore.CONFIG_PATH,
            tgcore.SESSION_PATH,
            tgcore.QUEUE_PATH,
            tgcore.DOWNLOAD_INDEX_PATH,
            tgcore.AVATAR_DIR,
        ) = self._previous
        self._tmp.cleanup()

    def test_default_classic_and_switch_keeps_queue_proxy_and_path(self) -> None:
        app = self.app
        self.assertEqual(app._ui_mode, "classic")
        self.assertEqual(app.geometry().split("+")[0], "880x640")
        self.assertEqual(app.nav_links.label, "链接")
        self.assertEqual(app.nav_chats.label, "对话")
        self.assertEqual(app.nav_settings.label, "设置")
        restored = app.queue.get("run")
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.status, "stopped")
        self.assertIn("未下完", restored.error)
        row = app.tasks.get("run")
        self.assertIsNotNone(row)
        assert row is not None
        view = tgcore.present_task(
            row["name"], row["status"], current=row["current"], total=row["total"], error=row["error"]
        )
        self.assertEqual(view["speed_text"], "已停止")
        self.assertEqual(view["subtitle"], "上次未完成")
        self.assertNotEqual(view["speed_text"], "已完成")
        self.assertTrue(view["show_delete"])
        self.assertFalse(view["show_stop"])
        self.assertFalse(app.tasks._rows["run"].btn_stop.winfo_ismapped())
        self.assertTrue(app.tasks._rows["run"].btn_remove.winfo_ismapped())

        app._apply_ui_mode("professional")
        app.update_idletasks()
        self.assertEqual(app._ui_mode, "professional")
        self.assertEqual(app.geometry().split("+")[0], "1040x720")
        cfg = tgcore.load_config()
        self.assertEqual(cfg.get("ui_mode"), "professional")
        self.assertEqual(cfg.get("proxy_type"), "socks5")
        self.assertEqual(cfg.get("proxy_host"), "127.0.0.1")
        self.assertEqual(cfg.get("proxy_port"), 7891)
        self.assertTrue(str(cfg.get("last_save_folder") or "").endswith("saved"))
        self.assertEqual(app.queue.get("run").status, "stopped")
        self.assertEqual(app.tasks.get("run")["status"], "stopped")
        self.assertIn("新建任务", app.btn_new_task._text)
        self.assertEqual(set(app._filter_labels), {"全部", "下载中", "已完成"})

        app._set_task_filter("已完成")
        app.update_idletasks()
        self.assertFalse(app.tasks._rows["run"].winfo_ismapped())
        app._set_task_filter("全部")
        app.update_idletasks()
        self.assertTrue(app.tasks._rows["run"].winfo_ismapped())

        app._open_new_task("links")
        app.update_idletasks()
        self.assertTrue(app._new_task.winfo_ismapped())
        app.txt_links.set_text("https://t.me/examplechannel/1842")
        app._show_new_tab("chats")
        app.update_idletasks()
        self.assertTrue(app.chat_list.winfo_exists())
        self.assertTrue(app.video_list.winfo_exists())
        link_text = app.txt_links.get_text()

        app.var_folder.set(str(cfg["last_save_folder"]))
        app._apply_ui_mode("classic")
        app.update_idletasks()
        self.assertEqual(app._ui_mode, "classic")
        self.assertEqual(app.geometry().split("+")[0], "880x640")
        self.assertEqual(tgcore.load_config().get("ui_mode"), "classic")
        self.assertEqual(tgcore.load_config().get("proxy_port"), 7891)
        self.assertEqual(app.queue.get("run").status, "stopped")
        self.assertEqual(app.tasks.get("run")["status"], "stopped")
        self.assertIn("examplechannel/1842", app.txt_links.get_text())
        self.assertEqual(link_text, app.txt_links.get_text())
        self.assertTrue(str(app.var_folder.get()).endswith("saved"))

    def test_entry_points_and_downloaded_badge(self) -> None:
        app = self.app
        api_text = "\n".join(_texts(app.dlg_api))
        self.assertNotIn("打开申请页", api_text)
        self.assertIn("只保存在本机", api_text)
        phone_text = "\n".join(_texts(app.dlg_phone))
        self.assertIn("更改 API 凭据", phone_text)
        proxy_text = "\n".join(_texts(app.dlg_proxy))
        self.assertIn("改完需重启再生效", proxy_text)
        about_text = "\n".join(_texts(app.dlg_about))
        self.assertIn("1.4.0", about_text)
        self.assertIn("XxxLllattice", about_text)
        style_text = "\n".join(_texts(app.dlg_appearance))
        self.assertIn("浅色，侧栏链接/对话", style_text)
        self.assertIn("深色，首页任务列表", style_text)

        app._open_settings_menu()
        app.update_idletasks()
        menu = "\n".join(_texts(app._flyout))
        self.assertIn("代理", menu)
        self.assertIn("界面风格", menu)
        self.assertIn("关于", menu)
        app._close_flyout()

        from tgcore import VideoItem

        item = VideoItem(
            message_id=8,
            date=None,
            duration=4,
            size=100,
            filename="片头花絮.mp4",
            caption="片头花絮",
            kind="video",
            downloaded=True,
        )
        row = self.appmod.VideoRow(app, item, lambda _item: None)
        app.update_idletasks()
        self.assertEqual(row.lbl_done.cget("text"), "已下载")
        self.assertNotIn("peer", row.lbl_done.cget("text"))
        self.assertNotIn("message", row.lbl_done.cget("text").lower())
        row.destroy()

        app.tasks.upsert("live", "夜间街景.mp4", "running", current=10, total=40, speed=1000, wait_seconds=32)
        app.update_idletasks()
        waiting = app.tasks._rows["live"]
        self.assertTrue(waiting.btn_stop.winfo_ismapped())
        speed_text = waiting.lbl_stat.cget("text")
        if hasattr(waiting, "lbl_speed"):
            speed_text += waiting.lbl_speed.cget("text")
        self.assertIn("等待 32 秒", speed_text)
        self.assertIn("频率限制，可停止", waiting.lbl_sub.cget("text"))

        app._logged_in = True
        app._account_name = "小梁"
        app._on_account_click()
        app.update_idletasks()
        account = "\n".join(_texts(app._flyout))
        self.assertIn("代理", account)
        self.assertIn("切换账号", account)
        self.assertIn("退出登录", account)
        app._close_flyout()
        app._logged_in = False

        def _cancel() -> None:
            for child in app.winfo_children():
                try:
                    if child.winfo_exists() and str(child.title()) == "删除任务":
                        child.destroy()
                except Exception:
                    pass

        app.after(30, _cancel)
        self.assertFalse(app._confirm_delete("街访成片.mp4"))


if __name__ == "__main__":
    unittest.main()
