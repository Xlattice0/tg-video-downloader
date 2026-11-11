# -*- coding: utf-8 -*-
"""TG视频下载 — Windows 桌面端主程序（tkinter + sv-ttk）。"""

from __future__ import annotations

import asyncio
import io
import math
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

import sv_ttk
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from PIL import Image, ImageDraw, ImageTk

from tgcore import (
    APP_NAME,
    APP_USER_MODEL_ID,
    APP_VERSION,
    DEFAULT_DOWNLOADS,
    DialogItem,
    FloodWaitError,
    GITHUB_HANDLE,
    GITHUB_URL,
    NeedPasswordError,
    QUALITY_HIGHEST,
    TelegramService,
    UserFacingError,
    VideoItem,
    X_HANDLE,
    X_URL,
    build_save_name,
    clear_credentials,
    clear_session_only,
    detect_local_proxies,
    DownloadQueue,
    VideoScanState,
    explain_error,
    filter_dialogs,
    format_bytes,
    format_date_short,
    format_datetime,
    format_duration_short,
    format_proxy_label,
    format_speed,
    has_api_credentials,
    is_user_stop,
    link_failure_text,
    load_cached_avatar,
    load_config,
    login_dialog_after_session_loss,
    message_video_kind,
    original_media_name,
    pick_best_proxy,
    pick_video_quality,
    plan_link_jobs,
    proxy_from_config,
    quality_pref_label,
    quality_pref_value,
    split_link_text,
    startup_login_target,
    test_proxy_connection,
    update_config,
    validate_proxy_settings,
    video_display_title,
    visible_chat_selection,
)

# ------------------------------------------------------------------ palette
C_BG = "#F3F3F3"
C_CARD = "#FFFFFF"
C_TITLE = "#F9F9F9"
C_TEXT = "#1A1A1A"
C_TEXT2 = "#5D5D5D"
C_TEXT3 = "#8A8A8A"
C_STROKE = "#E5E5E5"
C_STROKE2 = "#D1D1D1"
C_ACCENT = "#0F6CBD"
C_HOVER = "#115EA3"
C_SEL = "#E6F2FB"
C_OK = "#0F7B0F"
C_ERR = "#C42B1C"
C_BAR = "#EEEEEE"
C_BADGE = "#F1F1F1"
C_BADGE_SEL = "#D7E8F6"
AVATAR_COLORS = ("#5B7C99", "#6B8F71", "#8A6D4A", "#7A6B99", "#6A7A8A")

PROXY_LABEL_TO_KIND = {"直连": "none", "SOCKS5": "socks5", "HTTP": "http"}
PROXY_KIND_TO_LABEL = {v: k for k, v in PROXY_LABEL_TO_KIND.items()}
PROXY_KIND_TO_LABEL["off"] = "直连"
PROXY_KIND_TO_LABEL["direct"] = "直连"
PROXY_KIND_TO_LABEL[""] = "直连"


def resource_path(*names: str) -> Optional[Path]:
    """定位打包进 exe 或项目目录里的资源。"""
    bases: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass))
        bases.append(Path(sys.executable).resolve().parent)
    here = Path(__file__).resolve().parent
    bases.extend([here, here / "installer"])
    for base in bases:
        for name in names:
            path = Path(base) / name
            if path.is_file():
                return path
    return None


def apply_window_icon(win: tk.Tk | tk.Toplevel, photos: list) -> None:
    ico = resource_path("app-icon.ico")
    if ico:
        try:
            if isinstance(win, tk.Tk):
                win.iconbitmap(default=str(ico))
            win.iconbitmap(str(ico))
        except Exception:
            pass
        if sys.platform == "win32":
            _set_win_native_icon(win, ico)
            _set_win_relaunch_props(win, ico)
            return
    # Windows 上不要再用 iconphoto：它经常把任务栏里的 .ico 冲掉，变成空白图标。
    png = resource_path("app-icon-256.png", "app-icon-64.png")
    if png:
        try:
            img = tk.PhotoImage(file=str(png))
            photos.append(img)
            win.iconphoto(True, img)
        except Exception:
            pass


def _set_win_native_icon(win: tk.Misc, ico: Path) -> None:
    """把多尺寸 .ico 设到顶层窗口，确保任务栏和标题栏都显示应用图标。"""
    import ctypes
    from ctypes import wintypes

    try:
        win.update_idletasks()
        hwnd = int(win.winfo_id())
    except Exception:
        return

    # Windows 上 long 是 32 位，句柄必须按指针宽度传递，否则 64 位 HICON 会被截断。
    PTR = ctypes.c_void_p
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.LoadImageW.restype = PTR
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, PTR]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    if hasattr(user32, "SetClassLongPtrW"):
        user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, PTR]
        user32.SetClassLongPtrW.restype = PTR
        set_class_long = user32.SetClassLongPtrW
    else:
        user32.SetClassLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetClassLongW.restype = ctypes.c_ulong
        set_class_long = user32.SetClassLongW
    shell32.ExtractIconExW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(PTR),
        ctypes.POINTER(PTR),
        wintypes.UINT,
    ]
    shell32.ExtractIconExW.restype = wintypes.UINT

    GA_ROOT = 2
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040
    WM_SETICON = 0x0080
    ICON_SMALL = 0
    ICON_BIG = 1
    SM_CXICON = 11
    SM_CXSMICON = 49
    GCLP_HICON = -14
    GCLP_HICONSM = -34

    def as_handle(value) -> int:
        if not value:
            return 0
        if isinstance(value, int):
            return value
        return int(getattr(value, "value", 0) or 0)

    try:
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        path = str(ico)
        cx_big = user32.GetSystemMetrics(SM_CXICON) or 32
        cx_sm = user32.GetSystemMetrics(SM_CXSMICON) or 16
        h_small = as_handle(user32.LoadImageW(None, path, IMAGE_ICON, cx_sm, cx_sm, LR_LOADFROMFILE))
        h_big = as_handle(user32.LoadImageW(None, path, IMAGE_ICON, cx_big, cx_big, LR_LOADFROMFILE))
        if not h_small:
            h_small = as_handle(
                user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            )
        if not h_big:
            h_big = as_handle(user32.LoadImageW(None, path, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)) or h_small
        if (not h_small or not h_big) and getattr(sys, "frozen", False):
            big = PTR()
            small = PTR()
            exe = str(Path(sys.executable).resolve())
            if shell32.ExtractIconExW(exe, 0, ctypes.byref(big), ctypes.byref(small), 1):
                h_big = h_big or as_handle(big)
                h_small = h_small or as_handle(small)
        targets = []
        for candidate in (root, hwnd):
            value = as_handle(candidate)
            if value and value not in targets:
                targets.append(value)
        for target in targets:
            if h_small:
                user32.SendMessageW(target, WM_SETICON, ICON_SMALL, h_small)
                set_class_long(target, GCLP_HICONSM, h_small)
            if h_big:
                user32.SendMessageW(target, WM_SETICON, ICON_BIG, h_big)
                set_class_long(target, GCLP_HICON, h_big)
        # 保留句柄，避免图标被过早释放
        handles = [h for h in (h_small, h_big) if h]
        existing = getattr(win, "_native_icon_handles", None) or []
        win._native_icon_handles = list(existing) + handles  # type: ignore[attr-defined]
    except Exception:
        pass



def _set_win_relaunch_props(win: tk.Misc, ico: Path) -> None:
    """把任务栏图标钉到本程序的 .ico / exe，避免旧快捷方式把图标显示成空白页。"""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    try:
        win.update_idletasks()
        hwnd = int(win.winfo_id())
    except Exception:
        return

    PTR = ctypes.c_void_p
    user32 = ctypes.windll.user32
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_uint16),
            ("reserved1", ctypes.c_uint16),
            ("reserved2", ctypes.c_uint16),
            ("reserved3", ctypes.c_uint16),
            ("pwszVal", ctypes.c_wchar_p),
            ("_pad", ctypes.c_void_p),
        ]

    class IPropertyStoreVtbl(ctypes.Structure):
        _fields_ = [
            ("QueryInterface", PTR),
            ("AddRef", PTR),
            ("Release", ctypes.WINFUNCTYPE(ctypes.c_ulong, PTR)),
            ("GetCount", PTR),
            ("GetAt", PTR),
            ("GetValue", PTR),
            ("SetValue", ctypes.WINFUNCTYPE(ctypes.c_long, PTR, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))),
            ("Commit", ctypes.WINFUNCTYPE(ctypes.c_long, PTR)),
        ]

    class IPropertyStore(ctypes.Structure):
        _fields_ = [("lpVtbl", ctypes.POINTER(IPropertyStoreVtbl))]

    def make_guid(text: str) -> GUID:
        parts = text.strip("{}").split("-")
        data4 = bytes.fromhex(parts[3] + parts[4])
        return GUID(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), (ctypes.c_ubyte * 8).from_buffer_copy(data4))

    FMTID = make_guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3")
    IID_IPropertyStore = make_guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
    PKEY_RelaunchCommand = PROPERTYKEY(FMTID, 2)
    PKEY_RelaunchIconResource = PROPERTYKEY(FMTID, 3)
    PKEY_RelaunchDisplayName = PROPERTYKEY(FMTID, 4)
    PKEY_AppUserModel_ID = PROPERTYKEY(FMTID, 5)
    VT_LPWSTR = 31
    GA_ROOT = 2
    S_OK = 0
    S_FALSE = 1
    COINIT_APARTMENTTHREADED = 0x2

    exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    icon_res = str(ico)
    if ico.exists():
        icon_res = str(ico)
    elif exe:
        icon_res = f"{exe},0"
    display = APP_NAME
    command = f'"{exe}"' if exe else ""
    app_id = APP_USER_MODEL_ID

    store_ptr = PTR()
    initialized = False
    try:
        hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        initialized = hr in (S_OK, S_FALSE)
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(PTR)]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
        hr = shell32.SHGetPropertyStoreForWindow(root, ctypes.byref(IID_IPropertyStore), ctypes.byref(store_ptr))
        if hr != S_OK or not store_ptr.value:
            return
        store = ctypes.cast(store_ptr, ctypes.POINTER(IPropertyStore))
        vtbl = store.contents.lpVtbl.contents

        def set_str(key: PROPERTYKEY, value: str) -> None:
            if not value:
                return
            variant = PROPVARIANT()
            variant.vt = VT_LPWSTR
            variant.pwszVal = value
            vtbl.SetValue(store_ptr, ctypes.byref(key), ctypes.byref(variant))

        set_str(PKEY_AppUserModel_ID, app_id)
        set_str(PKEY_RelaunchIconResource, icon_res)
        set_str(PKEY_RelaunchDisplayName, display)
        if command:
            set_str(PKEY_RelaunchCommand, command)
        vtbl.Commit(store_ptr)
        vtbl.Release(store_ptr)
    except Exception:
        pass
    finally:
        if initialized:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def _tray_image():
    from PIL import Image as _Image

    path = resource_path("app-icon.ico", "app-icon-64.png", "app-icon-256.png")
    if path is None:
        return _Image.new("RGBA", (32, 32), (15, 108, 189, 255))
    img = _Image.open(path)
    best = None
    try:
        index = 0
        while True:
            try:
                img.seek(index)
            except EOFError:
                break
            frame = img.copy()
            if frame.size == (32, 32):
                best = frame
                break
            if best is None or abs(frame.size[0] - 32) < abs(best.size[0] - 32):
                best = frame
            index += 1
    except Exception:
        best = img
    chosen = best or img
    if chosen.mode != "RGBA":
        chosen = chosen.convert("RGBA")
    if chosen.size != (32, 32):
        chosen = chosen.resize((32, 32), _Image.Resampling.LANCZOS)
    return chosen


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """Group Start Menu / taskbar / Search entries under one app identity (Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
    except Exception:
        pass


def ui_scale(widget: Optional[tk.Misc] = None) -> float:
    """Logical 1.0 at 96 DPI. Used so covers/avatars are not upscaled from tiny bitmaps."""
    try:
        target = widget
        if target is None:
            target = tk._get_default_root()  # type: ignore[attr-defined]
        if target is None:
            return 1.0
        scaling = float(target.tk.call("tk", "scaling"))
        return max(1.0, scaling / (96.0 / 72.0))
    except Exception:
        return 1.0


def scaled_px(logical: int, widget: Optional[tk.Misc] = None) -> int:
    return max(1, int(round(logical * ui_scale(widget))))


def first_glyph(title: str) -> str:
    text = (title or "").strip()
    return text[0] if text else "?"


def avatar_color(title: str) -> str:
    acc = 0
    for ch in title or "":
        acc = (acc * 31 + ord(ch)) & 0xFFFFFFFF
    return AVATAR_COLORS[acc % len(AVATAR_COLORS)]


def message_file_size(msg: Any) -> Optional[int]:
    for obj in (
        getattr(msg, "document", None),
        getattr(msg, "file", None),
        getattr(msg, "video", None),
        getattr(msg, "video_note", None),
        getattr(msg, "gif", None),
    ):
        size = getattr(obj, "size", None) if obj is not None else None
        if size is not None:
            try:
                return int(size)
            except (TypeError, ValueError):
                continue
    return None



def preferred_media_size(msg: Any, pref: Optional[str] = None) -> Optional[int]:
    from tgcore import list_video_qualities
    chosen = pick_video_quality(list_video_qualities(msg), pref)
    if chosen and chosen.size:
        return chosen.size
    return message_file_size(msg)

def task_display_name(msg: Any, chat_title: str) -> str:
    original = original_media_name(msg)
    if original:
        return Path(original).name
    return build_save_name(msg, chat_title)


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kwargs) -> int:
    r = max(0.0, min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2))
    points = (
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=16, **kwargs)


def bind_mousewheel(widget: tk.Widget, target: "ScrollArea | tk.Canvas") -> None:
    area = target if isinstance(target, ScrollArea) else None
    canvas = target.canvas if isinstance(target, ScrollArea) else target

    def _on_wheel(event: tk.Event) -> str | None:
        if area is not None and not area.can_scroll():
            return "break"
        if getattr(event, "delta", 0):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _on_up(_event: tk.Event) -> str | None:
        if area is not None and not area.can_scroll():
            return "break"
        canvas.yview_scroll(-3, "units")
        return "break"

    def _on_down(_event: tk.Event) -> str | None:
        if area is not None and not area.can_scroll():
            return "break"
        canvas.yview_scroll(3, "units")
        return "break"

    widget.bind("<MouseWheel>", _on_wheel, add="+")
    widget.bind("<Button-4>", _on_up, add="+")
    widget.bind("<Button-5>", _on_down, add="+")


class Fonts:
    family = "Microsoft YaHei UI"
    title: Any = None
    body: Any = None
    body_bold: Any = None
    help: Any = None
    aux: Any = None
    aux_bold: Any = None
    nav: Any = None
    nav_active: Any = None
    badge: Any = None
    titlebar: Any = None

    @classmethod
    def init(cls, root: tk.Tk) -> None:
        preferred = (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "PingFang SC",
            "Segoe UI",
        )
        available = set(tkfont.families(root))
        cls.family = next((n for n in preferred if n in available), "TkDefaultFont")

        def make(size: int, weight: str = "normal") -> tkfont.Font:
            return tkfont.Font(root=root, family=cls.family, size=-size, weight=weight)

        cls.titlebar = make(12, "bold")
        cls.title = make(13, "bold")
        cls.body = make(13)
        cls.body_bold = make(13, "bold")
        cls.help = make(12)
        cls.aux = make(11)
        cls.aux_bold = make(11, "bold")
        cls.nav = make(11)
        cls.nav_active = make(11, "bold")
        cls.badge = make(10)


class PlaceholderEntry(tk.Entry):
    def __init__(
        self,
        master: tk.Misc,
        placeholder: str = "",
        textvariable: Optional[tk.StringVar] = None,
        show: str = "",
        **kwargs: Any,
    ) -> None:
        self._placeholder = placeholder
        self._real_show = show
        self._user_var = textvariable
        self._var = tk.StringVar()
        self._showing = False
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("insertbackground", C_TEXT)
        kwargs.setdefault("fg", C_TEXT)
        kwargs.setdefault("bg", C_CARD)
        super().__init__(master, textvariable=self._var, **kwargs)
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<KeyRelease>", self._sync_user, add="+")
        if self._user_var is not None:
            self._user_var.trace_add("write", self._from_user)
        initial = self._user_var.get() if self._user_var is not None else ""
        if initial:
            self._var.set(initial)
            self.configure(show=self._real_show, fg=C_TEXT)
        else:
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self._showing = True
        self.configure(show="", fg=C_TEXT3)
        self._var.set(self._placeholder)

    def _hide_placeholder(self) -> None:
        if not self._showing:
            return
        self._showing = False
        self._var.set("")
        self.configure(show=self._real_show, fg=C_TEXT)

    def _on_focus_in(self, _event: tk.Event | None = None) -> None:
        if self._showing:
            self._hide_placeholder()

    def _on_focus_out(self, _event: tk.Event | None = None) -> None:
        if not self._var.get():
            self._show_placeholder()

    def _sync_user(self, _event: tk.Event | None = None) -> None:
        if self._showing or self._user_var is None:
            return
        if self._user_var.get() != self._var.get():
            self._user_var.set(self._var.get())

    def _from_user(self, *_args: Any) -> None:
        if self._user_var is None or self._showing:
            return
        value = self._user_var.get()
        if value != self._var.get():
            self._var.set(value)

    def get(self) -> str:  # type: ignore[override]
        if self._showing:
            return ""
        return self._var.get()

    def set(self, value: str) -> None:
        if value:
            self._showing = False
            self.configure(show=self._real_show, fg=C_TEXT)
            self._var.set(value)
            if self._user_var is not None:
                self._user_var.set(value)
        else:
            if self._user_var is not None:
                self._user_var.set("")
            if self.focus_get() is self:
                self._showing = False
                self.configure(show=self._real_show, fg=C_TEXT)
                self._var.set("")
            else:
                self._show_placeholder()


class PlaceholderText(tk.Text):
    def __init__(self, master: tk.Misc, placeholder: str = "", **kwargs: Any) -> None:
        self._placeholder = placeholder
        self._showing = False
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("insertbackground", C_TEXT)
        kwargs.setdefault("fg", C_TEXT)
        kwargs.setdefault("bg", C_CARD)
        kwargs.setdefault("wrap", "word")
        kwargs.setdefault("undo", True)
        super().__init__(master, **kwargs)
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<KeyRelease>", self._on_key, add="+")
        # 在默认粘贴之前清掉占位符，避免把占位文案和内容叠在一起，或把刚贴的内容删掉
        self.bind("<<Paste>>", self._on_paste)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self._showing = True
        self.delete("1.0", "end")
        self.insert("1.0", self._placeholder)
        self.configure(fg=C_TEXT3)

    def _hide_placeholder(self) -> None:
        if not self._showing:
            return
        self._showing = False
        self.delete("1.0", "end")
        self.configure(fg=C_TEXT)

    def _on_focus_in(self, _event: tk.Event | None = None) -> None:
        if self._showing:
            self._hide_placeholder()

    def _on_focus_out(self, _event: tk.Event | None = None) -> None:
        if not self.get_text():
            self._show_placeholder()

    def _on_key(self, _event: tk.Event | None = None) -> None:
        if self._showing and self.get("1.0", "end-1c") != self._placeholder:
            self._showing = False
            self.configure(fg=C_TEXT)

    def _on_paste(self, _event: tk.Event | None = None) -> None:
        if self._showing:
            self._hide_placeholder()
        # 不 return "break"，让 Text 的默认粘贴继续执行

    def get_text(self) -> str:
        if self._showing:
            return ""
        return self.get("1.0", "end-1c")


class Field(tk.Frame):
    """白底描边输入容器，高度 32。"""

    def __init__(self, master: tk.Misc, height: int = 32, **kwargs: Any) -> None:
        super().__init__(
            master,
            bg=C_CARD,
            highlightthickness=1,
            highlightbackground=C_STROKE2,
            highlightcolor=C_ACCENT,
            **kwargs,
        )
        if height:
            self.configure(height=height)
            self.pack_propagate(False)


class FlatButton(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Optional[Callable[[], None]] = None,
        variant: str = "secondary",
        width: Optional[int] = None,
        height: int = 32,
        radius: int = 6,
        font: Any = None,
        padx: int = 14,
        bg: Optional[str] = None,
    ) -> None:
        self._text = text
        self._command = command
        self._variant = variant
        self._height = height
        self._radius = radius
        self._font = font or Fonts.body
        self._state = "normal"
        self._hover = False
        self._padx = padx
        self._min_width = width
        self._parent_bg = bg or _widget_bg(master)
        tw = self._font.measure(text) if hasattr(self._font, "measure") else len(text) * 8
        self._width = width or max(tw + padx * 2, 64)
        super().__init__(
            master,
            width=self._width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=self._parent_bg,
            cursor="hand2",
        )
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._redraw()

    def set_command(self, command: Optional[Callable[[], None]]) -> None:
        self._command = command

    def set_state(self, state: str) -> None:
        self._state = state
        self.configure(cursor="hand2" if state == "normal" else "")
        self._redraw()

    def set_text(self, text: str) -> None:
        self._text = text
        measure_font = Fonts.body_bold if self._variant == "primary" and Fonts.body_bold else self._font
        tw = measure_font.measure(text) if hasattr(measure_font, "measure") else len(text) * 8
        self._width = self._min_width or max(tw + self._padx * 2, 64)
        self.configure(width=self._width)
        self._redraw()

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self._hover = False
        self._redraw()

    def _on_click(self, _event: tk.Event | None = None) -> None:
        if self._state == "normal" and self._command:
            self._command()

    def _colors(self) -> tuple[str, str, str]:
        if self._state != "normal":
            if self._variant == "primary":
                return "#8BB8DE", "#FFFFFF", "#8BB8DE"
            return C_CARD, "#B0B0B0", C_STROKE2
        if self._variant == "primary":
            fill = C_HOVER if self._hover else C_ACCENT
            return fill, "#FFFFFF", fill
        fill = "#F5F5F5" if self._hover else C_CARD
        return fill, C_TEXT, C_STROKE2

    def _redraw(self) -> None:
        self.delete("all")
        fill, fg, outline = self._colors()
        font = Fonts.body_bold if self._variant == "primary" else self._font
        rounded_rect(
            self,
            0.5,
            0.5,
            self._width - 0.5,
            self._height - 0.5,
            self._radius,
            fill=fill,
            outline=outline,
        )
        self.create_text(self._width / 2, self._height / 2, text=self._text, fill=fg, font=font)


def _widget_bg(widget: tk.Misc) -> str:
    try:
        value = widget.cget("bg")
        if value:
            return str(value)
    except tk.TclError:
        pass
    return C_BG


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = (color or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return (90, 90, 90)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rounded_link_rect(size: int, color: tuple[int, int, int], width: float) -> Image.Image:
    """A single chain-link (rounded rectangle outline) on a transparent square."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size * 0.28
    box = (pad, size * 0.38, size - pad, size * 0.62)
    radius = (box[3] - box[1]) / 2
    draw.rounded_rectangle(box, radius=radius, outline=color + (255,), width=max(1, int(round(width))))
    return img


def make_nav_icon(kind: str, color: str, logical: int = 20, widget: Optional[tk.Misc] = None) -> ImageTk.PhotoImage:
    """Fluent-style 20px linear icons, rendered at 4x then downscaled for sharpness."""
    px = scaled_px(logical, widget)
    over = 4
    s = px * over
    rgb = _hex_rgb(color)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    stroke = max(over * 1.15, s * 0.075)

    if kind == "link":
        # Two interlocking 45° chain links (Fluent Link).
        link = _rounded_link_rect(s, rgb, stroke)
        a = link.rotate(45, resample=Image.Resampling.BICUBIC, expand=False)
        b = link.rotate(45, resample=Image.Resampling.BICUBIC, expand=False)
        canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        # offset the two links along the diagonal
        off = int(s * 0.16)
        canvas.alpha_composite(a, (-off, off))
        canvas.alpha_composite(b, (off, -off))
        img = canvas
    elif kind == "chat":
        # Rounded speech bubble with a short tail; stroke only.
        m = s * 0.16
        bubble = (m, m * 0.85, s - m, s * 0.70)
        r = s * 0.16
        draw.rounded_rectangle(bubble, radius=r, outline=rgb + (255,), width=max(1, int(round(stroke))))
        tail = [
            (s * 0.30, s * 0.68),
            (s * 0.24, s * 0.86),
            (s * 0.46, s * 0.68),
        ]
        draw.line(tail[0] + tail[1], fill=rgb + (255,), width=max(1, int(round(stroke))), joint="curve")
        draw.line(tail[1] + tail[2], fill=rgb + (255,), width=max(1, int(round(stroke))), joint="curve")
        # caps
        rad = max(1, int(round(stroke * 0.55)))
        for x, y in tail:
            draw.ellipse((x - rad, y - rad, x + rad, y + rad), fill=rgb + (255,))
    elif kind == "user":
        sw = max(1, int(round(stroke)))
        cx = s / 2
        head_cy = s * 0.34
        head_r = s * 0.16
        draw.ellipse(
            (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
            outline=rgb + (255,),
            width=sw,
        )
        body = (s * 0.18, s * 0.58, s * 0.82, s * 1.18)
        draw.arc(body, start=200, end=340, fill=rgb + (255,), width=sw)
    elif kind == "proxy":
        # Globe: meridians + equator (Fluent Globe).
        sw = max(1, int(round(stroke)))
        cx = cy = s / 2
        r = s * 0.36
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=rgb + (255,), width=sw)
        draw.ellipse((cx - r * 0.42, cy - r, cx + r * 0.42, cy + r), outline=rgb + (255,), width=sw)
        draw.arc((cx - r, cy - r * 0.42, cx + r, cy + r * 0.42), start=200, end=340, fill=rgb + (255,), width=sw)
        draw.arc((cx - r, cy - r * 0.42, cx + r, cy + r * 0.42), start=20, end=160, fill=rgb + (255,), width=sw)
        draw.line((cx - r, cy, cx + r, cy), fill=rgb + (255,), width=sw)
    elif kind == "about":
        # Circled i.
        sw = max(1, int(round(stroke)))
        cx = cy = s / 2
        r = s * 0.36
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=rgb + (255,), width=sw)
        dot = max(1.6 * over, sw * 0.7)
        draw.ellipse((cx - dot, cy - r * 0.48 - dot, cx + dot, cy - r * 0.48 + dot), fill=rgb + (255,))
        stem_w = max(sw, over * 1.2)
        y1, y2 = cy - r * 0.12, cy + r * 0.42
        draw.line((cx, y1, cx, y2), fill=rgb + (255,), width=int(stem_w))
        rad = max(1, int(round(stem_w / 2)))
        for y in (y1, y2):
            draw.ellipse((cx - rad, y - rad, cx + rad, y + rad), fill=rgb + (255,))
    else:
        # 6-tooth gear, stroke only, with a clean inner ring.
        cx = cy = s / 2
        teeth = 6
        r_outer = s * 0.42
        r_valley = s * 0.30
        r_hole = s * 0.13
        tooth_half = 12  # degrees
        pts: list[tuple[float, float]] = []
        for i in range(teeth):
            mid = -90 + i * (360 / teeth)
            a0 = math.radians(mid - tooth_half)
            a1 = math.radians(mid + tooth_half)
            b0 = math.radians(mid + (360 / teeth) / 2 - tooth_half * 0.55)
            b1 = math.radians(mid + (360 / teeth) / 2 + tooth_half * 0.55)
            # outer tooth corners then valley
            pts.append((cx + r_outer * math.cos(a0), cy + r_outer * math.sin(a0)))
            pts.append((cx + r_outer * math.cos(a1), cy + r_outer * math.sin(a1)))
            pts.append((cx + r_valley * math.cos(b0), cy + r_valley * math.sin(b0)))
            pts.append((cx + r_valley * math.cos(b1), cy + r_valley * math.sin(b1)))
        # close
        draw.line(pts + [pts[0]], fill=rgb + (255,), width=max(1, int(round(stroke))), joint="curve")
        for x, y in pts:
            rad = max(1, int(round(stroke * 0.45)))
            draw.ellipse((x - rad, y - rad, x + rad, y + rad), fill=rgb + (255,))
        draw.ellipse(
            (cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole),
            outline=rgb + (255,),
            width=max(1, int(round(stroke))),
        )

    img = img.resize((px, px), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(img)


def draw_link_icon(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 20) -> ImageTk.PhotoImage:
    photo = make_nav_icon("link", color, size, canvas)
    canvas.create_image(x, y, anchor="nw", image=photo)
    return photo


def draw_chat_icon(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 20) -> ImageTk.PhotoImage:
    photo = make_nav_icon("chat", color, size, canvas)
    canvas.create_image(x, y, anchor="nw", image=photo)
    return photo


def draw_gear_icon(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 20) -> ImageTk.PhotoImage:
    photo = make_nav_icon("settings", color, size, canvas)
    canvas.create_image(x, y, anchor="nw", image=photo)
    return photo


def draw_search_icon(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 14) -> None:
    s = size / 14
    w = max(1.1, 1.3 * s)
    canvas.create_oval(x + 1.1 * s, y + 1.1 * s, x + 10.3 * s, y + 10.3 * s, outline=color, width=w)
    canvas.create_line(x + 9.2 * s, y + 9.2 * s, x + 13 * s, y + 13 * s, fill=color, width=w, capstyle="round")


def draw_download_icon(canvas: tk.Canvas, x: int, y: int, color: str, size: int = 12) -> None:
    s = size / 12
    w = max(1.2, 1.4 * s)
    canvas.create_line(x + 6 * s, y + 1.5 * s, x + 6 * s, y + 7.7 * s, fill=color, width=w, capstyle="round")
    canvas.create_line(x + 6 * s, y + 7.7 * s, x + 3.6 * s, y + 5.3 * s, fill=color, width=w, capstyle="round")
    canvas.create_line(x + 6 * s, y + 7.7 * s, x + 8.4 * s, y + 5.3 * s, fill=color, width=w, capstyle="round")
    canvas.create_line(x + 2.2 * s, y + 9.4 * s, x + 9.8 * s, y + 9.4 * s, fill=color, width=w, capstyle="round")


class NavButton(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        kind: str,
        label: str,
        command: Optional[Callable[[], None]] = None,
        width: int = 48,
        height: int = 56,
    ) -> None:
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0, bg=C_BG, cursor="hand2")
        self.kind = kind
        self.label = label
        self._command = command
        self.active = False
        self._hover = False
        self._width = width
        self._height = height
        self._badge = 0
        self._pulse = 0.0
        self._pulse_after: Optional[str] = None
        self.bind("<Button-1>", lambda _e: self._command() if self._command else None)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.redraw()

    def set_badge(self, count: int) -> None:
        count = max(0, int(count or 0))
        if count == self._badge:
            return
        self._badge = count
        self.redraw()

    def pulse_badge(self) -> None:
        if self._pulse_after:
            try:
                self.after_cancel(self._pulse_after)
            except Exception:
                pass
        self._pulse_t0 = time.perf_counter()
        self._animate_pulse()

    def _animate_pulse(self) -> None:
        dur = 0.32
        t = time.perf_counter() - getattr(self, "_pulse_t0", time.perf_counter())
        if t >= dur:
            self._pulse = 0.0
            self._pulse_after = None
            self.redraw()
            return
        self._pulse = math.sin(math.pi * (t / dur))
        self.redraw()
        self._pulse_after = self.after(16, self._animate_pulse)

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self.redraw()

    def set_active(self, active: bool) -> None:
        self.active = active
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        color = C_ACCENT if self.active else C_TEXT2
        font = Fonts.nav_active if self.active else Fonts.nav
        if self.active:
            rounded_rect(self, 0, 0, self._width, self._height, 6, fill=C_SEL, outline=C_SEL)
        elif self._hover:
            rounded_rect(self, 0, 0, self._width, self._height, 6, fill="#EAEAEA", outline="#EAEAEA")
        else:
            self.configure(bg=C_BG)
        icon_size = 20
        icon_x, icon_y = (self._width - icon_size) // 2, 7
        kind = self.kind if self.kind in ("link", "chat", "proxy", "about", "user", "settings") else "settings"
        self._icon_photo = make_nav_icon(kind, color, icon_size, self)
        self.create_image(icon_x, icon_y, anchor="nw", image=self._icon_photo)
        self.create_text(self._width / 2, 42, text=self.label, fill=color, font=font)
        if self._badge > 0:
            label = "99+" if self._badge > 99 else str(self._badge)
            scale = 1.0 + 0.28 * self._pulse
            r = 7.2 * scale
            cx, cy = self._width - 9, 11
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=C_ACCENT, outline="#FFFFFF", width=1.5)
            badge_font = Fonts.aux_bold or Fonts.help
            self.create_text(cx, cy, text=label, fill="#FFFFFF", font=badge_font)


class AccountButton(tk.Canvas):
    """左侧边栏底部的账号入口：未登录显示「登录」，已登录只显示头像和绿点，不显示名称。"""

    def __init__(
        self,
        master: tk.Misc,
        command: Optional[Callable[[], None]] = None,
        width: int = 48,
        height: int = 56,
    ) -> None:
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0, bg=C_BG, cursor="hand2")
        self._command = command
        self._hover = False
        self._width = width
        self._height = height
        self._logged_in = False
        self.bind("<Button-1>", lambda _e: self._command() if self._command else None)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.redraw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self.redraw()

    def set_state(self, logged_in: bool, name: str = "") -> None:
        self._logged_in = bool(logged_in)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if self._logged_in:
            color = C_TEXT if self._hover else C_TEXT2
        else:
            color = C_HOVER if self._hover else C_ACCENT
        if self._hover:
            rounded_rect(self, 0, 0, self._width, self._height, 6, fill="#EAEAEA", outline="#EAEAEA")
        else:
            self.configure(bg=C_BG)
        icon_size = 20
        if self._logged_in:
            icon_x = (self._width - icon_size) // 2
            icon_y = (self._height - icon_size) // 2
        else:
            icon_x, icon_y = (self._width - icon_size) // 2, 7
        self._icon_photo = make_nav_icon("user", color, icon_size, self)
        self.create_image(icon_x, icon_y, anchor="nw", image=self._icon_photo)
        if self._logged_in:
            r = 3.2
            cx, cy = icon_x + icon_size - 1, icon_y + 2
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=C_OK, outline="#FFFFFF", width=1.2)
        else:
            self.create_text(self._width / 2, 42, text="登录", fill=color, font=Fonts.nav_active)


SB_W = 12
SB_TRACK = "#F3F3F3"
SB_THUMB = "#8A8A8A"
SB_THUMB_HOVER = "#6E6E6E"
SB_MARGIN = 3
SB_INSET = 2
SB_MIN_THUMB = 24


class FluentScrollbar(tk.Canvas):
    """Slim Win11-style vertical bar: 12px light track, rounded #8A8A8A thumb."""

    def __init__(self, master: tk.Misc, command: Optional[Callable[..., Any]] = None) -> None:
        super().__init__(master, width=SB_W, highlightthickness=0, bd=0, bg=SB_TRACK, cursor="arrow")
        self._command = command
        self._lo = 0.0
        self._hi = 1.0
        self._hover = False
        self._dragging = False
        self._drag_offset = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._on_move)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Enter>", lambda _e: None)

    def set_command(self, command: Optional[Callable[..., Any]]) -> None:
        self._command = command

    def set(self, lo: Any, hi: Any) -> None:
        try:
            self._lo, self._hi = float(lo), float(hi)
        except (TypeError, ValueError):
            self._lo, self._hi = 0.0, 1.0
        self._redraw()

    def _thumb(self) -> tuple[float, float, float, float]:
        h = max(1, int(self.winfo_height()))
        usable = max(1, h - 2 * SB_MARGIN)
        span = max(0.0, min(1.0, self._hi) - max(0.0, self._lo))
        thumb_h = max(SB_MIN_THUMB, span * usable)
        y1 = SB_MARGIN + max(0.0, self._lo) * usable
        if y1 + thumb_h > h - SB_MARGIN:
            y1 = max(SB_MARGIN, h - SB_MARGIN - thumb_h)
        y2 = y1 + thumb_h
        return float(SB_INSET), float(y1), float(SB_W - SB_INSET), float(y2)

    def _redraw(self) -> None:
        self.delete("all")
        if self.winfo_height() <= 1:
            return
        x1, y1, x2, y2 = self._thumb()
        color = SB_THUMB_HOVER if (self._hover or self._dragging) else SB_THUMB
        r = max(2.0, (x2 - x1) / 2)
        rounded_rect(self, x1, y1, x2, y2, r, fill=color, outline=color)

    def _hit_thumb(self, y: float) -> bool:
        _x1, y1, _x2, y2 = self._thumb()
        return y1 <= y <= y2

    def _moveto_from_y(self, y: float, offset: float = 0.0) -> None:
        h = max(1, int(self.winfo_height()))
        usable = max(1, h - 2 * SB_MARGIN)
        _x1, y1, _x2, y2 = self._thumb()
        thumb_h = y2 - y1
        top = min(max(SB_MARGIN, y - offset), h - SB_MARGIN - thumb_h)
        lo = (top - SB_MARGIN) / usable
        lo = max(0.0, min(1.0, lo))
        if self._command:
            self._command("moveto", lo)

    def _press(self, event: tk.Event) -> str | None:
        if self._hit_thumb(event.y):
            _x1, y1, _x2, _y2 = self._thumb()
            self._dragging = True
            self._drag_offset = event.y - y1
            self._redraw()
        else:
            _x1, y1, _x2, y2 = self._thumb()
            self._moveto_from_y(event.y, (y2 - y1) / 2)
        return "break"

    def _motion(self, event: tk.Event) -> str | None:
        if self._dragging:
            self._moveto_from_y(event.y, self._drag_offset)
        else:
            self._on_move(event)
        return "break"

    def _release(self, _event: tk.Event) -> str | None:
        self._dragging = False
        self._redraw()
        return "break"

    def _on_move(self, event: tk.Event) -> None:
        hover = self._hit_thumb(event.y)
        if hover != self._hover:
            self._hover = hover
            self.configure(cursor="hand2" if hover or self._dragging else "arrow")
            self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if not self._dragging and self._hover:
            self._hover = False
            self.configure(cursor="arrow")
            self._redraw()


class ScrollArea(tk.Frame):
    """Canvas + optional Fluent scrollbar. Scrollbar is shown only when content overflows."""

    def __init__(self, master: tk.Misc, bg: str = C_CARD, **kwargs: Any) -> None:
        super().__init__(master, bg=bg, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = FluentScrollbar(self, command=self._yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._overflow = False
        self._syncing = False
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        bind_mousewheel(self.canvas, self)
        bind_mousewheel(self.inner, self)
        bind_mousewheel(self.sb, self)

    def can_scroll(self) -> bool:
        return self._overflow

    def bind_wheel(self, widget: tk.Widget) -> None:
        bind_mousewheel(widget, self)

    def reset_top(self) -> None:
        self.canvas.yview_moveto(0)
        self._sync()

    def _yview(self, *args: Any) -> None:
        if not self._overflow:
            self.canvas.yview_moveto(0)
            return
        self.canvas.yview(*args)

    def _on_scroll(self, *args: Any) -> None:
        if self._overflow:
            self.sb.set(*args)
        else:
            self.sb.set(0, 1)
            try:
                if float(args[0]) != 0.0:
                    self.canvas.yview_moveto(0)
            except (TypeError, ValueError, IndexError):
                self.canvas.yview_moveto(0)

    def _on_inner(self, _event: tk.Event | None = None) -> None:
        self._sync()

    def _on_canvas(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._win, width=max(1, int(event.width)))
        self._sync()

    def _sync(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            try:
                self.inner.update_idletasks()
            except tk.TclError:
                return
            inner_h = max(0, int(self.inner.winfo_reqheight()))
            canvas_h = max(0, int(self.canvas.winfo_height()))
            canvas_w = max(0, int(self.canvas.winfo_width()))
            self.canvas.itemconfigure(self._win, width=max(1, canvas_w))
            self.canvas.configure(scrollregion=(0, 0, max(1, canvas_w), inner_h))
            overflow = inner_h > canvas_h + 1 and canvas_h > 1
            if overflow != self._overflow:
                self._overflow = overflow
                if overflow:
                    if not self.sb.winfo_ismapped():
                        self.sb.pack(side="right", fill="y")
                else:
                    self.canvas.yview_moveto(0)
                    if self.sb.winfo_ismapped():
                        self.sb.pack_forget()
            elif not overflow:
                self.canvas.yview_moveto(0)
                if self.sb.winfo_ismapped():
                    self.sb.pack_forget()
        finally:
            self._syncing = False


AVATAR_SIZE = 32
CHAT_ROW_H = 56


_PLACEHOLDER_AVATARS: dict[tuple[str, int], ImageTk.PhotoImage] = {}


def _avatar_mark_kind(kind: str) -> str:
    if kind == "频道":
        return "channel"
    if kind == "群组":
        return "group"
    if kind == "机器人":
        return "bot"
    return "person"


def make_placeholder_avatar(
    kind: str = "私聊",
    logical: int = AVATAR_SIZE,
    widget: Optional[tk.Misc] = None,
) -> ImageTk.PhotoImage:
    """Neutral gray circular silhouette. No letters, no colored blocks."""
    px = scaled_px(logical, widget)
    mark = _avatar_mark_kind(kind)
    key = (mark, px)
    cached = _PLACEHOLDER_AVATARS.get(key)
    if cached is not None:
        return cached
    over = 4
    s = max(px * over, 64)
    im = Image.new("RGB", (s, s), (208, 208, 208))
    pix = im.load()
    cx = cy = s / 2
    for y in range(s):
        for x in range(s):
            t = min(1.0, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / (s * 0.72))
            a = t * 0.08
            pix[x, y] = (
                int(208 + (180 - 208) * a),
                int(208 + (180 - 208) * a),
                int(208 + (180 - 208) * a),
            )
    d = ImageDraw.Draw(im)
    ink = (138, 138, 138)
    if mark == "person":
        d.ellipse((int(s * 0.32), int(s * 0.16), int(s * 0.68), int(s * 0.52)), fill=ink)
        d.pieslice((int(s * 0.10), int(s * 0.58), int(s * 0.90), int(s * 1.42)), 180, 0, fill=ink)
    elif mark == "group":
        # two quiet busts, slightly overlapping
        d.ellipse((int(s * 0.18), int(s * 0.20), int(s * 0.48), int(s * 0.50)), fill=ink)
        d.pieslice((int(s * 0.00), int(s * 0.58), int(s * 0.62), int(s * 1.40)), 180, 0, fill=ink)
        d.ellipse((int(s * 0.50), int(s * 0.16), int(s * 0.84), int(s * 0.50)), fill=ink)
        d.pieslice((int(s * 0.36), int(s * 0.56), int(s * 1.00), int(s * 1.40)), 180, 0, fill=ink)
    elif mark == "channel":
        # simple megaphone / broadcast mark
        d.polygon(
            [
                (int(s * 0.22), int(s * 0.40)),
                (int(s * 0.50), int(s * 0.40)),
                (int(s * 0.78), int(s * 0.22)),
                (int(s * 0.78), int(s * 0.78)),
                (int(s * 0.50), int(s * 0.60)),
                (int(s * 0.22), int(s * 0.60)),
            ],
            fill=ink,
        )
        d.rectangle((int(s * 0.18), int(s * 0.44), int(s * 0.28), int(s * 0.72)), fill=ink)
    else:
        # quiet robot head
        d.rounded_rectangle(
            (int(s * 0.26), int(s * 0.28), int(s * 0.74), int(s * 0.76)),
            radius=int(s * 0.10),
            fill=ink,
        )
        d.rectangle((int(s * 0.48), int(s * 0.14), int(s * 0.52), int(s * 0.28)), fill=ink)
        d.ellipse((int(s * 0.46), int(s * 0.08), int(s * 0.54), int(s * 0.16)), fill=ink)
        eye = (208, 208, 208)
        d.ellipse((int(s * 0.36), int(s * 0.42), int(s * 0.46), int(s * 0.52)), fill=eye)
        d.ellipse((int(s * 0.54), int(s * 0.42), int(s * 0.64), int(s * 0.52)), fill=eye)

    im = im.resize((px, px), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, px - 1, px - 1), fill=255)
    im.putalpha(mask)
    photo = ImageTk.PhotoImage(im)
    _PLACEHOLDER_AVATARS[key] = photo
    return photo


def make_avatar_photo(data: bytes, logical: int = AVATAR_SIZE, widget: Optional[tk.Misc] = None) -> Optional[ImageTk.PhotoImage]:
    """Circular crop of a Telegram profile photo, sized for the current display scale."""
    try:
        px = scaled_px(logical, widget)
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        src_w, src_h = im.size
        if src_w <= 0 or src_h <= 0:
            return None
        side = min(src_w, src_h)
        left = (src_w - side) // 2
        top = (src_h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        im = im.resize((px, px), Image.Resampling.LANCZOS)
        mask = Image.new("L", (px, px), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, px - 1, px - 1), fill=255)
        im.putalpha(mask)
        return ImageTk.PhotoImage(im)
    except Exception:
        return None


class ChatRow(tk.Frame):
    def __init__(self, master: tk.Misc, item: DialogItem, on_select: Callable[[DialogItem], None]) -> None:
        av = scaled_px(AVATAR_SIZE, master)
        row_h = max(scaled_px(CHAT_ROW_H, master), av + 16)
        super().__init__(master, bg=C_CARD, height=row_h, cursor="hand2")
        self.pack_propagate(False)
        self.item = item
        self._on_select = on_select
        self.selected = False
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._av_size = av
        self._token = 0

        self.av = tk.Canvas(self, width=av, height=av, highlightthickness=0, bd=0, bg=C_CARD)
        self.av.pack(side="left", padx=(12, 10))
        self._draw_placeholder()

        self.lbl_title = tk.Label(self, text=item.title, bg=C_CARD, fg=C_TEXT, font=Fonts.body, anchor="w")
        self.lbl_title.pack(side="left", fill="x", expand=True)

        self.badge = tk.Label(
            self,
            text=item.kind,
            bg=C_BADGE,
            fg=C_TEXT2,
            font=Fonts.badge,
            padx=6,
            pady=1,
        )
        self.badge.pack(side="right", padx=(0, 12))

        for w in (self, self.av, self.lbl_title, self.badge):
            w.bind("<Button-1>", self._click)
        self.bind("<Configure>", self._rule)

    def _draw_placeholder(self) -> None:
        bg = C_SEL if self.selected else C_CARD
        self.av.configure(bg=bg)
        self._photo = make_placeholder_avatar(self.item.kind, AVATAR_SIZE, self)
        self.av.delete("all")
        self.av.create_image(0, 0, anchor="nw", image=self._photo)

    def _paint_avatar(self) -> None:
        s = self._av_size
        bg = C_SEL if self.selected else C_CARD
        self.av.delete("all")
        self.av.configure(bg=bg)
        if self._photo is not None:
            self.av.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self._draw_placeholder()

    def set_avatar(self, data: Optional[bytes], token: Optional[int] = None) -> None:
        if token is not None:
            self._token = token
        if not data:
            self._draw_placeholder()
            return
        photo = make_avatar_photo(data, AVATAR_SIZE, self)
        if photo is None:
            self._draw_placeholder()
            return
        self._photo = photo
        self._paint_avatar()

    def _rule(self, _event: tk.Event | None = None) -> None:
        pass

    def _click(self, _event: tk.Event | None = None) -> None:
        self._on_select(self.item)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        bg = C_SEL if selected else C_CARD
        fg_badge = C_ACCENT if selected else C_TEXT2
        badge_bg = C_BADGE_SEL if selected else C_BADGE
        self.configure(bg=bg)
        self.av.configure(bg=bg)
        self.lbl_title.configure(bg=bg)
        self.badge.configure(bg=badge_bg, fg=fg_badge)
        if self._photo is not None:
            self._paint_avatar()
        else:
            self._draw_placeholder()


class ChatList(tk.Frame):
    def __init__(self, master: tk.Misc, on_select: Callable[[DialogItem], None]) -> None:
        super().__init__(
            master,
            bg=C_CARD,
            highlightthickness=1,
            highlightbackground=C_STROKE,
            highlightcolor=C_STROKE,
        )
        self._on_select = on_select
        self._rows: list[ChatRow] = []
        self._selected: Optional[DialogItem] = None
        self.scroll = ScrollArea(self, bg=C_CARD)
        self.scroll.pack(fill="both", expand=True)
        self._dividers: list[tk.Frame] = []

    def set_items(self, items: list[DialogItem], selected: Optional[DialogItem] = None) -> None:
        for row in self._rows:
            row.destroy()
        self._rows.clear()
        self._selected = selected
        if items and selected is not None:
            if not any(d.id == selected.id for d in items):
                self._selected = None
        for i, item in enumerate(items):
            row = ChatRow(self.scroll.inner, item, self._choose)
            row.pack(fill="x")
            if i < len(items) - 1:
                tk.Frame(row, bg=C_STROKE, height=1).place(relx=0, rely=1, relwidth=1, y=-1)
            if self._selected and item.id == self._selected.id:
                row.set_selected(True)
                self._selected = item
            self._rows.append(row)
            self.scroll.bind_wheel(row)
        self.scroll.reset_top()

    def apply_avatars(self, photos: dict[int, bytes], token: Optional[int] = None) -> None:
        for row in self._rows:
            data = photos.get(row.item.id)
            if data:
                row.set_avatar(data, token=token)

    def set_avatar(self, dialog_id: int, data: bytes, token: Optional[int] = None) -> None:
        for row in self._rows:
            if row.item.id == dialog_id:
                row.set_avatar(data, token=token)
                return

    def _choose(self, item: DialogItem) -> None:
        self._selected = item
        for row in self._rows:
            row.set_selected(row.item.id == item.id)
        self._on_select(item)

    @property
    def selected(self) -> Optional[DialogItem]:
        return self._selected


THUMB_W = 128
THUMB_H = 72
THUMB_RADIUS = 4
VIDEO_ROW_H = 88
CHK_SIZE = 16
CHK_RADIUS = 3
CHK_BORDER = "#8A8A8A"

_PLACEHOLDER_COLORS = (
    ((0x1F, 0x2C, 0x38), (0x3A, 0x53, 0x68), "radial"),
    ((0x24, 0x34, 0x42), (0x2E, 0x4A, 0x5C), "slash"),
    ((0x33, 0x41, 0x55), (0x1E, 0x2A, 0x36), "circle"),
    ((0x2A, 0x38, 0x46), (0x3D, 0x55, 0x66), "circle2"),
    ((0x1C, 0x2B, 0x38), (0x45, 0x5A, 0x6B), "slash2"),
)


def elide_text(font: Any, text: str, width: int) -> str:
    if not text or width <= 0:
        return text
    if not hasattr(font, "measure") or font.measure(text) <= width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if font.measure(text[:mid] + ell) <= width:
            lo = mid + 1
        else:
            hi = mid
    cut = max(0, lo - 1)
    return (text[:cut] + ell) if cut else ell


def _rounded_mask(width: int, height: int, radius: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    return mask


def make_cover_photo(
    data: bytes,
    width: int = THUMB_W,
    height: int = THUMB_H,
    radius: int = THUMB_RADIUS,
    widget: Optional[tk.Misc] = None,
) -> Optional[ImageTk.PhotoImage]:
    """把封面裁成 16:9 圆角图。按显示缩放用 LANCZOS 缩放，避免把小图拉糊。"""
    try:
        pw, ph = scaled_px(width, widget), scaled_px(height, widget)
        pr = max(1, scaled_px(radius, widget))
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGBA")
        src_w, src_h = im.size
        if src_w <= 0 or src_h <= 0:
            return None
        scale = max(pw / src_w, ph / src_h)
        nw = max(1, int(round(src_w * scale)))
        nh = max(1, int(round(src_h * scale)))
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
        left = max(0, (nw - pw) // 2)
        top = max(0, (nh - ph) // 2)
        im = im.crop((left, top, left + pw, top + ph))
        out = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        out.paste(im, (0, 0))
        out.putalpha(_rounded_mask(pw, ph, pr))
        return ImageTk.PhotoImage(out)
    except Exception:
        return None


def make_placeholder_photo(
    variant: int = 0,
    width: int = THUMB_W,
    height: int = THUMB_H,
    radius: int = THUMB_RADIUS,
    widget: Optional[tk.Misc] = None,
) -> ImageTk.PhotoImage:
    """冷色几何占位封面，带半透明播放三角，不用纸飞机。"""
    width = scaled_px(width, widget)
    height = scaled_px(height, widget)
    radius = max(1, scaled_px(radius, widget))
    c1, c2, motif = _PLACEHOLDER_COLORS[variant % len(_PLACEHOLDER_COLORS)]
    base = Image.new("RGB", (width, height))
    pix = base.load()
    denom = max(1.0, width * 0.7 + height)
    for y in range(height):
        for x in range(width):
            t = max(0.0, min(1.0, (x * 0.7 + y) / denom))
            pix[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if motif == "radial":
        od.ellipse((-16, height // 2, width // 2 + 4, height + 28), fill=(130, 170, 200, 80))
    elif motif == "slash":
        od.polygon(
            [(0, height), (int(width * 0.42), 0), (int(width * 0.58), 0), (0, height)],
            fill=(255, 255, 255, 16),
        )
    elif motif == "circle":
        od.ellipse((-12, -14, 34, 32), fill=(90, 130, 160, 82))
    elif motif == "circle2":
        od.ellipse((width - 42, height - 32, width + 22, height + 32), fill=(70, 110, 140, 88))
    else:
        od.ellipse((width - 40, -8, width - 8, 26), fill=(160, 190, 210, 46))
        od.polygon(
            [(0, 0), (int(width * 0.28), 0), (0, int(height * 0.7))],
            fill=(255, 255, 255, 12),
        )
    img = Image.alpha_composite(base.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)
    cx, cy = width / 2, height / 2
    # 约 32% 不透明的播放三角
    draw.polygon(
        [(cx - 3, cy - 6), (cx - 3, cy + 6), (cx + 7, cy)],
        fill=(255, 255, 255, 82),
    )
    img.putalpha(_rounded_mask(width, height, radius))
    return ImageTk.PhotoImage(img)


class CheckMark(tk.Canvas):
    """16px、圆角 3px 的 Fluent 复选框，支持选中 / 中间态。"""

    def __init__(
        self,
        master: tk.Misc,
        command: Optional[Callable[[], None]] = None,
        size: int = CHK_SIZE,
        bg: str = C_CARD,
    ) -> None:
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0, bg=bg, cursor="hand2")
        self._command = command
        self._size = size
        self._bg = bg
        self._enabled = True
        self.state = "off"  # off / on / mid
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def set_checked(self, checked: bool) -> None:
        self.set_state("on" if checked else "off")

    def set_state(self, state: str) -> None:
        self.state = state if state in ("on", "off", "mid") else "off"
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "")
        self._redraw()

    def set_bg(self, bg: str) -> None:
        self._bg = bg
        self.configure(bg=bg)
        self._redraw()

    def _on_click(self, _event: tk.Event | None = None) -> str | None:
        if self._enabled and self._command:
            self._command()
        return "break"

    def _redraw(self) -> None:
        self.delete("all")
        s = self._size
        if self.state in ("on", "mid"):
            fill, outline = C_ACCENT, C_ACCENT
        else:
            fill, outline = "#FFFFFF", CHK_BORDER
        if not self._enabled and self.state == "off":
            outline = "#C8C8C8"
        rounded_rect(self, 0.5, 0.5, s - 0.5, s - 0.5, CHK_RADIUS, fill=fill, outline=outline)
        if self.state == "on":
            self.create_line(4, 8, 6.5, 11.5, fill="#FFFFFF", width=1.6, capstyle="round")
            self.create_line(6.5, 11.5, 12, 4.5, fill="#FFFFFF", width=1.6, capstyle="round")
        elif self.state == "mid":
            rounded_rect(self, 4, 7, 12, 9, 1, fill="#FFFFFF", outline="#FFFFFF")


class VideoRow(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        item: VideoItem,
        on_toggle: Callable[[VideoItem], None],
    ) -> None:
        self._tw, self._th = scaled_px(THUMB_W, master), scaled_px(THUMB_H, master)
        row_h = max(VIDEO_ROW_H, self._th + 12)
        super().__init__(master, bg=C_CARD, height=row_h, cursor="hand2")
        self.pack_propagate(False)
        self.item = item
        self._on_toggle = on_toggle
        self.selected = False
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._badge: Optional[ImageTk.PhotoImage] = None
        self._title_full = video_display_title(item)

        self._chk_box = tk.Frame(self, bg=C_CARD, width=CHK_SIZE)
        self._chk_box.pack(side="left", fill="y", padx=(12, 0))
        self._chk_box.pack_propagate(False)
        self.chk = CheckMark(self._chk_box, command=self._toggle, bg=C_CARD)
        self.chk.place(relx=0.5, rely=0.5, anchor="center")

        self.cover = tk.Canvas(
            self,
            width=self._tw,
            height=self._th,
            highlightthickness=0,
            bd=0,
            bg=C_CARD,
        )
        self.cover.pack(side="left", padx=(10, 0), pady=6)

        texts = tk.Frame(self, bg=C_CARD)
        texts.pack(side="left", fill="both", expand=True, padx=(10, 12))
        mid = tk.Frame(texts, bg=C_CARD)
        mid.place(relx=0, rely=0.5, relwidth=1, anchor="w")
        self.lbl_title = tk.Label(
            mid,
            text=self._title_full,
            bg=C_CARD,
            fg=C_TEXT,
            font=Fonts.body,
            anchor="w",
            justify="left",
        )
        self.lbl_title.pack(fill="x")
        self.lbl_meta = tk.Label(
            mid, text="", bg=C_CARD, fg=C_TEXT3, font=Fonts.aux, anchor="w", justify="left", wraplength=420
        )
        self.lbl_meta.pack(fill="x", pady=(4, 0))
        self._texts = texts
        self._mid = mid
        self._quality_pref = QUALITY_HIGHEST
        self.refresh_meta()

        for w in (self, self._chk_box, self.cover, texts, mid, self.lbl_title, self.lbl_meta):
            w.bind("<Button-1>", self._click)
        mid.bind("<Configure>", self._on_texts)
        if item.thumb_bytes:
            self.set_thumb(item.thumb_bytes)
        else:
            self._photo = make_placeholder_photo(int(item.message_id or 0), widget=self)
            self._paint_cover()

    def _on_texts(self, event: tk.Event) -> None:
        self.lbl_title.configure(text=elide_text(Fonts.body, self._title_full, max(1, int(event.width))))

    def _meta_text(self, pref: Optional[str] = None) -> str:
        chosen = pick_video_quality(self.item.qualities or [], pref or self._quality_pref)
        size = chosen.size if chosen else self.item.size
        parts = [format_bytes(size), format_date_short(self.item.date)]
        if self.item.note:
            parts.append(self.item.note)
        elif not self.item.downloadable:
            parts.append("无法下载")
        return " · ".join(p for p in parts if p)

    def refresh_meta(self, pref: Optional[str] = None) -> None:
        if pref is not None:
            self._quality_pref = pref
        self.lbl_meta.configure(text=self._meta_text())

    def _paint_cover(self) -> None:
        bg = C_SEL if self.selected else C_CARD
        self.cover.delete("all")
        self.cover.configure(bg=bg)
        self.cover.create_rectangle(0, 0, self._tw, self._th, fill=bg, outline=bg)
        if self._photo is not None:
            self.cover.create_image(0, 0, anchor="nw", image=self._photo)
        self._draw_duration_badge()

    def _draw_duration_badge(self) -> None:
        text = format_duration_short(self.item.duration)
        if not text or text == "—":
            self._badge = None
            return
        font = Fonts.badge
        tw = font.measure(text) if hasattr(font, "measure") else len(text) * 6
        try:
            th = int(font.metrics("ascent"))
        except Exception:
            th = 10
        pad_x, pad_y = 4, 2
        bw = max(1, tw + pad_x * 2)
        bh = max(1, th + pad_y * 2)
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=2, fill=(0, 0, 0, 158))
        self._badge = ImageTk.PhotoImage(img)
        x1 = self._tw - 4 - bw
        y1 = self._th - 4 - bh
        self.cover.create_image(x1, y1, anchor="nw", image=self._badge)
        self.cover.create_text(x1 + bw / 2, y1 + bh / 2, text=text, fill="#FFFFFF", font=font)

    def set_thumb(self, data: Optional[bytes]) -> None:
        photo = make_cover_photo(data, widget=self) if data else None
        if photo is None:
            self._photo = make_placeholder_photo(int(self.item.message_id or 0), widget=self)
        else:
            self._photo = photo
        self._paint_cover()

    def _click(self, _event: tk.Event | None = None) -> None:
        self._toggle()

    def _toggle(self) -> None:
        self._on_toggle(self.item)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        bg = C_SEL if selected else C_CARD
        self.configure(bg=bg)
        self._chk_box.configure(bg=bg)
        self.cover.configure(bg=bg)
        self._texts.configure(bg=bg)
        self._mid.configure(bg=bg)
        self.lbl_title.configure(bg=bg)
        self.lbl_meta.configure(bg=bg)
        self.chk.set_bg(bg)
        self.chk.set_checked(selected)
        self._paint_cover()


class VideoList(tk.Frame):
    def __init__(self, master: tk.Misc, on_toggle: Callable[[VideoItem], None]) -> None:
        super().__init__(master, bg=C_CARD)
        self._on_toggle = on_toggle
        self._rows: dict[int, VideoRow] = {}
        self._order: list[int] = []
        self._selected: set[int] = set()
        self.scroll = ScrollArea(self, bg=C_CARD)
        self.scroll.pack(fill="both", expand=True)

    def clear(self) -> None:
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()
        self._order.clear()
        self._selected.clear()
        self.scroll.reset_top()

    def set_items(self, items: list[VideoItem], selected: Optional[set[int]] = None, reset_scroll: bool = True) -> None:
        self.clear()
        if selected:
            self._selected = set(selected)
        for i, item in enumerate(items):
            self._append_row(item, divider=i > 0)
        if reset_scroll:
            self.scroll.reset_top()

    def append_items(self, items: list[VideoItem]) -> None:
        start = len(self._order)
        for i, item in enumerate(items):
            if item.message_id in self._rows:
                continue
            self._append_row(item, divider=start + i > 0)

    def _append_row(self, item: VideoItem, divider: bool = False) -> None:
        row = VideoRow(self.scroll.inner, item, self._toggle)
        row.pack(fill="x")
        if divider:
            tk.Frame(row, bg=C_STROKE, height=1).place(relx=0, rely=0, relwidth=1, y=0)
        if item.message_id in self._selected:
            row.set_selected(True)
        self._rows[item.message_id] = row
        self._order.append(item.message_id)
        self.scroll.bind_wheel(row)

    def _toggle(self, item: VideoItem) -> None:
        mid = item.message_id
        if mid in self._selected:
            self._selected.discard(mid)
        else:
            self._selected.add(mid)
        row = self._rows.get(mid)
        if row:
            row.set_selected(mid in self._selected)
        self._on_toggle(item)

    def set_all_selected(self, selected: bool) -> None:
        self._selected = set(self._order) if selected else set()
        for mid, row in self._rows.items():
            row.set_selected(mid in self._selected)

    def update_thumb(self, message_id: int, data: bytes) -> None:
        row = self._rows.get(message_id)
        if not row:
            return
        row.item.thumb_bytes = data
        row.set_thumb(data)
        if row.selected:
            row.set_selected(True)

    @property
    def selected_ids(self) -> set[int]:
        return set(self._selected)

    def all_selected(self) -> bool:
        return bool(self._order) and len(self._selected) == len(self._order)

    def refresh_quality(self, pref: str) -> None:
        for row in self._rows.values():
            row.refresh_meta(pref)


class TaskRow(tk.Frame):
    def __init__(self, master: tk.Misc, on_remove: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master, bg=C_CARD)
        self.key = ""
        self._on_remove = on_remove
        top = tk.Frame(self, bg=C_CARD)
        top.pack(fill="x", padx=12, pady=(10, 0))
        self.lbl_name = tk.Label(top, text="", bg=C_CARD, fg=C_TEXT, font=Fonts.body, anchor="w")
        self.lbl_name.pack(side="left", fill="x", expand=True)
        self.btn_remove = tk.Label(top, text="删除", bg=C_CARD, fg=C_ACCENT, font=Fonts.aux, cursor="hand2")
        self.btn_remove.pack(side="right")
        self.btn_remove.bind("<Button-1>", self._remove_clicked)
        self.lbl_stat = tk.Label(top, text="", bg=C_CARD, fg=C_TEXT2, font=Fonts.aux, anchor="e")
        self.lbl_stat.pack(side="right", padx=(0, 10))

        mid = tk.Frame(self, bg=C_CARD)
        mid.pack(fill="x", padx=12, pady=(2, 0))
        self.lbl_sub = tk.Label(
            mid, text="", bg=C_CARD, fg=C_TEXT3, font=Fonts.aux, anchor="w", justify="left", wraplength=420
        )
        self.lbl_sub.pack(side="left", fill="x", expand=True)
        self.lbl_pct = tk.Label(mid, text="", bg=C_CARD, fg=C_TEXT2, font=Fonts.aux, anchor="e")
        self.lbl_pct.pack(side="right")

        self.bar = tk.Canvas(self, height=4, highlightthickness=0, bd=0, bg=C_CARD)
        self.bar.pack(fill="x", padx=12, pady=(6, 11))
        self._bar_pct = 0
        self._bar_color = C_ACCENT
        self.bar.bind("<Configure>", lambda _e: self._draw_bar(self._bar_pct, self._bar_color))
        tk.Frame(self, bg=C_STROKE, height=1).pack(fill="x")

    def update_row(
        self,
        name: str,
        status: str,
        current: int = 0,
        total: int = 0,
        speed: float = 0.0,
        error: str = "",
    ) -> None:
        self.lbl_name.configure(text=name, fg=C_TEXT2 if status == "done" else C_TEXT)
        if status == "done":
            self.lbl_stat.configure(text="已完成", fg=C_OK)
            self.lbl_sub.configure(text=format_bytes(total or current) if (total or current) else "", fg=C_TEXT3)
            self.lbl_pct.configure(text="100%", fg=C_TEXT2)
            pct = 100
            bar_color = C_OK
        elif status == "error":
            self.lbl_stat.configure(text="失败", fg=C_ERR)
            self.lbl_sub.configure(text=error or "", fg=C_ERR)
            self.lbl_pct.configure(text="")
            pct = int(current * 100 / total) if total else 0
            bar_color = C_ERR
        elif status == "stopped":
            self.lbl_stat.configure(text="已停止", fg=C_TEXT2)
            self.lbl_sub.configure(text="", fg=C_TEXT3)
            self.lbl_pct.configure(text="")
            pct = int(current * 100 / total) if total else 0
            bar_color = C_TEXT3
        elif status == "queued":
            size = f"{format_bytes(total)}" if total else ""
            self.lbl_stat.configure(text=size, fg=C_TEXT2)
            self.lbl_sub.configure(text="排队中", fg=C_TEXT3)
            self.lbl_pct.configure(text="0%" if not current else f"{int(current * 100 / total) if total else 0}%")
            pct = int(current * 100 / total) if total else 0
            bar_color = C_ACCENT
        else:
            if total:
                self.lbl_stat.configure(text=f"{format_bytes(current)} / {format_bytes(total)}", fg=C_TEXT2)
                pct = int(current * 100 / total) if total else 0
            else:
                self.lbl_stat.configure(text=format_bytes(current) if current else "", fg=C_TEXT2)
                pct = 0
            self.lbl_sub.configure(text=format_speed(speed) if speed else "下载中", fg=C_TEXT3)
            self.lbl_pct.configure(text=f"{pct}%")
            bar_color = C_ACCENT
        self._draw_bar(pct, bar_color)

    def _draw_bar(self, pct: int, color: str) -> None:
        self._bar_pct = pct
        self._bar_color = color
        self.bar.delete("all")
        w = max(int(self.bar.winfo_width()), 1)
        h = 4
        self.bar.create_rectangle(0, 0, w, h, fill=C_BAR, outline=C_BAR)
        fw = max(0, int(w * max(0, min(pct, 100)) / 100))
        if fw:
            self.bar.create_rectangle(0, 0, fw, h, fill=color, outline=color)

    def _remove_clicked(self, _event: tk.Event | None = None) -> str | None:
        if self._on_remove and self.key:
            self._on_remove(self.key)
        return "break"


class TaskList(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_stop: Callable[[], None],
        on_open_folder: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        on_remove: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(
            master,
            bg=C_CARD,
            highlightthickness=1,
            highlightbackground=C_STROKE,
            highlightcolor=C_STROKE,
        )
        self._on_stop = on_stop
        self._on_open_folder = on_open_folder
        self._on_close = on_close
        self._on_remove = on_remove
        self._items: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._rows: dict[str, TaskRow] = {}
        self._resizable = False
        self._closable = False
        self._stop_wanted = False
        self._open_wanted = False
        self._drag_y = 0
        self.pane_height = 200

        self.sash = tk.Frame(self, bg=C_CARD, height=8, cursor="sb_v_double_arrow")
        self.sash.pack_propagate(False)
        self._sash_bar = tk.Frame(self.sash, bg="#C8C8C8", height=3, width=36)
        self._sash_bar.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self.sash, self._sash_bar):
            w.configure(cursor="sb_v_double_arrow")
            w.bind("<Button-1>", self._sash_down)
            w.bind("<B1-Motion>", self._sash_move)

        hd = tk.Frame(self, bg=C_CARD, height=36)
        self._header = hd
        hd.pack(fill="x")
        hd.pack_propagate(False)
        self.lbl_title = tk.Label(hd, text="就绪", bg=C_CARD, fg=C_TEXT, font=Fonts.help, anchor="w")
        self.lbl_title.pack(side="left", padx=12)
        self.lbl_meta = tk.Label(hd, text="", bg=C_CARD, fg=C_TEXT2, font=Fonts.help, anchor="w")
        self.lbl_meta.pack(side="left", padx=(2, 0))
        self.btn_close = FlatButton(hd, "关闭", command=self._close, height=26, padx=10, font=Fonts.help, bg=C_CARD)
        self.btn_stop = FlatButton(hd, "停止", command=on_stop, height=26, padx=10, font=Fonts.help, bg=C_CARD)
        self.btn_open_folder = FlatButton(
            hd, "打开文件夹", command=on_open_folder, height=26, padx=10, font=Fonts.help, bg=C_CARD
        )
        # packed when running / finished
        tk.Frame(self, bg=C_STROKE, height=1).pack(fill="x")

        self.body = tk.Frame(self, bg=C_CARD)
        self.body.pack(fill="both", expand=True)
        self.lbl_empty = tk.Label(self.body, text="还没有任务", bg=C_CARD, fg=C_TEXT3, font=Fonts.body)
        self.lbl_empty.place(relx=0.5, rely=0.5, anchor="center")
        self.scroll = ScrollArea(self.body, bg=C_CARD)

    def clear(self) -> None:
        self._items.clear()
        self._order.clear()
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()
        self._show_empty(True)
        self.set_header("就绪")
        self._show_stop(False)
        self._show_open(False)

    def set_header(self, title: str, meta: str = "", show_stop: bool = False, show_open: bool = False) -> None:
        self.lbl_title.configure(text=title)
        self.lbl_meta.configure(text=meta)
        self._show_stop(show_stop)
        self._show_open(show_open)

    def _show_stop(self, show: bool) -> None:
        self._stop_wanted = bool(show)
        self._pack_header_buttons()

    def _show_open(self, show: bool) -> None:
        self._open_wanted = bool(show and self._on_open_folder)
        self._pack_header_buttons()

    def _show_close(self, show: bool) -> None:
        self._closable = bool(show and self._on_close)
        self._pack_header_buttons()

    def _pack_header_buttons(self) -> None:
        for btn in (self.btn_close, self.btn_open_folder, self.btn_stop):
            if btn.winfo_ismapped():
                btn.pack_forget()
        # pack right-to-left: close is rightmost
        if self._closable:
            self.btn_close.pack(side="right", padx=(0, 12))
        if getattr(self, "_open_wanted", False):
            self.btn_open_folder.pack(side="right", padx=(0, 8))
        if getattr(self, "_stop_wanted", False):
            self.btn_stop.pack(side="right", padx=(0, 8))

    def _close(self) -> None:
        if self._on_close:
            self._on_close()

    def set_pane_mode(self, *, compact: bool, height: int = 200, closable: bool = False) -> None:
        self._resizable = compact
        self.pane_height = height
        if compact:
            if not self.sash.winfo_ismapped():
                self.sash.pack(fill="x", before=self._header)
            self.configure(height=height)
            self.grid_propagate(False)
        else:
            if self.sash.winfo_ismapped():
                self.sash.pack_forget()
            self.grid_propagate(True)
        self._show_close(closable)

    def _sash_down(self, event: tk.Event) -> None:
        self._drag_y = int(event.y_root)

    def _sash_move(self, event: tk.Event) -> None:
        if not self._resizable:
            return
        dy = int(event.y_root) - self._drag_y
        self._drag_y = int(event.y_root)
        try:
            max_h = max(140, int(self.master.winfo_height()) - 240)
        except tk.TclError:
            max_h = 520
        new_h = max(110, min(max_h, int(self.winfo_height()) - dy))
        self.pane_height = new_h
        self.configure(height=new_h)

    def _show_empty(self, empty: bool) -> None:
        if empty:
            self.scroll.pack_forget()
            self.lbl_empty.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.lbl_empty.place_forget()
            if not self.scroll.winfo_ismapped():
                self.scroll.pack(fill="both", expand=True)

    def upsert(
        self,
        key: str,
        name: str,
        status: str,
        current: int = 0,
        total: int = 0,
        speed: float = 0.0,
        error: str = "",
        index: Optional[int] = None,
    ) -> None:
        self._items[key] = {
            "name": name,
            "status": status,
            "current": current,
            "total": total,
            "speed": speed,
            "error": error,
        }
        if key not in self._order:
            if index is None or index >= len(self._order):
                self._order.append(key)
            else:
                self._order.insert(index, key)
        self._show_empty(False)
        row = self._rows.get(key)
        if row is None:
            row = TaskRow(self.scroll.inner, on_remove=self._on_remove)
            row.key = key
            self._rows[key] = row
            self._repack()
        row.update_row(name, status, current, total, speed, error)

    def remove(self, key: str) -> None:
        if key in self._order:
            self._order.remove(key)
        self._items.pop(key, None)
        row = self._rows.pop(key, None)
        if row:
            row.destroy()
        if not self._order:
            self._show_empty(True)

    def replace_key(self, old: str, new_items: list[tuple[str, str, int]]) -> None:
        """用一组新任务替换占位项（例如一条链接解析出多个视频）。"""
        idx = self._order.index(old) if old in self._order else len(self._order)
        self.remove(old)
        for i, (key, name, total) in enumerate(new_items):
            self.upsert(key, name, "queued", total=total, index=idx + i)

    def keys(self) -> list[str]:
        return list(self._order)

    def get(self, key: str) -> Optional[dict[str, Any]]:
        return self._items.get(key)

    def counts(self) -> tuple[int, int, int]:
        done = sum(1 for i in self._items.values() if i["status"] in ("done", "error", "stopped"))
        running = sum(1 for i in self._items.values() if i["status"] == "running")
        total = len(self._order)
        return done, running, total

    def current_speed(self) -> float:
        for item in self._items.values():
            if item["status"] == "running":
                return float(item.get("speed") or 0)
        return 0.0

    def _repack(self) -> None:
        for row in self._rows.values():
            row.pack_forget()
        for key in self._order:
            row = self._rows.get(key)
            if row:
                row.pack(fill="x")
                self.scroll.bind_wheel(row)
        self.scroll._sync()

    def has_items(self) -> bool:
        return bool(self._order)



CHAT_FILTERS = ("全部", "频道", "群组", "私聊", "机器人")


class FilterChipBar(tk.Frame):
    """28px text tabs: selected = accent + bold + light pill; others are plain text."""

    def __init__(
        self,
        master: tk.Misc,
        command: Optional[Callable[[str], None]] = None,
        bg: str = C_BG,
    ) -> None:
        super().__init__(master, bg=bg, height=28)
        self.grid_propagate(False)
        self._command = command
        self.value = "全部"
        self._bg = bg
        self._chips: dict[str, tk.Canvas] = {}
        for i, label in enumerate(CHAT_FILTERS):
            self.columnconfigure(i, weight=1, uniform="filt")
            self.rowconfigure(0, weight=1)
            chip = tk.Canvas(self, height=22, highlightthickness=0, bd=0, bg=bg, cursor="hand2")
            chip.grid(row=0, column=i, padx=0, pady=3, sticky="nsew")
            chip.bind("<Button-1>", lambda _e, v=label: self._pick(v))
            chip.bind("<Configure>", lambda _e, v=label: self._draw_one(v))
            self._chips[label] = chip
        self._redraw()

    def set_value(self, value: str) -> None:
        if value not in CHAT_FILTERS:
            value = "全部"
        self.value = value
        self._redraw()

    def _pick(self, value: str) -> None:
        if value == self.value:
            return
        self.set_value(value)
        if self._command:
            self._command(value)

    def _redraw(self) -> None:
        for label in self._chips:
            self._draw_one(label)

    def _draw_one(self, label: str) -> None:
        chip = self._chips.get(label)
        if chip is None:
            return
        active = label == self.value
        width = max(8, int(chip.winfo_width()))
        height = max(22, int(chip.winfo_height()) if chip.winfo_height() > 1 else 22)
        chip.delete("all")
        chip.configure(bg=self._bg)
        if active:
            rounded_rect(chip, 0.5, 0.5, width - 0.5, height - 0.5, height / 2, fill=C_SEL, outline=C_SEL)
            fg, font = C_ACCENT, Fonts.aux_bold or Fonts.nav_active
        else:
            fg, font = C_TEXT2, Fonts.aux
        chip.create_text(width / 2, height / 2, text=label, fill=fg, font=font)


class QualityPicker(tk.Frame):
    """Compact 「清晰度  最高 ▾」 control. Dropdown only when multiple real heights exist."""

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.StringVar,
        on_change: Optional[Callable[[], None]] = None,
        bg: str = C_BG,
    ) -> None:
        super().__init__(master, bg=bg)
        self._var = variable
        self._on_change = on_change
        self._bg = bg
        self._mode = "static"
        self._choices: list[str] = ["最高"]
        self._lbl_title = tk.Label(self, text="清晰度", bg=bg, fg=C_TEXT2, font=Fonts.help)
        self._lbl_title.pack(side="left")
        self._lbl_static = tk.Label(self, text="最高", bg=bg, fg=C_TEXT, font=Fonts.help, anchor="w")
        self._box = tk.Canvas(self, width=52, height=24, highlightthickness=0, bd=0, bg=bg, cursor="hand2")
        self._box.bind("<Button-1>", self._open_menu)
        self._menu = tk.Menu(self, tearoff=0, font=Fonts.help)
        self._var.trace_add("write", lambda *_: self._refresh())
        self.configure_display("static", "最高")

    def configure_display(self, mode: str, text: str, options: Optional[list[str]] = None) -> None:
        self._mode = "dropdown" if mode == "dropdown" else "static"
        if options:
            values: list[str] = []
            for label in options:
                if label and label not in values and label != "原画":
                    values.append(label)
            self._choices = values or ["最高"]
        if self._mode == "dropdown":
            if self._lbl_static.winfo_ismapped():
                self._lbl_static.pack_forget()
            if not self._box.winfo_ismapped():
                self._box.pack(side="left", padx=(6, 0))
            current = quality_pref_label(self._var.get())
            if current not in self._choices:
                current = "最高"
            self._draw_box(current)
        else:
            if self._box.winfo_ismapped():
                self._box.pack_forget()
            self._lbl_static.configure(text=text or "最高")
            if not self._lbl_static.winfo_ismapped():
                self._lbl_static.pack(side="left", padx=(6, 0))

    def set_options(self, labels: list[str]) -> None:
        """Back-compat: multiple labels → dropdown; one or none → static."""
        unique = [x for x in labels if x and x != "原画"]
        if len(unique) > 1:
            self.configure_display("dropdown", "", ["最高", *unique])
        elif len(unique) == 1:
            self.configure_display("static", unique[0], unique)
        else:
            self.configure_display("static", "最高", ["最高"])

    def _refresh(self) -> None:
        if self._mode == "dropdown":
            self._draw_box(quality_pref_label(self._var.get()))

    def _draw_box(self, text: str) -> None:
        label = f"{text} ▾"
        font = Fonts.help
        tw = font.measure(label) if hasattr(font, "measure") else len(label) * 8
        width = max(52, tw + 16)
        self._box.configure(width=width, height=24, bg=self._bg)
        self._box.delete("all")
        rounded_rect(self._box, 0.5, 0.5, width - 0.5, 23.5, 4, fill=C_CARD, outline=C_STROKE2)
        self._box.create_text(width / 2, 12, text=label, fill=C_TEXT, font=font)

    def _open_menu(self, _event: Optional[tk.Event] = None) -> None:
        if self._mode != "dropdown":
            return
        self._menu.delete(0, "end")
        current = quality_pref_label(self._var.get())
        for label in self._choices:
            self._menu.add_command(label=label, command=lambda v=label: self._pick(v))
        try:
            x = self._box.winfo_rootx()
            y = self._box.winfo_rooty() + self._box.winfo_height()
            self._menu.tk_popup(x, y)
        finally:
            self._menu.grab_release()

    def _pick(self, label: str) -> None:
        self._var.set(label)
        self._draw_box(label)
        if self._on_change:
            self._on_change()

    def _changed(self, _event: Optional[tk.Event] = None) -> None:
        if self._on_change:
            self._on_change()


class ModalDialog(tk.Toplevel):
    def __init__(self, master: tk.Tk, title: str, width: int = 400) -> None:
        super().__init__(master)
        self.withdraw()
        self._owner = master
        self._width = width
        self.resizable(False, False)
        self.title(title)
        self.configure(bg=C_CARD)
        try:
            self.transient(master)
        except Exception:
            pass
        apply_window_icon(self, getattr(master, "_icon_photos", []))

        self.body = tk.Frame(self, bg=C_CARD, width=width)
        self.body.pack(fill="both", expand=True, padx=16, pady=(16, 18))
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.bind("<Escape>", lambda _e: self.hide())
        self._on_hide: Optional[Callable[[], None]] = None

    def set_on_hide(self, fn: Optional[Callable[[], None]]) -> None:
        self._on_hide = fn

    def hide(self, notify: bool = True) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()
        if notify and self._on_hide:
            self._on_hide()

    def show(self) -> None:
        owner = self._owner
        try:
            owner.update_idletasks()
        except Exception:
            pass
        self.deiconify()
        self.update_idletasks()
        req_h = max(self.winfo_reqheight(), 120)
        try:
            x = owner.winfo_rootx() + max(0, (owner.winfo_width() - self._width) // 2)
            y = owner.winfo_rooty() + max(0, (owner.winfo_height() - req_h) // 2)
        except Exception:
            x, y = 120, 120
        self.geometry(f"{self._width}x{req_h}+{x}+{y}")
        self.lift()
        try:
            self.grab_set()
        except Exception:
            pass
        self.focus_force()
        self.after_idle(self._raise_front)

    def _raise_front(self) -> None:
        try:
            if not (self.winfo_exists() and self.winfo_ismapped()):
                return
            self.lift()
            try:
                self.grab_set()
            except Exception:
                pass
            self.focus_force()
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("880x640")
        self.minsize(760, 560)
        self.configure(bg=C_BG)
        self._icon_photos: list = []
        self._tray = None
        self._tray_hint_shown = False
        self._hiding_to_tray = False
        apply_window_icon(self, self._icon_photos)
        self.after_idle(lambda: apply_window_icon(self, self._icon_photos))
        self.bind("<Map>", self._on_window_map)
        self.bind("<Unmap>", self._on_window_unmap)

        self.cfg = load_config()
        self.service: Optional[TelegramService] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._ui_q: queue.Queue = queue.Queue()
        self._busy = False
        self._fly_chip: Optional[tk.Canvas] = None
        self._fly_after: Optional[str] = None
        self._stop_flag = threading.Event()
        self._dialogs: list[DialogItem] = []
        self._selected_dialog: Optional[DialogItem] = None
        self._video_chat: Optional[DialogItem] = None
        self._video_items: list[VideoItem] = []
        self._video_gen = 0
        self._video_loading = False
        self._video_has_more = False
        self._video_scan = VideoScanState()
        self._video_notices: list[str] = []
        self._task_gen = 0
        self.queue = DownloadQueue()
        self._phone_pending: Optional[str] = None
        self._awaiting_password = False
        self._logged_in = False
        self._account_name = ""
        self._page = "links"
        self._log_open = False
        self._title_icon_img = None
        self._chat_filter = "全部"
        self._avatar_bytes: dict[int, bytes] = {}
        self._avatar_gen = 0
        self.var_quality = tk.StringVar(value=quality_pref_label(self.cfg.get("video_quality")))

        Fonts.init(self)
        self._build_style()
        self._build_ui()
        self._build_login_dialogs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._drain_ui_queue)
        self.after(120, self._bootstrap)
        self.after(200, self._start_tray)

    # ------------------------------------------------------------------ UI
    def _build_style(self) -> None:
        try:
            sv_ttk.set_theme("light", self)
        except Exception:
            pass
        self.configure(bg=C_BG)
        style = ttk.Style(self)
        style.configure(".", font=Fonts.body)
        style.configure("TRadiobutton", font=Fonts.body, background=C_CARD, foreground=C_TEXT)
        style.map("TRadiobutton", background=[("selected", C_CARD), ("active", C_CARD)])
        style.configure("TCombobox", font=Fonts.body)
        style.configure("TScrollbar", background=C_BG)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.menu_account = tk.Menu(self, tearoff=0, font=Fonts.body)
        self.menu_account.add_command(label="切换账号", command=self._switch_account)
        self.menu_account.add_command(label="退出登录", command=self._logout)

        body = tk.Frame(self, bg=C_BG)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_rail(body)
        self._build_main(body)

    def _refresh_account_bar(self) -> None:
        if getattr(self, "nav_account", None) is not None:
            self.nav_account.set_state(self._logged_in, self._account_name)

    def _on_account_click(self) -> None:
        if self._logged_in:
            self._popup(self.menu_account, self.nav_account)
        else:
            self._open_login()

    def _popup(self, menu: tk.Menu, anchor: tk.Widget) -> None:
        try:
            x = anchor.winfo_rootx()
            y_below = anchor.winfo_rooty() + anchor.winfo_height()
            try:
                end = menu.index("end")
                count = 0 if end is None else int(end) + 1
            except Exception:
                count = 2
            est = count * 28 + 16
            screen_h = anchor.winfo_screenheight()
            if y_below + est > screen_h - 12:
                y = max(8, anchor.winfo_rooty() - est)
            else:
                y = y_below
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _build_rail(self, body: tk.Frame) -> None:
        rail = tk.Frame(body, bg=C_BG, width=64)
        rail.grid(row=0, column=0, sticky="nsw")
        rail.grid_propagate(False)
        tk.Frame(body, bg=C_STROKE, width=1).grid(row=0, column=0, sticky="nse")

        top = tk.Frame(rail, bg=C_BG)
        top.pack(fill="x", padx=8, pady=(10, 0))
        self.nav_links = NavButton(top, "link", "链接", command=lambda: self._show_page("links"))
        self.nav_links.pack(pady=(0, 4))
        self.nav_chats = NavButton(top, "chat", "对话", command=lambda: self._show_page("chats"))
        self.nav_chats.pack()

        bot = tk.Frame(rail, bg=C_BG)
        bot.pack(side="bottom", fill="x", padx=8, pady=(0, 12))
        self.nav_proxy = NavButton(bot, "proxy", "代理", command=self._open_proxy)
        self.nav_proxy.pack(pady=(0, 4))
        self.nav_about = NavButton(bot, "about", "关于", command=self._open_about)
        self.nav_about.pack(pady=(0, 4))
        self.nav_account = AccountButton(bot, command=self._on_account_click)
        self.nav_account.pack()
        self._refresh_account_bar()

    def _build_main(self, body: tk.Frame) -> None:
        self.main = tk.Frame(body, bg=C_BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(1, weight=1)

        # 保存路径（两页共用）
        path = tk.Frame(self.main, bg=C_BG)
        path.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        path.columnconfigure(1, weight=1)
        tk.Label(path, text="保存到", bg=C_BG, fg=C_TEXT2, font=Fonts.help).grid(row=0, column=0, sticky="w")
        default_folder = self.cfg.get("last_save_folder") or str(DEFAULT_DOWNLOADS)
        self.var_folder = tk.StringVar(value=default_folder)
        field = Field(path, height=32)
        field.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.ent_folder = tk.Entry(
            field,
            textvariable=self.var_folder,
            relief="flat",
            bd=0,
            bg=C_CARD,
            fg=C_TEXT,
            font=Fonts.help,
            insertbackground=C_TEXT,
            highlightthickness=0,
        )
        self.ent_folder.pack(fill="both", expand=True, padx=10, pady=6)
        self.btn_browse = FlatButton(path, "浏览", command=self._browse_folder, bg=C_BG)
        self.btn_browse.grid(row=0, column=2, padx=(0, 8))
        self.btn_open = FlatButton(path, "打开文件夹", command=self._open_folder, bg=C_BG)
        self.btn_open.grid(row=0, column=3)

        self.page_host = tk.Frame(self.main, bg=C_BG)
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=16, pady=(10, 0))
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)
        self._build_link_page()
        self._build_chat_page()

        self.tasks = TaskList(
            self.main,
            on_stop=self._request_stop,
            on_open_folder=self._open_folder,
            on_remove=self._on_remove_task,
        )
        self.tasks.grid(row=2, column=0, sticky="nsew", padx=16, pady=(10, 0))
        self.main.rowconfigure(2, weight=1)

        foot = tk.Frame(self.main, bg=C_BG, height=28)
        foot.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 12))
        foot.columnconfigure(1, weight=1)
        self.lbl_ready = tk.Label(foot, text="就绪", bg=C_BG, fg=C_TEXT2, font=Fonts.help)
        self.btn_log = tk.Label(foot, text="▸  日志", bg=C_BG, fg=C_TEXT2, font=Fonts.help, cursor="hand2")
        self.btn_log.pack(side="left")
        self.btn_log.bind("<Button-1>", lambda _e: self._toggle_log())
        self.btn_clear_log = tk.Label(foot, text="清空", bg=C_BG, fg=C_ACCENT, font=Fonts.help, cursor="hand2")
        self.btn_clear_log.bind("<Button-1>", lambda _e: self._clear_log())

        self.log_wrap = tk.Frame(
            self.main,
            bg=C_CARD,
            highlightthickness=1,
            highlightbackground=C_STROKE,
            highlightcolor=C_STROKE,
        )
        self.txt_log = tk.Text(
            self.log_wrap,
            height=8,
            wrap="word",
            state="disabled",
            relief="flat",
            bd=0,
            bg=C_CARD,
            fg=C_TEXT,
            font=Fonts.aux,
            highlightthickness=0,
        )
        log_sb = ttk.Scrollbar(self.log_wrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        log_sb.pack(side="right", fill="y")

        self._show_page("links")
        self._set_logged_in_state(False)

    def _build_link_page(self) -> None:
        f = tk.Frame(self.page_host, bg=C_BG)
        self.page_links = f
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=0)
        box = tk.Frame(
            f,
            bg=C_CARD,
            highlightthickness=1,
            highlightbackground=C_STROKE2,
            highlightcolor=C_ACCENT,
        )
        box.grid(row=0, column=0, sticky="nsew")
        self.txt_links = PlaceholderText(
            box,
            placeholder="粘贴链接，一行一条",
            font=Fonts.body,
            padx=10,
            pady=8,
            height=6,
        )
        self.txt_links.pack(fill="both", expand=True)
        actions = tk.Frame(f, bg=C_BG)
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.quality_links = QualityPicker(actions, self.var_quality, on_change=self._on_quality_change, bg=C_BG)
        self.quality_links.pack(side="left")
        self.btn_dl_link = FlatButton(actions, "开始下载", command=self._on_download_link, variant="primary", bg=C_BG)
        self.btn_dl_link.pack(side="right")

    def _build_chat_page(self) -> None:
        f = tk.Frame(self.page_host, bg=C_BG)
        self.page_chats = f
        f.columnconfigure(0, minsize=236, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        left = tk.Frame(f, bg=C_BG, width=236)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        search = tk.Frame(left, bg=C_BG)
        search.grid(row=0, column=0, sticky="ew")
        search.columnconfigure(0, weight=1)
        sfield = Field(search, height=32)
        sfield.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ic = tk.Canvas(sfield, width=14, height=14, highlightthickness=0, bd=0, bg=C_CARD)
        ic.pack(side="left", padx=(10, 0))
        draw_search_icon(ic, 0, 0, C_TEXT3, 14)
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *_: self._refresh_chat_list())
        self.ent_search = PlaceholderEntry(
            sfield,
            placeholder="搜索对话",
            textvariable=self.var_search,
            font=Fonts.body,
        )
        self.ent_search.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        self.btn_refresh = FlatButton(search, "刷新", command=self._reload_dialogs, bg=C_BG)
        self.btn_refresh.grid(row=0, column=1)

        self.chat_filters = FilterChipBar(left, command=self._on_chat_filter, bg=C_BG)
        self.chat_filters.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.chat_list = ChatList(left, on_select=self._on_chat_select)
        self.chat_list.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

        right = tk.Frame(
            f,
            bg=C_CARD,
            highlightthickness=1,
            highlightbackground=C_STROKE,
            highlightcolor=C_STROKE,
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self.video_pane = right

        self.video_header = tk.Frame(right, bg=C_CARD, height=36)
        self.video_header.grid_propagate(False)
        tk.Frame(self.video_header, bg=C_STROKE, height=1).pack(side="bottom", fill="x")
        hd = tk.Frame(self.video_header, bg=C_CARD)
        hd.pack(fill="both", expand=True)
        self.chk_select_all = CheckMark(hd, command=self._toggle_select_all, bg=C_CARD)
        self.chk_select_all.pack(side="left", padx=(12, 0), pady=9)
        self.lbl_select_all = tk.Label(
            hd, text="全选", bg=C_CARD, fg=C_TEXT, font=Fonts.body, cursor="hand2"
        )
        self.lbl_select_all.pack(side="left", padx=(8, 0))
        self.lbl_select_all.bind("<Button-1>", lambda _e: self._toggle_select_all())
        self.lbl_video_count = tk.Label(hd, text="", bg=C_CARD, fg=C_TEXT2, font=Fonts.help, anchor="w")
        self.lbl_video_count.pack(side="left", padx=(8, 0))
        self.quality_chats = QualityPicker(hd, self.var_quality, on_change=self._on_quality_change, bg=C_CARD)
        self.quality_chats.pack(side="right", padx=(0, 12))

        self.video_body = tk.Frame(right, bg=C_CARD)
        self.video_body.grid(row=1, column=0, sticky="nsew")
        self.video_body.columnconfigure(0, weight=1)
        self.video_body.rowconfigure(0, weight=1)

        self.lbl_video_empty = tk.Label(
            self.video_body,
            text="选择一个对话，查看可下载的视频",
            bg=C_CARD,
            fg=C_TEXT3,
            font=Fonts.body,
        )
        self.lbl_video_empty.place(relx=0.5, rely=0.5, anchor="center")

        self.video_list = VideoList(self.video_body, on_toggle=lambda _item: self._sync_chat_actions())

        foot = tk.Frame(right, bg=C_CARD, height=44)
        self.video_footer = foot
        foot.grid_propagate(False)
        tk.Frame(foot, bg=C_STROKE, height=1).pack(fill="x")
        bar = tk.Frame(foot, bg=C_CARD)
        bar.pack(fill="both", expand=True, padx=12, pady=5)
        self.btn_load_more = FlatButton(bar, "加载更多", command=self._on_load_more, bg=C_CARD)
        self.btn_load_more.pack(side="left")
        self.btn_dl_selected = FlatButton(
            bar, "下载选中", command=self._on_download_selected, variant="primary", bg=C_CARD
        )
        self.btn_dl_selected.pack(side="right")
        self.lbl_picked = tk.Label(bar, text="已选 0", bg=C_CARD, fg=C_TEXT2, font=Fonts.help)
        self.lbl_picked.pack(side="right", padx=(0, 10))
        self._sync_quality_options()

    def _show_video_empty(self, text: str) -> None:
        if self.video_list.winfo_ismapped():
            self.video_list.pack_forget()
        self.lbl_video_empty.configure(text=text)
        self.lbl_video_empty.place(relx=0.5, rely=0.5, anchor="center")

    def _show_video_list(self) -> None:
        self.lbl_video_empty.place_forget()
        if not self.video_list.winfo_ismapped():
            self.video_list.pack(fill="both", expand=True)

    def _reset_video_pane(self, message: str = "选择一个对话，查看可下载的视频") -> None:
        self._video_gen += 1
        self._video_chat = None
        self._video_items = []
        self._video_loading = False
        self._video_has_more = False
        self._video_scan = VideoScanState()
        self._video_notices = []
        self.video_list.clear()
        if self.video_header.winfo_ismapped():
            self.video_header.grid_forget()
        if self.video_footer.winfo_ismapped():
            self.video_footer.grid_forget()
        self._show_video_empty(message)
        self._sync_chat_actions()

    def _reveal_video_chrome(self, _title: str = "") -> None:
        if not self.video_header.winfo_ismapped():
            self.video_header.grid(row=0, column=0, sticky="ew")
        if not self.video_footer.winfo_ismapped():
            self.video_footer.grid(row=2, column=0, sticky="ew")

    def _sync_chat_actions(self) -> None:
        if not hasattr(self, "btn_dl_selected"):
            return
        has_items = bool(self._video_items)
        selected_n = len(self.video_list.selected_ids) if has_items else 0
        can_act = bool(self._logged_in and not self._busy)
        if has_items and selected_n == len(self._video_items):
            self.chk_select_all.set_state("on")
        elif selected_n:
            self.chk_select_all.set_state("mid")
        else:
            self.chk_select_all.set_state("off")
        self.chk_select_all.set_enabled(has_items)
        if self._video_chat is not None and not (self._video_loading and not has_items):
            note_n = len(getattr(self, "_video_notices", []) or [])
            text = f"{len(self._video_items)} 个视频"
            if note_n:
                text += f" · {note_n} 条说明"
            self.lbl_video_count.configure(text=text)
        else:
            self.lbl_video_count.configure(text="")
        more_ok = can_act and self._video_chat is not None and self._video_has_more and not self._video_loading
        self.btn_load_more.set_state("normal" if more_ok else "disabled")
        self.lbl_picked.configure(text=f"已选 {selected_n}")
        self.btn_dl_selected.set_state("normal" if can_act and selected_n else "disabled")

    def _toggle_select_all(self) -> None:
        if not self._video_items:
            return
        self.video_list.set_all_selected(not self.video_list.all_selected())
        self._sync_chat_actions()

    def _quality_pref(self) -> str:
        return quality_pref_value(self.var_quality.get())

    def _sync_quality_options(self) -> None:
        labels: list[str] = []
        seen: set[str] = set()
        for item in getattr(self, "_video_items", []) or []:
            for q in item.qualities or []:
                if q.label and q.label not in seen and q.label != "原画":
                    seen.add(q.label)
                    labels.append(q.label)
        def _rank(label: str) -> int:
            digits = "".join(ch for ch in label if ch.isdigit())
            return int(digits) if digits else 0
        labels.sort(key=_rank, reverse=True)
        chats = getattr(self, "quality_chats", None)
        links = getattr(self, "quality_links", None)
        if chats is not None:
            chats.set_options(labels)
        if links is not None:
            # Links page has no per-message list yet: keep a compact 最高
            # until real heights have been discovered in this session.
            if len(labels) > 1:
                links.configure_display("dropdown", "", ["最高", *labels])
            elif len(labels) == 1:
                links.configure_display("static", labels[0], labels)
            else:
                links.configure_display("static", "最高", ["最高"])

    def _on_quality_change(self) -> None:
        pref = self._quality_pref()
        self.var_quality.set(quality_pref_label(pref))
        self.cfg = update_config(video_quality=pref)
        if hasattr(self, "video_list"):
            self.video_list.refresh_quality(pref)
        self._sync_quality_options()


    def _show_page(self, name: str) -> None:
        self._page = name
        self.nav_links.set_active(name == "links")
        self.nav_chats.set_active(name == "chats")
        if name == "links":
            self.page_chats.grid_forget()
            self.page_links.grid(row=0, column=0, sticky="new")
        else:
            self.page_links.grid_forget()
            self.page_chats.grid(row=0, column=0, sticky="nsew")
        self._relayout_tasks()

    def _relayout_tasks(self) -> None:
        on_links = self._page == "links"
        # 任务列表只出现在「链接」页；对话里用飞向图标的动画提示去那里查看
        self.main.rowconfigure(1, weight=0 if on_links else 1)
        if on_links:
            self.tasks.set_pane_mode(compact=False, closable=False)
            self.main.rowconfigure(2, weight=1)
            if not self.tasks.winfo_ismapped():
                self.tasks.grid(row=2, column=0, sticky="nsew", padx=16, pady=(10, 0))
            if self.lbl_ready.winfo_ismapped():
                self.lbl_ready.pack_forget()
        else:
            if self.tasks.winfo_ismapped():
                self.tasks.grid_forget()
            self.main.rowconfigure(2, weight=0)
            if self.lbl_ready.winfo_ismapped():
                self.lbl_ready.pack_forget()
        self._refresh_links_badge()

    def _refresh_links_badge(self) -> None:
        if not hasattr(self, "nav_links"):
            return
        if self._page == "links":
            self.nav_links.set_badge(0)
            return
        _done, _running, total = self.tasks.counts() if hasattr(self, "tasks") else (0, 0, 0)
        count = total if (total or self._busy) else 0
        if not total and self._busy:
            count = 1
        self.nav_links.set_badge(count)

    def _widget_center(self, widget: tk.Misc) -> tuple[float, float]:
        self.update_idletasks()
        rx, ry = self.winfo_rootx(), self.winfo_rooty()
        return (
            widget.winfo_rootx() - rx + widget.winfo_width() / 2,
            widget.winfo_rooty() - ry + widget.winfo_height() / 2,
        )

    def _cancel_task_fly(self) -> None:
        if self._fly_after:
            try:
                self.after_cancel(self._fly_after)
            except Exception:
                pass
            self._fly_after = None
        if self._fly_chip is not None:
            try:
                self._fly_chip.destroy()
            except Exception:
                pass
            self._fly_chip = None

    def _fly_task_to_links(self, count: int) -> None:
        """对话里点「下载选中」后，一小块任务卡片飞向左侧「链接」图标。"""
        self._cancel_task_fly()
        try:
            sx, sy = self._widget_center(self.btn_dl_selected)
            ex, ey = self._widget_center(self.nav_links)
        except Exception:
            self._refresh_links_badge()
            self.nav_links.pulse_badge()
            return
        # 飞向图标中心，略偏上，对准链结图标而不是文字
        ey = ey - 8
        text = f"{count} 个任务" if count > 1 else "1 个任务"
        base_w, base_h = 102, 30
        chip = tk.Canvas(self, width=base_w, height=base_h, highlightthickness=0, bd=0, bg=C_BG)
        self._fly_chip = chip

        def paint(scale: float) -> tuple[int, int]:
            w = max(16, int(base_w * scale))
            h = max(12, int(base_h * scale))
            chip.configure(width=w, height=h)
            chip.delete("all")
            r = max(4, 8 * scale)
            rounded_rect(chip, 0, 0, w, h, r, fill=C_ACCENT, outline=C_HOVER)
            if scale >= 0.55:
                chip.create_text(w / 2, h / 2, text=text, fill="#FFFFFF", font=Fonts.help)
            return w, h

        def place_at(cx: float, cy: float, scale: float) -> None:
            w, h = paint(scale)
            chip.place(x=int(cx - w / 2), y=int(cy - h / 2))
            chip.lift()

        place_at(sx, sy, 0.86)
        t0 = time.perf_counter()
        duration = 0.48

        def tick() -> None:
            if self._fly_chip is None or not chip.winfo_exists():
                self._fly_after = None
                return
            p = min(1.0, (time.perf_counter() - t0) / duration)
            # ease-in-out + 向上拋物线，末段缩小吸进图标
            e = 0.5 - 0.5 * math.cos(math.pi * p)
            arc = -46 * math.sin(math.pi * p)
            scale = 1.0 if p < 0.68 else max(0.28, 1.0 - (p - 0.68) / 0.32 * 0.72)
            place_at(sx + (ex - sx) * e, sy + (ey - sy) * e + arc, scale)
            if p < 1:
                self._fly_after = self.after(16, tick)
            else:
                self._cancel_task_fly()
                self._refresh_links_badge()
                self.nav_links.pulse_badge()

        self._fly_after = self.after(16, tick)

    def _toggle_log(self) -> None:
        self._log_open = not self._log_open
        if self._log_open:
            self.btn_log.configure(text="▾  日志")
            self.log_wrap.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 12))
            self.main.rowconfigure(4, weight=0)
            if not self.btn_clear_log.winfo_ismapped():
                self.btn_clear_log.pack(side="right")
        else:
            self.btn_log.configure(text="▸  日志")
            self.log_wrap.grid_forget()
            if self.btn_clear_log.winfo_ismapped():
                self.btn_clear_log.pack_forget()

    def _set_logged_in_state(self, logged_in: bool, name: str = "") -> None:
        self._logged_in = logged_in
        self._account_name = name or self._account_name
        self._refresh_account_bar()
        if not logged_in:
            self.btn_dl_link.set_state("disabled")
        elif not self._busy:
            self.btn_dl_link.set_state("normal")
        self._sync_chat_actions()

    def _set_busy_ui(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.btn_dl_link.set_state("disabled")
        elif self._logged_in:
            self.btn_dl_link.set_state("normal")
        else:
            self.btn_dl_link.set_state("disabled")
        self._sync_chat_actions()
        self._relayout_tasks()

    # -------------------------------------------------------------- dialogs
    def _build_login_dialogs(self) -> None:
        self._build_dlg_api()
        self._build_dlg_phone()
        self._build_dlg_code()
        self._build_dlg_2fa()
        self._build_dlg_proxy()
        self._build_dlg_about()
        self._modals = [
            self.dlg_api,
            self.dlg_phone,
            self.dlg_code,
            self.dlg_2fa,
            self.dlg_proxy,
            self.dlg_about,
        ]
        self.bind("<FocusIn>", self._raise_open_modal, add="+")

    def _form_label(self, master: tk.Misc, text: str) -> tk.Label:
        return tk.Label(master, text=text, bg=C_CARD, fg=C_TEXT2, font=Fonts.help, anchor="w")

    def _form_help(self, master: tk.Misc, text: str, color: str = C_TEXT2) -> tk.Label:
        return tk.Label(master, text=text, bg=C_CARD, fg=color, font=Fonts.help, wraplength=400, justify="left", anchor="w")

    def _link_label(self, master: tk.Misc, text: str, url: str) -> tk.Label:
        lbl = tk.Label(master, text=text, bg=C_CARD, fg=C_ACCENT, font=Fonts.help, cursor="hand2", anchor="w")
        lbl.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
        lbl.bind("<Enter>", lambda _e: lbl.configure(font=Fonts.help, fg=C_HOVER))
        lbl.bind("<Leave>", lambda _e: lbl.configure(fg=C_ACCENT))
        return lbl

    def _link_label_action(self, master: tk.Misc, text: str, command: Callable[[], None]) -> tk.Label:
        lbl = tk.Label(master, text=text, bg=C_CARD, fg=C_ACCENT, font=Fonts.help, cursor="hand2", anchor="w")
        lbl.bind("<Button-1>", lambda _e: command())
        lbl.bind("<Enter>", lambda _e: lbl.configure(fg=C_HOVER))
        lbl.bind("<Leave>", lambda _e: lbl.configure(fg=C_ACCENT))
        return lbl

    def _form_entry(
        self,
        master: tk.Misc,
        var: tk.StringVar,
        placeholder: str = "",
        show: str = "",
        width: int = 36,
    ) -> Field:
        box = Field(master, height=32)
        if placeholder:
            PlaceholderEntry(box, placeholder=placeholder, textvariable=var, show=show, font=Fonts.body).pack(
                fill="both", expand=True, padx=10, pady=6
            )
        else:
            tk.Entry(
                box,
                textvariable=var,
                show=show,
                font=Fonts.body,
                relief="flat",
                bd=0,
                bg=C_CARD,
                fg=C_TEXT,
                insertbackground=C_TEXT,
                highlightthickness=0,
            ).pack(fill="both", expand=True, padx=10, pady=6)
        return box

    def _build_dlg_api(self) -> None:
        self.dlg_api = ModalDialog(self, "连接 Telegram", width=440)
        b = self.dlg_api.body
        self._form_help(b, "首次使用需填写 API 凭据，请用同一手机号申请。").pack(fill="x", pady=(0, 12))
        grid = tk.Frame(b, bg=C_CARD)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self._form_label(grid, "api_id").grid(row=0, column=0, sticky="e", padx=(0, 10), pady=(0, 8))
        self.var_api_id = tk.StringVar(value=str(self.cfg.get("api_id") or ""))
        self._form_entry(grid, self.var_api_id, placeholder="数字").grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self._form_label(grid, "api_hash").grid(row=1, column=0, sticky="e", padx=(0, 10))
        self.var_api_hash = tk.StringVar(value=str(self.cfg.get("api_hash") or ""))
        self._form_entry(grid, self.var_api_hash, placeholder="从申请页复制").grid(row=1, column=1, sticky="ew")
        FlatButton(b, "打开申请页", command=self._open_api_site, bg=C_CARD).pack(anchor="w", pady=(12, 12))
        self._form_help(b, "只保存在本机，请不要发给别人。").pack(fill="x")
        ft = tk.Frame(b, bg=C_CARD)
        ft.pack(fill="x", pady=(16, 0))
        FlatButton(ft, "保存并继续", command=self._save_api, variant="primary", bg=C_CARD).pack(side="right")
        FlatButton(ft, "取消", command=self.dlg_api.hide, bg=C_CARD).pack(side="right", padx=(0, 8))
        self.dlg_api.set_on_hide(None)

    def _build_dlg_phone(self) -> None:
        self.dlg_phone = ModalDialog(self, "登录", width=400)
        b = self.dlg_phone.body
        self._form_label(b, "手机号").pack(fill="x")
        self.var_phone = tk.StringVar()
        self._form_entry(b, self.var_phone, placeholder="11 位手机号，或 +区号").pack(fill="x", pady=(6, 6))
        self._form_help(b, "与官方 Telegram 同一号码，大陆会自动加 +86。将使用已保存的 API 凭据。").pack(fill="x")
        self._link_label_action(b, "修改 API 凭据", self._open_api_from_phone).pack(anchor="w", pady=(8, 0))
        ft = tk.Frame(b, bg=C_CARD)
        ft.pack(fill="x", pady=(16, 0))
        FlatButton(ft, "发送验证码", command=self._send_code, variant="primary", bg=C_CARD).pack(side="right")
        FlatButton(ft, "取消", command=self.dlg_phone.hide, bg=C_CARD).pack(side="right", padx=(0, 8))

    def _build_dlg_code(self) -> None:
        self.dlg_code = ModalDialog(self, "验证码", width=400)
        b = self.dlg_code.body
        self._form_help(b, "验证码已发送到 Telegram。").pack(fill="x", pady=(0, 12))
        self._form_label(b, "验证码").pack(fill="x")
        self.var_code = tk.StringVar()
        self._form_entry(b, self.var_code).pack(fill="x", pady=(6, 0))
        ft = tk.Frame(b, bg=C_CARD)
        ft.pack(fill="x", pady=(16, 0))
        FlatButton(ft, "登录", command=self._do_sign_in, variant="primary", bg=C_CARD).pack(side="right")
        FlatButton(ft, "取消", command=self.dlg_code.hide, bg=C_CARD).pack(side="right", padx=(0, 8))

    def _build_dlg_2fa(self) -> None:
        self.dlg_2fa = ModalDialog(self, "两步验证", width=400)
        b = self.dlg_2fa.body
        self._form_help(b, "该账号已开启两步验证，请输入云密码。").pack(fill="x", pady=(0, 12))
        self._form_label(b, "密码").pack(fill="x")
        self.var_password = tk.StringVar()
        self._form_entry(b, self.var_password, show="•").pack(fill="x", pady=(6, 0))
        ft = tk.Frame(b, bg=C_CARD)
        ft.pack(fill="x", pady=(16, 0))
        FlatButton(ft, "登录", command=self._do_sign_in_password, variant="primary", bg=C_CARD).pack(side="right")
        FlatButton(ft, "取消", command=self.dlg_2fa.hide, bg=C_CARD).pack(side="right", padx=(0, 8))

    def _build_dlg_proxy(self) -> None:
        self.dlg_proxy = ModalDialog(self, "代理", width=440)
        b = self.dlg_proxy.body
        grid = tk.Frame(b, bg=C_CARD)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        self.var_proxy_type_label = tk.StringVar(value="直连")
        self.var_proxy_type = tk.StringVar(value=str(self.cfg.get("proxy_type") or "none"))
        self._form_label(grid, "类型").grid(row=0, column=0, sticky="e", padx=(0, 10), pady=(0, 8))
        cmb_box = Field(grid, height=32)
        cmb_box.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.cmb_proxy = ttk.Combobox(
            cmb_box,
            textvariable=self.var_proxy_type_label,
            values=("直连", "SOCKS5", "HTTP"),
            state="readonly",
        )
        self.cmb_proxy.pack(fill="both", expand=True, padx=2, pady=2)
        self.cmb_proxy.bind("<<ComboboxSelected>>", lambda _e: self._sync_proxy_kind())

        self._form_label(grid, "地址").grid(row=1, column=0, sticky="e", padx=(0, 10), pady=(0, 8))
        self.var_proxy_host = tk.StringVar(value=str(self.cfg.get("proxy_host") or "127.0.0.1"))
        self._form_entry(grid, self.var_proxy_host).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        self._form_label(grid, "端口").grid(row=2, column=0, sticky="e", padx=(0, 10), pady=(0, 8))
        self.var_proxy_port = tk.StringVar(value=str(self.cfg.get("proxy_port") or "7891"))
        self._form_entry(grid, self.var_proxy_port).grid(row=2, column=1, sticky="ew", pady=(0, 8))

        self._form_label(grid, "用户名").grid(row=3, column=0, sticky="e", padx=(0, 10), pady=(0, 8))
        self.var_proxy_user = tk.StringVar(value=str(self.cfg.get("proxy_user") or ""))
        self._form_entry(grid, self.var_proxy_user, placeholder="可选").grid(row=3, column=1, sticky="ew", pady=(0, 8))

        self._form_label(grid, "密码").grid(row=4, column=0, sticky="e", padx=(0, 10))
        self.var_proxy_pass = tk.StringVar(value=str(self.cfg.get("proxy_pass") or ""))
        self._form_entry(grid, self.var_proxy_pass, placeholder="可选", show="•").grid(row=4, column=1, sticky="ew")

        self.lbl_proxy_status = tk.Label(b, text="", bg=C_CARD, fg=C_TEXT2, font=Fonts.help, anchor="w", justify="left", wraplength=400)
        self.lbl_proxy_status.pack(fill="x", pady=(12, 0))

        ft = tk.Frame(b, bg=C_CARD)
        ft.pack(fill="x", pady=(16, 0))
        left = tk.Frame(ft, bg=C_CARD)
        left.pack(side="left")
        FlatButton(left, "检测本机", command=self._detect_proxy, bg=C_CARD).pack(side="left")
        FlatButton(left, "测试", command=self._test_proxy, bg=C_CARD).pack(side="left", padx=(8, 0))
        FlatButton(ft, "保存", command=self._save_proxy, variant="primary", bg=C_CARD).pack(side="right")
        FlatButton(ft, "取消", command=self.dlg_proxy.hide, bg=C_CARD).pack(side="right", padx=(0, 8))

    def _build_dlg_about(self) -> None:
        self.dlg_about = ModalDialog(self, "关于", width=400)
        b = self.dlg_about.body
        row = tk.Frame(b, bg=C_CARD)
        row.pack(fill="x")
        ic = tk.Canvas(row, width=36, height=36, highlightthickness=0, bd=0, bg=C_CARD)
        ic.pack(side="left")
        rounded_rect(ic, 0, 0, 36, 36, 8, fill=C_ACCENT, outline=C_ACCENT)
        draw_download_icon(ic, 9, 9, "#FFFFFF", 18)
        texts = tk.Frame(row, bg=C_CARD)
        texts.pack(side="left", padx=12)
        tk.Label(texts, text=APP_NAME, bg=C_CARD, fg=C_TEXT, font=Fonts.title, anchor="w").pack(anchor="w")
        tk.Label(texts, text=APP_VERSION, bg=C_CARD, fg=C_TEXT2, font=Fonts.help, anchor="w").pack(anchor="w")
        dev = tk.Frame(b, bg=C_CARD)
        dev.pack(fill="x", pady=(12, 0))
        tk.Label(dev, text="开发者", bg=C_CARD, fg=C_TEXT2, font=Fonts.help, anchor="w").pack(anchor="w")
        tk.Label(dev, text="XxxLllattice", bg=C_CARD, fg=C_TEXT, font=Fonts.body, anchor="w").pack(anchor="w", pady=(6, 0))
        links = tk.Frame(b, bg=C_CARD)
        links.pack(fill="x", pady=(12, 0))
        def _link_row(parent: tk.Misc, key: str, handle: str, url: str) -> None:
            line = tk.Frame(parent, bg=C_CARD)
            line.pack(fill="x", pady=(0, 8))
            tk.Label(line, text=key, bg=C_CARD, fg=C_TEXT2, font=Fonts.help, width=6, anchor="w").pack(side="left")
            self._link_label(line, handle, url).pack(side="left")
        _link_row(links, "GitHub", f"@{GITHUB_HANDLE}", GITHUB_URL)
        _link_row(links, "X", f"@{X_HANDLE}", X_URL)
        ft = tk.Frame(b, bg=C_CARD)
        ft.pack(fill="x", pady=(4, 0))
        FlatButton(ft, "关闭", command=self.dlg_about.hide, bg=C_CARD).pack(side="right")

    def _sync_proxy_kind(self) -> None:
        label = self.var_proxy_type_label.get()
        self.var_proxy_type.set(PROXY_LABEL_TO_KIND.get(label, "none"))

    def _open_about(self) -> None:
        self._show_dialog(self.dlg_about)

    def _raise_open_modal(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        for dialog in getattr(self, "_modals", []):
            try:
                if dialog.winfo_exists() and dialog.winfo_ismapped() and dialog.state() != "withdrawn":
                    dialog.lift()
                    try:
                        dialog.grab_set()
                    except Exception:
                        pass
                    dialog.focus_force()
                    return
            except Exception:
                continue

    def _show_dialog(self, dlg: ModalDialog) -> None:
        for d in self._modals:
            if d is not dlg:
                d.hide(notify=False)
        dlg.show()

    def _hide_login_dialogs(self) -> None:
        for d in (self.dlg_api, self.dlg_phone, self.dlg_code, self.dlg_2fa):
            d.hide(notify=False)


    # ----------------------------------------------------------- bootstrap

    def _bootstrap(self) -> None:
        self._maybe_show_notice()
        self._maybe_autodetect_proxy()
        if startup_login_target(has_credentials=has_api_credentials(self.cfg), authorized=False) == "api":
            self._set_logged_in_state(False)
            self._show_dialog(self.dlg_api)
            return
        self._start_loop_and_connect()

    def _maybe_show_notice(self) -> None:
        if self.cfg.get("noticed_personal_use"):
            return
        messagebox.showinfo(
            APP_NAME,
            "请仅保存你有权保存、用于个人备份的内容，并遵守 Telegram 使用条款。",
        )
        update_config(noticed_personal_use=True)
        self.cfg = load_config()

    def _maybe_autodetect_proxy(self) -> None:
        kind = str(self.cfg.get("proxy_type") or "none").strip().lower()
        if kind not in ("", "none", "off", "direct"):
            return
        found = pick_best_proxy()
        if not found:
            return
        update_config(
            proxy_type=found["type"],
            proxy_host=found["host"],
            proxy_port=int(found["port"]),
        )
        self.cfg = load_config()
        self._log(f"已自动检测到本地代理：{found['label']}（{found['type']} {found['host']}:{found['port']}），可在「代理」里修改。")

    def _open_login(self) -> None:
        if not has_api_credentials(self.cfg):
            self._show_dialog(self.dlg_api)
            return
        if self.service is None:
            self._start_loop_and_connect()
            self._show_dialog(self.dlg_phone)
            return
        self._show_dialog(self.dlg_phone)

    def _open_api_from_phone(self) -> None:
        self.cfg = load_config()
        self.var_api_id.set(str(self.cfg.get("api_id") or ""))
        self.var_api_hash.set(str(self.cfg.get("api_hash") or ""))
        self._show_dialog(self.dlg_api)

    def _open_api_site(self) -> None:
        try:
            webbrowser.open("https://my.telegram.org")
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc), parent=self.dlg_api)

    def _fill_proxy_vars(self, item: dict) -> None:
        kind = str(item.get("type") or "socks5")
        self.var_proxy_type.set(kind)
        self.var_proxy_type_label.set(PROXY_KIND_TO_LABEL.get(kind, "SOCKS5"))
        self.var_proxy_host.set(str(item.get("host") or "127.0.0.1"))
        self.var_proxy_port.set(str(item.get("port") or ""))

    def _set_proxy_status(self, text: str, kind: str = "muted") -> None:
        color = {"ok": C_OK, "err": C_ERR}.get(kind, C_TEXT2)
        self.lbl_proxy_status.configure(text=text, fg=color)

    def _detect_proxy(self) -> None:
        items = detect_local_proxies()
        if not items:
            self._set_proxy_status("没有检测到本机常见代理端口。请先打开 Clash / v2rayN，并确认端口已开启。", "err")
            return
        best = pick_best_proxy(items)
        assert best
        self._fill_proxy_vars(best)
        lines = [f"已填入：{best['label']}（{best['type']} {best['host']}:{best['port']}）。"]
        if len(items) > 1:
            others = "、".join(f"{i['type']} {i['host']}:{i['port']}" for i in items if i is not best)
            if others:
                lines.append(f"另外还发现：{others}")
        self._set_proxy_status(" ".join(lines), "ok")

    def _test_proxy(self) -> None:
        self._sync_proxy_kind()
        kind = (self.var_proxy_type.get() or "none").strip().lower()
        host = (self.var_proxy_host.get() or "").strip() or "127.0.0.1"
        port_s = (self.var_proxy_port.get() or "").strip()
        self._set_proxy_status("正在测试…")
        self.dlg_proxy.update_idletasks()
        try:
            port = int(port_s) if port_s else 0
            msg = test_proxy_connection(
                kind,
                host,
                port,
                user=(self.var_proxy_user.get() or "").strip(),
                password=self.var_proxy_pass.get() or "",
            )
            self._set_proxy_status(msg, "ok")
        except Exception as exc:
            self._set_proxy_status(explain_error(exc), "err")

    def _start_loop_and_connect(self) -> None:
        self._ensure_loop()
        self._log(f"正在连接 Telegram（当前网络：{format_proxy_label(self.cfg)}）…")
        self._log("若超过十几秒还停在这一步，请先打开能访问 Telegram 的 VPN，或在左侧边栏点「代理」。")
        self._run_async(self._async_startup(), on_done=self._after_startup, on_error=self._startup_failed)

    def _ensure_loop(self) -> None:
        if not (self.loop and self._loop_thread and self._loop_thread.is_alive()):
            self.loop = asyncio.new_event_loop()

            def _runner() -> None:
                asyncio.set_event_loop(self.loop)
                self.loop.run_forever()

            self._loop_thread = threading.Thread(target=_runner, name="tg-asyncio", daemon=True)
            self._loop_thread.start()

        if self.service is None:
            api_id = int(str(self.cfg["api_id"]).strip())
            api_hash = str(self.cfg["api_hash"]).strip()
            self.service = TelegramService(api_id, api_hash, proxy=proxy_from_config(self.cfg))

    async def _async_startup(self) -> bool:
        assert self.service
        await self.service.connect()
        return await self.service.is_authorized()

    def _after_startup(self, authorized: bool) -> None:
        if authorized:
            self._run_async(self._async_load_me_and_dialogs(), on_done=None, on_error=self._show_error)
            return
        self._set_logged_in_state(False)
        self._log("需要登录账号。可点左侧边栏「登录」，或现在完成手机号验证。")
        # 若用户已经在输入手机号/验证码，不要把窗口切回去
        target = startup_login_target(
            has_credentials=has_api_credentials(self.cfg),
            authorized=False,
            phone_pending=bool(self._phone_pending),
            code_dialog=self._login_dialog_open(self.dlg_code),
            password_dialog=self._login_dialog_open(self.dlg_2fa),
            api_dialog=self._login_dialog_open(self.dlg_api),
            phone_dialog=self._login_dialog_open(self.dlg_phone),
        )
        if target == "phone":
            self._show_dialog(self.dlg_phone)

    def _login_dialog_open(self, *dialogs: ModalDialog) -> bool:
        for dialog in dialogs:
            try:
                if dialog.winfo_exists() and dialog.winfo_ismapped() and dialog.state() != "withdrawn":
                    return True
            except Exception:
                continue
        return False

    def _startup_failed(self, exc: BaseException) -> None:
        msg = explain_error(exc)
        self._log(msg, error=True)
        if "登录已失效" in msg:
            messagebox.showwarning(APP_NAME, msg + "\n\n将清除本机会话，请重新登录。api_id 和代理设置会保留。")
            clear_session_only()
            self.cfg = load_config()
            target = login_dialog_after_session_loss(has_api_credentials(self.cfg))
            self._show_dialog(self.dlg_phone if target == "phone" else self.dlg_api)
        else:
            messagebox.showerror(APP_NAME, msg)

    async def _async_load_me_and_dialogs(self) -> None:
        assert self.service
        me = await self.service.get_me()
        self.service.persist_session()
        label = self.service.me_label(me)
        self._ui(lambda: self._set_logged_in_state(True, label))
        self._ui(lambda: self._log(f"已自动登录：{label}"))
        dialogs = await self.service.list_dialogs()
        self._ui(lambda: self._set_dialogs(dialogs))

    def _set_dialogs(self, dialogs: list[DialogItem]) -> None:
        self._dialogs = dialogs
        self._avatar_gen += 1
        # Keep already-downloaded avatars when the same dialog returns
        keep = {d.id for d in dialogs}
        self._avatar_bytes = {k: v for k, v in self._avatar_bytes.items() if k in keep}
        for d in dialogs:
            if d.id in self._avatar_bytes or not d.photo_id:
                continue
            cached = load_cached_avatar(d.id, d.photo_id)
            if cached:
                self._avatar_bytes[d.id] = cached
        if self._selected_dialog and not any(d.id == self._selected_dialog.id for d in dialogs):
            self._selected_dialog = None
        if self._video_chat and not any(d.id == self._video_chat.id for d in dialogs):
            self._reset_video_pane()
        self._refresh_chat_list()
        self._log(f"已加载 {len(dialogs)} 个对话。")
        self._start_avatar_loads(dialogs)

    def _on_chat_filter(self, value: str) -> None:
        self._chat_filter = value
        self._refresh_chat_list()

    def _refresh_chat_list(self) -> None:
        if not hasattr(self, "chat_list"):
            return
        q = (self.ent_search.get() if hasattr(self, "ent_search") else self.var_search.get() or "").strip()
        kind = getattr(self, "_chat_filter", "全部")
        items = filter_dialogs(self._dialogs, kind, q)
        selected = visible_chat_selection(self._selected_dialog, items)
        self.chat_list.set_items(items, selected)
        self.chat_list.apply_avatars(self._avatar_bytes, token=self._avatar_gen)

    def _start_avatar_loads(self, dialogs: list[DialogItem]) -> None:
        if not self.service:
            return
        gen = self._avatar_gen
        missing = [d for d in dialogs if d.photo_id and d.id not in self._avatar_bytes]
        if not missing:
            return
        self._run_async(self._async_load_avatars(missing, gen), on_error=lambda _e: None)

    async def _async_load_avatars(self, items: list[DialogItem], gen: int) -> None:
        assert self.service
        for item in items:
            if gen != self._avatar_gen:
                return
            if item.id in self._avatar_bytes:
                continue
            try:
                data = await self.service.download_profile_photo(item)
            except Exception:
                continue
            if gen != self._avatar_gen:
                return
            if not data:
                continue
            self._avatar_bytes[item.id] = data
            self._ui(lambda i=item.id, b=data, g=gen: self._apply_avatar(g, i, b))

    def _apply_avatar(self, gen: int, dialog_id: int, data: bytes) -> None:
        if gen != self._avatar_gen:
            return
        self._avatar_bytes[dialog_id] = data
        if hasattr(self, "chat_list"):
            self.chat_list.set_avatar(dialog_id, data, token=gen)

    def _on_chat_select(self, item: DialogItem) -> None:
        self._selected_dialog = item
        if self._video_chat and self._video_chat.id == item.id:
            return
        self._start_load_videos(item, reset=True)

    # -------------------------------------------------------------- login
    def _save_api(self) -> None:
        api_id = self.var_api_id.get().strip()
        api_hash = self.var_api_hash.get().strip()
        if not api_id.isdigit():
            messagebox.showerror(APP_NAME, "api_id 应为纯数字。", parent=self.dlg_api)
            return
        if len(api_hash) < 16:
            messagebox.showerror(APP_NAME, "api_hash 看起来不正确，请从 my.telegram.org 复制完整值。", parent=self.dlg_api)
            return
        self.cfg = update_config(api_id=api_id, api_hash=api_hash)
        self._hide_login_dialogs()
        self._log("API 已保存到本机。")
        self._start_loop_and_connect()

    def _open_proxy(self) -> None:
        self.cfg = load_config()
        kind = str(self.cfg.get("proxy_type") or "none")
        self.var_proxy_type.set(kind)
        self.var_proxy_type_label.set(PROXY_KIND_TO_LABEL.get(kind.lower(), "直连"))
        self.var_proxy_host.set(str(self.cfg.get("proxy_host") or "127.0.0.1"))
        self.var_proxy_port.set(str(self.cfg.get("proxy_port") or "7891"))
        self.var_proxy_user.set(str(self.cfg.get("proxy_user") or ""))
        self.var_proxy_pass.set(str(self.cfg.get("proxy_pass") or ""))
        self._set_proxy_status("")
        self._show_dialog(self.dlg_proxy)

    def _save_proxy(self) -> None:
        self._sync_proxy_kind()
        kind = (self.var_proxy_type.get() or "none").strip().lower()
        host = (self.var_proxy_host.get() or "").strip() or "127.0.0.1"
        port_s = (self.var_proxy_port.get() or "").strip()
        try:
            kind, port = validate_proxy_settings(kind, port_s)
        except UserFacingError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.dlg_proxy)
            return
        self.cfg = update_config(
            proxy_type=kind,
            proxy_host=host,
            proxy_port=port,
            proxy_user=(self.var_proxy_user.get() or "").strip(),
            proxy_pass=self.var_proxy_pass.get() or "",
        )
        try:
            proxy_from_config(self.cfg)
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc), parent=self.dlg_proxy)
            return
        self.dlg_proxy.hide()
        self._log(f"代理已保存：{format_proxy_label(self.cfg)}，正在重新连接…")
        self._reset_service_and_reconnect()

    def _reset_service_and_reconnect(self) -> None:
        def _after_close(_: Any = None) -> None:
            self.service = None
            self._start_loop_and_connect()

        if self.service and self.loop:
            self._run_async(self.service.disconnect(), on_done=_after_close, on_error=lambda _e: _after_close())
        else:
            _after_close()

    def _send_code(self) -> None:
        phone = self.var_phone.get().strip()
        if not phone:
            messagebox.showerror(APP_NAME, "请输入手机号。", parent=self.dlg_phone)
            return
        try:
            self._ensure_loop()
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc), parent=self.dlg_phone)
            return
        if self.service is None:
            messagebox.showerror(APP_NAME, "尚未准备好连接，请稍后再试。", parent=self.dlg_phone)
            return
        self._phone_pending = phone
        self._run_async(
            self._async_send_code(phone),
            on_done=lambda _: self._after_code_sent(),
            on_error=lambda e: messagebox.showerror(APP_NAME, explain_error(e), parent=self.dlg_phone),
        )

    async def _async_send_code(self, phone: str) -> None:
        assert self.service
        await self.service.connect()
        await self.service.send_code(phone)

    def _after_code_sent(self) -> None:
        self._log("验证码已发送，请查收 Telegram / 短信。")
        self.var_code.set("")
        self.var_password.set("")
        self._awaiting_password = False
        self._show_dialog(self.dlg_code)

    def _do_sign_in(self) -> None:
        phone = self._phone_pending or self.var_phone.get().strip()
        code = self.var_code.get()
        self.var_code.set("")

        async def _job() -> Any:
            assert self.service
            await self.service.connect()
            try:
                return await self.service.sign_in(phone, code, None)
            except NeedPasswordError:
                self._awaiting_password = True
                raise

        def _ok(me: Any) -> None:
            self._after_signed_in(me)

        def _err(exc: BaseException) -> None:
            if isinstance(exc, NeedPasswordError):
                self._show_dialog(self.dlg_2fa)
                return
            messagebox.showerror(APP_NAME, explain_error(exc), parent=self.dlg_code)
            self._show_dialog(self.dlg_code)

        self._run_async(_job(), on_done=_ok, on_error=_err)

    def _do_sign_in_password(self) -> None:
        password = self.var_password.get()
        self.var_password.set("")

        async def _job() -> Any:
            assert self.service
            await self.service.connect()
            return await self.service.sign_in_password(password)

        def _ok(me: Any) -> None:
            self._after_signed_in(me)

        def _err(exc: BaseException) -> None:
            messagebox.showerror(APP_NAME, explain_error(exc), parent=self.dlg_2fa)
            self._show_dialog(self.dlg_2fa)

        self._run_async(_job(), on_done=_ok, on_error=_err)

    def _after_signed_in(self, me: Any) -> None:
        self._hide_login_dialogs()
        label = self.service.me_label(me) if self.service else "已登录"
        self._set_logged_in_state(True, label)
        self._log(f"登录成功：{label}")
        self._run_async(self._async_reload_dialogs_only(), on_error=self._show_error)

    async def _async_reload_dialogs_only(self) -> None:
        assert self.service
        dialogs = await self.service.list_dialogs()
        self._ui(lambda: self._set_dialogs(dialogs))

    def _switch_account(self) -> None:
        if not messagebox.askyesno(
            APP_NAME,
            "确定切换到其他 Telegram 账号？\n当前会话会退出。已保存的 API 凭据会保留，也可在下一步修改。",
        ):
            return
        self._perform_logout(then_login=True)

    def _logout(self) -> None:
        if not messagebox.askyesno(APP_NAME, "确定退出登录并清除本机会话？\n（api_id / api_hash 会保留）"):
            return
        self._perform_logout(then_login=False)

    def _perform_logout(self, then_login: bool = False) -> None:
        async def _job() -> None:
            if self.service:
                try:
                    if await self.service.is_authorized():
                        await self.service.client.log_out()
                except Exception:
                    pass
                await self.service.disconnect()

        def _done(_: Any = None) -> None:
            api_id, api_hash = self.cfg.get("api_id"), self.cfg.get("api_hash")
            clear_credentials(keep_folder=True)
            update_config(
                api_id=api_id,
                api_hash=api_hash,
                last_save_folder=self.var_folder.get() or self.cfg.get("last_save_folder"),
            )
            self.cfg = load_config()
            self.service = None
            self._account_name = ""
            self._set_logged_in_state(False)
            self._dialogs = []
            self._selected_dialog = None
            self._avatar_bytes.clear()
            self._avatar_gen += 1
            self._refresh_chat_list()
            self._reset_video_pane()
            self._log("已退出登录。" if not then_login else "已退出当前账号，请登录其他账号。")
            self._start_loop_and_connect()
            if then_login:
                self._open_login()

        self._run_async(_job(), on_done=_done, on_error=lambda e: (_done(), self._log(explain_error(e), True)))

    # ----------------------------------------------------------- download
    def _save_folder(self) -> Path:
        path = Path(self.var_folder.get().strip() or str(DEFAULT_DOWNLOADS))
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UserFacingError(explain_error(exc)) from exc
        update_config(last_save_folder=str(path))
        self.cfg = load_config()
        return path

    def _collect_links(self) -> list[str]:
        return split_link_text(self.txt_links.get_text())

    def _on_remove_task(self, key: str) -> None:
        item = self.queue.get(key)
        label = (item.url or item.name) if item else key
        action = self.queue.remove(key)
        self.tasks.remove(key)
        if action == "cancelled":
            self._log(f"已取消并移除：{label}")
        elif action == "removed":
            self._log(f"已移除：{label}")
        if not self.tasks.has_items():
            self.tasks.set_header("就绪", show_stop=self._busy)
        else:
            self._refresh_task_header(done=not self._busy)

    def _mirror_queue(self) -> None:
        selected_keys = set(self.tasks.keys())
        self.tasks.clear()
        for key in self.queue.keys():
            item = self.queue.get(key)
            if item is None or self.queue.cancelled(key):
                continue
            self.tasks.upsert(
                key,
                item.name,
                item.status,
                current=item.current,
                total=item.total,
                speed=item.speed,
                error=item.error,
            )
        del selected_keys
        self._refresh_task_header(done=not self._busy)
        self._relayout_tasks()

    def _seed_tasks(self, rows: list[tuple[str, str, str, str, str, int]], *, paint_now: bool = False) -> None:
        """rows: key, name, status, url, error, total。先写入队列，再刷新界面。"""
        self.queue.clear()
        for key, name, status, url, error, total in rows:
            self.queue.upsert(key, name, status, url=url, error=error, total=total)
        if paint_now:
            self._mirror_queue()
        else:
            self._ui(self._mirror_queue)

    def _paint_task(self, key: str) -> None:
        if self.queue.cancelled(key):
            self.tasks.remove(key)
            return
        item = self.queue.get(key)
        if item is None:
            self.tasks.remove(key)
            return
        self.tasks.upsert(
            key,
            item.name,
            item.status,
            current=item.current,
            total=item.total,
            speed=item.speed,
            error=item.error,
        )

    def _set_task(
        self,
        key: str,
        name: str,
        status: str,
        *,
        url: str = "",
        error: str = "",
        current: int = 0,
        total: int = 0,
        speed: float = 0.0,
    ) -> bool:
        if self.queue.cancelled(key):
            return False
        self.queue.upsert(
            key, name, status, url=url, error=error, current=current, total=total, speed=speed
        )
        self._ui(lambda k=key: self._paint_task(k))
        return True

    def _on_download_link(self) -> None:
        if self._busy:
            messagebox.showinfo(APP_NAME, "当前有任务在进行，请稍候或先停止。")
            return
        links = self._collect_links()
        if not links:
            messagebox.showwarning(APP_NAME, "请先粘贴帖子链接，多条请换行。")
            return
        try:
            folder = self._save_folder()
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc))
            return
        jobs = plan_link_jobs(links)
        if not jobs:
            messagebox.showwarning(APP_NAME, "请先粘贴帖子链接，多条请换行。")
            return
        self._task_gen += 1
        gen = self._task_gen
        if not any(job.parsed for job in jobs):
            self._stop_flag.clear()
            self._seed_tasks(self._link_rows(gen, jobs), paint_now=True)
            self.tasks.set_header(f"完成：成功 0，失败 {len(jobs)}", show_stop=False)
            self._log("这些链接都无法识别，没有开始下载。失败的那一条可以点「删除」去掉。")
            return
        valid = sum(1 for job in jobs if job.parsed)
        label = "链接下载" if valid == 1 and len(jobs) == 1 else f"链接下载（{len(jobs)} 条）"
        self._start_task(self._async_download_links(jobs, folder, gen), label=label)

    def _link_rows(self, gen: int, jobs: list[Any]) -> list[tuple[str, str, str, str, str, int]]:
        rows = []
        for i, job in enumerate(jobs):
            key = f"{gen}-link-{i}"
            if job.error:
                rows.append((key, job.raw, "error", job.raw, job.error, 0))
            else:
                rows.append((key, job.raw, "queued", job.raw, "", 0))
        return rows

    async def _async_download_links(self, jobs: list[Any], folder: Path, gen: int) -> None:
        assert self.service
        ok = fail = 0
        total_links = len(jobs)
        self._seed_tasks(self._link_rows(gen, jobs))

        for li, job in enumerate(jobs):
            key = f"{gen}-link-{li}"
            if job.error or job.parsed is None:
                fail += 1
                if job.error:
                    self._ui_log(job.error, error=True)
                continue
            if self._stop_flag.is_set():
                self._ui_log("已停止。")
                self._ui(self._mark_remaining_stopped)
                break
            if self.queue.cancelled(key):
                continue
            parsed = job.parsed
            self._set_task(key, parsed.raw, "running", url=parsed.raw)
            self._ui(self._refresh_task_header)
            self._ui_log(f"解析链接（{li + 1}/{total_links}）：{parsed.raw}")
            try:
                entity, message = await self.service.resolve_link(parsed)
                if self.queue.cancelled(key):
                    continue
                title = self.service.entity_title(entity)
                album = await self.service.album_messages(entity, message)
                videos = [(m, message_video_kind(m)) for m in album if message_video_kind(m)]
                if not videos:
                    raise UserFacingError(
                        f"链接 {parsed.raw} · 消息 #{message.id}（{title}，"
                        f"{format_datetime(getattr(message, 'date', None))}）"
                        f"不含视频 / 圆形视频 / 动态 GIF。"
                    )
                replacements = [
                    (
                        f"{gen}-link-{li}-msg-{msg.id}",
                        task_display_name(msg, title),
                        preferred_media_size(msg, quality_pref_value(self.var_quality.get())) or 0,
                        parsed.raw,
                    )
                    for msg, _kind in videos
                ]
                # 队列立刻替换，避免用户点删除时界面还停在旧的占位行。
                self.queue.replace(key, replacements)
                self._ui(lambda old=key, items=replacements: self._paint_replaced_link(old, items))
                self._ui_log(f"对话：{title} · 消息 {message.id} · 发现 {len(videos)} 个视频媒体")
                for idx, (msg, kind) in enumerate(videos, 1):
                    child = f"{gen}-link-{li}-msg-{msg.id}"
                    if self._stop_flag.is_set():
                        self._ui_log("已停止。")
                        self._ui(self._mark_remaining_stopped)
                        return
                    if self.queue.cancelled(key) or self.queue.cancelled(child):
                        continue
                    name = task_display_name(msg, title)
                    try:
                        path = await self._download_one(
                            entity,
                            msg,
                            folder,
                            title,
                            kind,
                            idx,
                            len(videos),
                            task_key=child,
                            display_name=name,
                            source_url=parsed.raw,
                        )
                        if self.queue.cancelled(child):
                            continue
                        ok += 1
                        size = path.stat().st_size if path.exists() else (message_file_size(msg) or 0)
                        self._set_task(child, name, "done", url=parsed.raw, current=size, total=size)
                        self._ui_log(f"✓ 已保存：{path.name}（{parsed.raw}）")
                    except Exception as exc:
                        if self.queue.cancelled(child):
                            continue
                        if is_user_stop(exc):
                            self._ui_log("已停止。")
                            self._ui(self._mark_remaining_stopped)
                            return
                        fail += 1
                        err = link_failure_text(parsed.raw, exc, message_id=getattr(msg, "id", None), title=title)
                        self._set_task(child, name, "error", url=parsed.raw, error=err)
                        self._ui_log(err, error=True)
            except Exception as exc:
                if self.queue.cancelled(key):
                    continue
                if is_user_stop(exc):
                    self._ui_log("已停止。")
                    self._ui(self._mark_remaining_stopped)
                    break
                fail += 1
                err = link_failure_text(job.raw, exc)
                self._set_task(key, job.raw, "error", url=job.raw, error=err)
                self._ui_log(err, error=True)
        self._ui(lambda: self._refresh_task_header(done=True, ok=ok, fail=fail))
        self._ui_log(f"完成：成功 {ok}，失败 {fail}。保存目录：{folder}")

    def _paint_replaced_link(self, old: str, items: list[tuple[str, str, int, str]]) -> None:
        self.tasks.remove(old)
        if self.queue.cancelled(old):
            return
        for key, name, total, _url in items:
            if self.queue.cancelled(key):
                continue
            current = self.queue.get(key)
            status = current.status if current else "queued"
            shown_total = current.total if current else total
            self.tasks.upsert(
                key,
                current.name if current else name,
                status,
                total=shown_total,
                current=current.current if current else 0,
                error=current.error if current else "",
            )

    def _start_load_videos(self, item: DialogItem, reset: bool = True) -> None:
        if not self.service:
            messagebox.showwarning(APP_NAME, "请先登录。")
            return
        self._video_chat = item
        if reset:
            self._video_items = []
            self._video_scan = VideoScanState()
            self._video_notices = []
            self._video_has_more = True
            self.video_list.clear()
            self._video_gen += 1
        gen = self._video_gen
        self._video_loading = True
        self._reveal_video_chrome(item.title)
        if reset:
            self._show_video_empty("正在加载视频…")
        self._sync_chat_actions()
        state = self._video_scan
        self._run_async(
            self._async_list_videos(item, gen, state),
            on_error=lambda exc, g=gen: self._on_videos_error(g, exc),
        )

    async def _async_list_videos(self, item: DialogItem, gen: int, state: VideoScanState) -> None:
        assert self.service
        page = await self.service.list_chat_videos(item.entity, limit=30, state=state)

        loop = asyncio.get_running_loop()
        applied = loop.create_future()

        def _apply() -> None:
            try:
                if gen == self._video_gen:
                    self._apply_video_page(item, gen, page)
            finally:
                if not applied.done():
                    loop.call_soon_threadsafe(applied.set_result, None)

        self._ui(_apply)
        await applied
        if gen != self._video_gen:
            return
        # 压缩/缓存图只作占位；这里再拉最大静帧封面。切换对话后由 generation 丢弃过期结果。
        for v in page.items:
            if gen != self._video_gen:
                return
            if not v.downloadable:
                continue
            try:
                data = await self.service.download_video_thumb(v)
            except Exception:
                continue
            if not data or gen != self._video_gen:
                continue
            self._ui(lambda mid=v.message_id, b=data, g=gen: self._apply_video_thumb(g, mid, b))

    def _apply_video_page(self, item: DialogItem, gen: int, page: Any) -> None:
        if gen != self._video_gen or self._video_chat is None or self._video_chat.id != item.id:
            return
        videos = list(page.items)
        self._video_loading = False
        self._video_scan = page.state
        self._video_notices = list(page.notices)
        for note in page.notices:
            self._log(note)
        if not self._video_items:
            self._video_items = list(videos)
            if not videos:
                self._show_video_empty(
                    "\n".join(page.notices[:4]) if page.notices else "这个对话里没有可下载的视频"
                )
            else:
                self.video_list.set_items(self._video_items)
                self._show_video_list()
        else:
            existing = {v.message_id for v in self._video_items}
            added = [v for v in videos if v.message_id not in existing]
            if added and self._video_items and any(v.message_id > self._video_items[-1].message_id for v in added):
                self._video_items.extend(added)
                self._video_items.sort(key=lambda v: v.message_id, reverse=True)
                self.video_list.set_items(
                    self._video_items,
                    selected=self.video_list.selected_ids,
                    reset_scroll=False,
                )
            else:
                self._video_items.extend(added)
                if added:
                    self.video_list.append_items(added)
            if self._video_items:
                self._show_video_list()
        self._video_has_more = bool(page.has_more)
        self._sync_quality_options()
        self.video_list.refresh_quality(quality_pref_value(self.var_quality.get()))
        self._sync_chat_actions()

    def _apply_video_thumb(self, gen: int, message_id: int, data: bytes) -> None:
        if gen != self._video_gen:
            return
        for item in self._video_items:
            if item.message_id == message_id:
                item.thumb_bytes = data
                break
        self.video_list.update_thumb(message_id, data)

    def _on_videos_error(self, gen: int, exc: BaseException) -> None:
        if gen != self._video_gen:
            return
        self._video_loading = False
        self._sync_chat_actions()
        msg = explain_error(exc)
        self._log(msg, error=True)
        if not self._video_items:
            self._show_video_empty(msg)
        else:
            messagebox.showerror(APP_NAME, msg)

    def _on_load_more(self) -> None:
        if self._video_loading or not self._video_has_more or not self._video_chat:
            return
        if not self._logged_in:
            messagebox.showwarning(APP_NAME, "请先登录。")
            return
        self._start_load_videos(self._video_chat, reset=False)

    def _on_download_selected(self) -> None:
        if self._busy:
            messagebox.showinfo(APP_NAME, "当前有任务在进行，请稍候或先停止。")
            return
        item = self._video_chat or self._selected_dialog
        if not item:
            messagebox.showwarning(APP_NAME, "请先选择一个对话。")
            return
        chosen_ids = self.video_list.selected_ids
        messages = [v.message for v in self._video_items if v.message_id in chosen_ids and v.message is not None]
        # 保持当前列表顺序（新到旧）
        if not messages:
            messagebox.showwarning(APP_NAME, "请先勾选要下载的视频。")
            return
        try:
            folder = self._save_folder()
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc))
            return
        self._start_task(self._async_download_selected(item, messages, folder), label="对话下载")
        self._fly_task_to_links(len(messages))

    async def _async_download_selected(self, item: DialogItem, messages: list[Any], folder: Path) -> None:
        assert self.service
        entity = await self.service.resolve_entity(item.entity)
        title = item.title or self.service.entity_title(entity)
        await self._download_message_list(entity, title, messages, folder)

    async def _download_message_list(
        self,
        entity: Any,
        title: str,
        messages: list[Any],
        folder: Path,
    ) -> None:
        total = len(messages)
        self._ui_log(f"准备下载 {total} 个视频媒体 → {folder}")
        self._task_gen += 1
        gen = self._task_gen
        rows = []
        for msg in messages:
            key = f"{gen}-msg-{msg.id}"
            name = task_display_name(msg, title)
            rows.append((key, name, "queued", "", "", preferred_media_size(msg, self._quality_pref()) or 0))
        self._seed_tasks(rows)
        ok = fail = 0
        for idx, msg in enumerate(messages, 1):
            key = f"{gen}-msg-{msg.id}"
            if self._stop_flag.is_set():
                self._ui_log("已停止。")
                self._ui(self._mark_remaining_stopped)
                break
            if self.queue.cancelled(key):
                continue
            kind = message_video_kind(msg)
            name = task_display_name(msg, title)
            try:
                path = await self._download_one(
                    entity, msg, folder, title, kind, idx, total, task_key=key, display_name=name
                )
                if self.queue.cancelled(key):
                    continue
                ok += 1
                size = path.stat().st_size if path.exists() else (message_file_size(msg) or 0)
                self._set_task(key, name, "done", current=size, total=size)
                self._ui_log(f"✓ [{idx}/{total}] {path.name}")
            except Exception as exc:
                if self.queue.cancelled(key):
                    continue
                if is_user_stop(exc):
                    self._ui_log("已停止。")
                    self._ui(self._mark_remaining_stopped)
                    break
                fail += 1
                err = link_failure_text("", exc, message_id=getattr(msg, "id", None), title=title)
                self._set_task(key, name, "error", error=err)
                self._ui_log(err, error=True)
        self._ui(lambda: self._refresh_task_header(done=True, ok=ok, fail=fail))
        self._ui_log(f"完成：成功 {ok}，失败 {fail}。")

    async def _download_one(
        self,
        entity: Any,
        msg: Any,
        folder: Path,
        title: str,
        kind: Optional[str],
        idx: int,
        total: int,
        task_key: str = "",
        display_name: str = "",
        source_url: str = "",
    ) -> Path:
        assert self.service
        name = display_name or task_display_name(msg, title)
        known = preferred_media_size(msg, self._quality_pref()) or 0
        started = time.monotonic()
        if task_key:
            self._set_task(task_key, name, "running", url=source_url, current=0, total=known)
            self._ui(self._refresh_task_header)

        def progress(cur: int, tot: int) -> None:
            elapsed = max(time.monotonic() - started, 0.001)
            speed = cur / elapsed

            def _upd() -> None:
                if task_key and not self.queue.cancelled(task_key):
                    self.queue.upsert(
                        task_key, name, "running", url=source_url, current=cur, total=tot or known, speed=speed
                    )
                    self.tasks.upsert(task_key, name, "running", current=cur, total=tot or known, speed=speed)
                    self._refresh_task_header()

            self._ui(_upd)

        def _cancelled() -> bool:
            return self._stop_flag.is_set() or bool(task_key and self.queue.cancelled(task_key))

        while True:
            if _cancelled():
                raise UserFacingError("已停止。")
            try:
                return await self.service.download_message_video(
                    entity,
                    msg,
                    folder,
                    progress=progress,
                    chat_title=title,
                    should_cancel=_cancelled,
                    quality=quality_pref_value(self.var_quality.get()),
                )
            except FloodWaitError as exc:
                seconds = int(getattr(exc, "seconds", 0) or 0)
                self._ui_log(f"请求过于频繁，自动等待 {seconds} 秒后再继续…（可点停止）")
                await self._sleep_interruptible(seconds, should_cancel=_cancelled)

    async def _sleep_interruptible(self, seconds: int, should_cancel: Optional[Callable[[], bool]] = None) -> None:
        left = seconds
        while left > 0 and not self._stop_flag.is_set():
            if should_cancel and should_cancel():
                return
            step = min(1, left)
            await asyncio.sleep(step)
            left -= step
            self._ui(lambda l=left: self.tasks.set_header("限流等待中…", f"剩余 {l} 秒", show_stop=True))

    def _mark_remaining_stopped(self) -> None:
        self.queue.mark_stopped()
        for key in list(self.queue.keys()):
            item = self.queue.get(key)
            if item is None or self.queue.cancelled(key):
                continue
            self.tasks.upsert(
                key,
                item.name,
                item.status,
                current=item.current,
                total=item.total,
                speed=0.0,
                error=item.error,
            )
        self._refresh_task_header(done=True)

    def _refresh_task_header(self, done: bool = False, ok: int = 0, fail: int = 0) -> None:
        if done:
            show_open = bool(ok or self.tasks.has_items())
            if fail or ok:
                self.tasks.set_header(f"完成：成功 {ok}，失败 {fail}", show_stop=False, show_open=show_open)
            else:
                title = "已停止" if self._stop_flag.is_set() else "就绪"
                self.tasks.set_header(title, show_stop=False, show_open=show_open and self._stop_flag.is_set())
            return
        _done, running, total = self.tasks.counts()
        if self._busy and total:
            idx = min(_done + (1 if running else 0), total) or total
            speed = self.tasks.current_speed()
            meta = f"·  {format_speed(speed)}" if speed else ""
            self.tasks.set_header(f"下载中 {idx}/{total}", meta, show_stop=True)
        elif total:
            self.tasks.set_header("就绪", show_stop=False)
        else:
            self.tasks.set_header("就绪", show_stop=self._busy)

    # -------------------------------------------------------------- tasks
    def _start_task(self, coro: Any, label: str) -> None:
        if not self.service:
            messagebox.showwarning(APP_NAME, "尚未连接，请先完成登录。")
            return
        self._stop_flag.clear()
        self._set_busy_ui(True)
        self.tasks.clear()
        self.tasks.set_header("准备中…", show_stop=True)
        self._relayout_tasks()
        self._log(f"—— 开始：{label} ——")

        def _done(_: Any = None) -> None:
            self._set_busy_ui(False)
            if not self.tasks.lbl_title.cget("text").startswith("完成") and not self.tasks.lbl_title.cget("text").startswith("已停止"):
                self._refresh_task_header(done=True)

        def _err(exc: BaseException) -> None:
            self._set_busy_ui(False)
            msg = explain_error(exc)
            self._log(msg, error=True)
            self.tasks.set_header("失败", show_stop=False)
            messagebox.showerror(APP_NAME, msg)

        self._run_async(coro, on_done=_done, on_error=_err)

    def _request_stop(self) -> None:
        self._stop_flag.set()
        self._log("正在停止…当前文件会尽快中断。未下完的临时文件会被删除（Telegram 暂不支持可靠续传）。")

    # --------------------------------------------------------------- async
    def _run_async(
        self,
        coro: Any,
        on_done: Optional[Any] = None,
        on_error: Optional[Any] = None,
    ) -> None:
        self._ensure_loop()
        assert self.loop

        async def _wrapper() -> None:
            try:
                result = await coro
            except Exception as exc:
                if on_error:
                    self._ui(lambda: on_error(exc))
                else:
                    self._ui(lambda: self._show_error(exc))
                return
            if on_done:
                self._ui(lambda: on_done(result))

        asyncio.run_coroutine_threadsafe(_wrapper(), self.loop)

    def _ui(self, fn: Any) -> None:
        self._ui_q.put(fn)

    def _ui_log(self, text: str, error: bool = False) -> None:
        self._ui(lambda: self._log(text, error=error))

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(80, self._drain_ui_queue)

    def _log(self, text: str, error: bool = False) -> None:
        self.txt_log.configure(state="normal")
        prefix = "!" if error else "·"
        self.txt_log.insert("end", f"{prefix} {text}\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def _show_error(self, exc: BaseException) -> None:
        self._log(explain_error(exc), error=True)

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(initialdir=self.var_folder.get() or str(DEFAULT_DOWNLOADS))
        if path:
            self.var_folder.set(path)
            update_config(last_save_folder=path)

    def _open_folder(self) -> None:
        try:
            folder = self._save_folder()
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc))
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, explain_error(exc))

    def _reload_dialogs(self) -> None:
        if not self.service:
            messagebox.showwarning(APP_NAME, "请先登录。")
            return
        self._log("刷新对话列表…")
        self._run_async(self._async_reload_dialogs_only(), on_error=self._show_error)

    def _on_window_map(self, event: tk.Event) -> None:
        if event.widget is self:
            apply_window_icon(self, self._icon_photos)

    def _on_window_unmap(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if getattr(self, "_exiting", False) or getattr(self, "_hiding_to_tray", False):
            return
        try:
            if self.state() == "iconic":
                self._hide_to_tray()
        except Exception:
            pass

    def _start_tray(self) -> None:
        if getattr(self, "_tray", None) is not None:
            return
        if sys.platform != "win32":
            return
        try:
            import pystray
        except ImportError:
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("打开主窗口", self._tray_show, default=True),
                pystray.MenuItem("退出", self._tray_exit),
            )
            icon = pystray.Icon("Xlattice0.TGVideoDownloader", _tray_image(), APP_NAME, menu)
            icon.run_detached()
            self._tray = icon
        except Exception:
            self._tray = None

    def _tray_show(self, _icon=None, _item=None) -> None:
        try:
            self.after(0, self._restore_from_tray)
        except Exception:
            pass

    def _tray_exit(self, _icon=None, _item=None) -> None:
        try:
            self.after(0, self._quit_app)
        except Exception:
            pass

    def _restore_from_tray(self) -> None:
        if getattr(self, "_exiting", False):
            return
        try:
            self.deiconify()
            try:
                self.state("normal")
            except Exception:
                pass
            self.lift()
            self.focus_force()
            apply_window_icon(self, self._icon_photos)
        except Exception:
            pass

    def _hide_to_tray(self) -> None:
        if getattr(self, "_exiting", False):
            return
        if getattr(self, "_tray", None) is None:
            self._start_tray()
        if getattr(self, "_tray", None) is None:
            self._quit_app()
            return
        self._hiding_to_tray = True
        try:
            try:
                self._hide_login_dialogs()
            except Exception:
                pass
            try:
                self.dlg_proxy.hide()
                self.dlg_about.hide()
            except Exception:
                pass
            self.withdraw()
        finally:
            self._hiding_to_tray = False
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            try:
                self._tray.notify("已最小化到托盘，点击图标可重新打开。", APP_NAME)
            except Exception:
                pass

    def _stop_tray(self) -> None:
        tray = getattr(self, "_tray", None)
        self._tray = None
        if tray is None:
            return
        try:
            tray.stop()
        except Exception:
            pass

    def _on_close(self) -> None:
        # 点窗口关闭：缩到托盘，下载任务继续跑
        self._hide_to_tray()

    def _quit_app(self) -> None:
        if getattr(self, "_exiting", False):
            return
        if self._busy:
            try:
                self.deiconify()
                self.lift()
            except Exception:
                pass
            if not messagebox.askyesno(APP_NAME, "还有下载任务在进行。确定要退出吗？未下完的文件不会保留。"):
                return
        self._exiting = True
        self._stop_tray()
        self._stop_flag.set()
        # 先立刻隐去窗口，并用看门狗保证进程一定退出，避免安装包仍检测到残留进程
        try:
            self.withdraw()
        except Exception:
            pass
        if getattr(self, "_fly_after", None) or getattr(self, "_fly_chip", None):
            try:
                self._cancel_task_fly()
            except Exception:
                pass

        def _force_exit() -> None:
            time.sleep(1.2)
            os._exit(0)

        threading.Thread(target=_force_exit, name="force-exit", daemon=True).start()
        try:
            self._hide_login_dialogs()
        except Exception:
            pass
        try:
            self.dlg_proxy.hide()
            self.dlg_about.hide()
        except Exception:
            pass
        # 先把会话写盘再断开，避免强制退出时丢失登录状态
        if self.service and self.loop and self.loop.is_running():
            done = threading.Event()

            async def _flush_and_disconnect() -> None:
                try:
                    self.service.persist_session()
                    await self.service.disconnect()
                except Exception:
                    pass
                finally:
                    done.set()

            try:
                asyncio.run_coroutine_threadsafe(_flush_and_disconnect(), self.loop)
                done.wait(timeout=0.9)
            except Exception:
                pass
        elif self.service:
            try:
                self.service.persist_session()
            except Exception:
                pass
        if self.loop:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        set_app_user_model_id()
    app = App()
    try:
        app.mainloop()
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
