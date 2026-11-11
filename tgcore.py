# -*- coding: utf-8 -*-
"""Telegram 视频下载核心逻辑（官方用户 API / Telethon）。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    ChannelInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatIdInvalidError,
    FloodWaitError,
    MessageIdInvalidError,
    PasswordHashInvalidError,
    PeerIdInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    RPCError,
    SessionExpiredError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.messages import GetDiscussionMessageRequest
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeVideo,
    InputMessagesFilterDocument,
    InputMessagesFilterGif,
    InputMessagesFilterPhotos,
    InputMessagesFilterRoundVideo,
    InputMessagesFilterUrl,
    InputMessagesFilterVideo,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    MessageMediaDocument,
    MessageMediaPaidMedia,
    MessageMediaPhoto,
    MessageMediaUnsupported,
    MessageMediaVideoStream,
    MessageMediaWebPage,
    PhotoCachedSize,
    PhotoPathSize,
    PhotoSize,
    PhotoSizeProgressive,
    PhotoStrippedSize,
    VideoSize,
)
from telethon.utils import stripped_photo_to_jpg

APP_NAME = "TG视频下载"
APP_VERSION = "1.4.0"

# 第一次启动（含从 1.3.x 升级、配置里还没有这项）使用经典浅色侧栏。
UI_MODE_CLASSIC = "classic"
UI_MODE_PROFESSIONAL = "professional"
UI_MODE_CLASSIC_TITLE = "经典"
UI_MODE_CLASSIC_DESC = "浅色，侧栏链接/对话"
UI_MODE_PRO_TITLE = "专业"
UI_MODE_PRO_DESC = "深色，首页任务列表"

TASK_FILTER_ALL = "全部"
TASK_FILTER_ACTIVE = "下载中"
TASK_FILTER_DONE = "已完成"
TASK_FILTERS = (TASK_FILTER_ALL, TASK_FILTER_ACTIVE, TASK_FILTER_DONE)

# 任务行副文案。持久化的中断说明仍是 INTERRUPTED_TASK_HINT，界面上收成这一句。
STOPPED_ROW_SUBTITLE = "上次未完成"
FLOOD_ROW_SUBTITLE = "频率限制，可停止"
DELETE_DIALOG_TITLE = "删除任务"
APP_DIR_NAME = "TGVideoDownloader"
APP_USER_MODEL_ID = "Xlattice0.TGVideoDownloader"
GITHUB_URL = "https://github.com/Xlattice0"
GITHUB_HANDLE = "Xlattice0"
X_URL = "https://x.com/XxxLllattice"
X_HANDLE = "XxxLllattice"


def _dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def app_dir() -> Path:
    """程序数据根目录。

    - 源码运行：项目根目录
    - 便携版（exe 旁有 portable.dat）：exe 所在目录
    - 安装版：%LOCALAPPDATA%\\TGVideoDownloader，避免写入 Program Files 失败

    仅凭 exe 旁存在 data/ 不能当作便携版，否则安装版会话可能被写到另一处，
    重启后看起来像掉登录。
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "portable.dat").is_file():
        return exe_dir

    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_DIR_NAME
    if _dir_is_writable(local):
        return local
    if _dir_is_writable(exe_dir):
        return exe_dir
    local.mkdir(parents=True, exist_ok=True)
    return local


DATA_DIR = app_dir() / "data"
CONFIG_PATH = DATA_DIR / "config.json"
SESSION_PATH = DATA_DIR / "tg_session"
AVATAR_DIR = DATA_DIR / "avatars"
QUEUE_PATH = DATA_DIR / "download_queue.json"
DOWNLOAD_INDEX_PATH = DATA_DIR / "downloads_index.json"
QUALITY_HIGHEST = "highest"


def default_downloads() -> Path:
    """安装版默认放到用户「下载」目录，避免藏在隐藏的 AppData 里。"""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for cand in (home / "Downloads" / "TG视频下载", Path.home() / "Downloads" / "TG视频下载"):
        if cand.parent.exists():
            return cand
    return app_dir() / "downloads"


DEFAULT_DOWNLOADS = default_downloads()

TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}

# Windows 文件名非法字符与保留名
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

ProgressCb = Callable[[int, int], None]
StatusCb = Callable[[str], None]


class UserFacingError(Exception):
    """已是中文、可直接展示给用户的错误。"""


class NeedPasswordError(Exception):
    """登录需要两步验证密码。"""


@dataclass
class ParsedLink:
    entity: Any  # 用户名 str 或频道 int（-100...）
    message_id: int
    comment_id: Optional[int] = None
    raw: str = ""


@dataclass
class DialogItem:
    entity: Any
    title: str
    kind: str
    id: int
    photo_id: Optional[int] = None


@dataclass
class VideoQuality:
    """A real video file attached to a message (main document or alt_documents)."""
    label: str
    width: int
    height: int
    size: Optional[int]
    document: Any
    key: str = ""  # "highest" or "1080"


@dataclass
class VideoItem:
    message_id: int
    date: Optional[datetime]
    duration: Optional[int]
    size: Optional[int]
    filename: Optional[str]
    caption: str
    kind: str
    thumb_bytes: Optional[bytes] = None
    message: Any = None
    width: int = 0
    height: int = 0
    qualities: list[VideoQuality] = field(default_factory=list)
    note: str = ""
    downloadable: bool = True
    document_id: Optional[int] = None
    downloaded: bool = False


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> None:
    ensure_data_dir()
    # 不把会话字节写入配置
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_config(**kwargs: Any) -> dict:
    cfg = load_config()
    cfg.update(kwargs)
    save_config(cfg)
    return cfg


def normalize_ui_mode(value: Any) -> str:
    """未知或空值都回到经典，避免老用户一打开就被换成专业界面。"""
    text = str(value or "").strip().lower()
    if text in {UI_MODE_PROFESSIONAL, "pro", "专业"}:
        return UI_MODE_PROFESSIONAL
    return UI_MODE_CLASSIC


def ui_mode_from_config(cfg: Optional[dict] = None) -> str:
    if not isinstance(cfg, dict):
        return UI_MODE_CLASSIC
    return normalize_ui_mode(cfg.get("ui_mode"))


def config_with_ui_mode(cfg: Optional[dict], mode: Any) -> dict:
    """只改界面风格。登录、代理、保存路径和队列文件都不在这里动。"""
    out = dict(cfg or {})
    out["ui_mode"] = normalize_ui_mode(mode)
    return out


def appearance_choices() -> list[dict[str, str]]:
    return [
        {
            "mode": UI_MODE_CLASSIC,
            "title": UI_MODE_CLASSIC_TITLE,
            "description": UI_MODE_CLASSIC_DESC,
        },
        {
            "mode": UI_MODE_PROFESSIONAL,
            "title": UI_MODE_PRO_TITLE,
            "description": UI_MODE_PRO_DESC,
        },
    ]


def delete_task_message(name: str) -> str:
    label = (name or "").strip() or "该任务"
    return f"确定删除「{label}」？"



CONNECT_HINT = (
    "连接 Telegram 失败或超时。国内网络通常需要先打开能访问 Telegram 的 VPN，"
    "或在软件里填写本地代理（常见：Clash Socks 127.0.0.1:7891 或混合端口 7890，v2rayN 127.0.0.1:10808）。"
)


def proxy_from_config(cfg: Optional[dict] = None) -> Optional[tuple]:
    """把配置转成 Telethon / PySocks 代理元组；未启用则返回 None。"""
    cfg = cfg if cfg is not None else load_config()
    kind = str(cfg.get("proxy_type") or "none").strip().lower()
    if kind in ("", "none", "off", "direct"):
        return None
    host = str(cfg.get("proxy_host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(str(cfg.get("proxy_port") or "0").strip() or 0)
    except ValueError:
        port = 0
    if port <= 0 or port > 65535:
        raise UserFacingError("代理端口无效，请填写 1–65535 之间的数字。")
    try:
        import socks  # type: ignore
    except ImportError as exc:
        raise UserFacingError("缺少代理组件，请安装完整版本后重试。") from exc
    if kind in ("socks5", "socks"):
        ptype = socks.SOCKS5
    elif kind in ("http", "https"):
        ptype = socks.HTTP
    else:
        raise UserFacingError("代理类型只支持 SOCKS5 或 HTTP。")
    user = str(cfg.get("proxy_user") or "").strip()
    password = str(cfg.get("proxy_pass") or "")
    if user:
        return (ptype, host, port, True, user, password)
    return (ptype, host, port)


def format_proxy_label(cfg: Optional[dict] = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    kind = str(cfg.get("proxy_type") or "none").strip().lower()
    if kind in ("", "none", "off", "direct"):
        return "直连"
    host = str(cfg.get("proxy_host") or "127.0.0.1").strip() or "127.0.0.1"
    port = str(cfg.get("proxy_port") or "").strip()
    return f"{kind.upper()} {host}:{port}"



def format_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN
        return "—"
    sec = int(seconds)
    if sec < 60:
        return f"{sec} 秒"
    minutes, sec = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def format_duration_short(seconds: Optional[float]) -> str:
    """封面角标时长，如 04:21；超过一小时为 1:05:03。未知为 —。"""
    if seconds is None:
        return "—"
    try:
        if seconds < 0 or seconds != seconds:  # NaN
            return "—"
        sec = int(seconds)
    except (TypeError, ValueError):
        return "—"
    hours, rem = divmod(sec, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def format_speed(bytes_per_sec: float) -> str:
    return f"{format_bytes(int(max(bytes_per_sec, 0)))}/s"


COMMON_PROXY_CANDIDATES: list[tuple[str, int, str]] = [
    ("socks5", 7891, "Clash / Clash Party Socks 端口"),
    ("socks5", 7890, "Clash 混合端口"),
    ("http", 7890, "Clash 混合端口（HTTP）"),
    ("socks5", 10808, "v2rayN Socks"),
    ("http", 10809, "v2rayN HTTP"),
    ("http", 7892, "Clash HTTP 端口"),
    ("socks5", 1080, "常见 Socks 端口"),
]


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def read_system_proxy() -> Optional[dict]:
    """读取 Windows 系统代理（Internet Settings）。失败则返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            key.Close()
    except OSError:
        return None
    if not enable or not server:
        return None
    server = str(server).strip()
    # 可能是 http=127.0.0.1:7890;https=... 或 127.0.0.1:7890
    hostport = server
    if "=" in server:
        for part in server.split(";"):
            if part.lower().startswith("socks="):
                hostport = part.split("=", 1)[1]
                kind = "socks5"
                break
            if part.lower().startswith(("http=", "https=")):
                hostport = part.split("=", 1)[1]
                kind = "http"
                break
        else:
            return None
    else:
        kind = "http"
    if "://" in hostport:
        hostport = hostport.split("://", 1)[1]
    if ":" not in hostport:
        return None
    host, _, port_s = hostport.rpartition(":")
    host = host.strip().strip("[]") or "127.0.0.1"
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not (1 <= port <= 65535):
        return None
    return {"type": kind, "host": host, "port": port, "label": "Windows 系统代理"}


def detect_local_proxies() -> list[dict]:
    found: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    sys_p = read_system_proxy()
    if sys_p:
        key = (sys_p["type"], sys_p["host"], int(sys_p["port"]))
        seen.add(key)
        found.append(sys_p)
    for kind, port, label in COMMON_PROXY_CANDIDATES:
        if not _port_open("127.0.0.1", port):
            continue
        key = (kind, "127.0.0.1", port)
        if key in seen:
            continue
        seen.add(key)
        found.append({"type": kind, "host": "127.0.0.1", "port": port, "label": label})
    return found


def pick_best_proxy(candidates: Optional[list[dict]] = None) -> Optional[dict]:
    items = candidates if candidates is not None else detect_local_proxies()
    if not items:
        return None
    def score(item: dict) -> tuple:
        kind = item.get("type")
        host = item.get("host")
        port = int(item.get("port") or 0)
        # prefer local socks5, Clash Party 7891 first
        is_local = 1 if host in ("127.0.0.1", "localhost") else 0
        is_socks = 1 if kind == "socks5" else 0
        port_rank = {7891: 3, 7890: 2, 10808: 2, 1080: 1}.get(port, 0)
        return (is_local, is_socks, port_rank)
    return max(items, key=score)


def test_proxy_connection(
    kind: str,
    host: str,
    port: int,
    user: str = "",
    password: str = "",
    timeout: float = 8,
) -> str:
    """探测能否连上 Telegram 数据中心。成功返回「已连通 · 延迟 xx ms」，失败抛 UserFacingError。"""
    kind = (kind or "").strip().lower()
    targets = [("149.154.167.51", 443), ("149.154.167.91", 443)]
    last_err: Optional[BaseException] = None

    def _latency_ms(t0: float) -> int:
        return max(1, int((time.perf_counter() - t0) * 1000))

    if kind in ("", "none", "off", "direct"):
        for th, tp in targets:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.settimeout(timeout)
                t0 = time.perf_counter()
                s.connect((th, tp))
                return f"已连通 · 延迟 {_latency_ms(t0)} ms"
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            finally:
                try:
                    s.close()
                except OSError:
                    pass
        raise UserFacingError(
            "测试失败：无法直连 Telegram。"
            + (f"（{type(last_err).__name__}）" if last_err else "")
            + " 请检查网络，或改用 SOCKS5 / HTTP 代理。"
        )

    try:
        import socks  # type: ignore
    except ImportError as exc:
        raise UserFacingError("缺少代理组件，请安装完整版本后重试。") from exc
    if kind in ("socks5", "socks"):
        ptype = socks.SOCKS5
    elif kind in ("http", "https"):
        ptype = socks.HTTP
    else:
        raise UserFacingError("代理类型只支持 SOCKS5 或 HTTP。")
    for th, tp in targets:
        s = socks.socksocket()
        try:
            if user:
                s.set_proxy(ptype, host, int(port), rdns=True, username=user, password=password)
            else:
                s.set_proxy(ptype, host, int(port))
            s.settimeout(timeout)
            t0 = time.perf_counter()
            s.connect((th, tp))
            return f"已连通 · 延迟 {_latency_ms(t0)} ms"
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        finally:
            try:
                s.close()
            except OSError:
                pass
    raise UserFacingError(
        f"测试失败：无法通过 {kind.upper()} {host}:{port} 连接 Telegram。"
        + (f"（{type(last_err).__name__}）" if last_err else "")
        + " 请确认代理软件已开启，且端口与类型一致。"
    )


def has_api_credentials(cfg: Optional[dict] = None) -> bool:
    cfg = cfg if cfg is not None else load_config()
    api_id = str(cfg.get("api_id", "")).strip()
    api_hash = str(cfg.get("api_hash", "")).strip()
    return bool(api_id and api_hash and api_id.isdigit())


def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = (name or "").strip()
    name = _WIN_INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip(" ._")
    if not name:
        name = "video"
    stem = name.split(".")[0].upper()
    if stem in _WIN_RESERVED:
        name = "_" + name
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "video"


def format_bytes(n: Optional[int]) -> str:
    if n is None or n < 0:
        return "?"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_datetime(dt: Optional[datetime]) -> str:
    if not dt:
        return "未知时间"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # 展示为本地时间（Windows 上即用户本地时区）
    local = dt.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def format_date_short(dt: Optional[datetime]) -> str:
    """对话页辅助行日期：今天 HH:MM、昨天 HH:MM，更早为 M月D日。"""
    if not dt:
        return "未知时间"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    today = datetime.now(local.tzinfo).date()
    day = local.date()
    if day == today:
        return f"今天 {local.strftime('%H:%M')}"
    if day == today - timedelta(days=1):
        return f"昨天 {local.strftime('%H:%M')}"
    return f"{local.month}月{local.day}日"


def file_date_tag(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y%m%d_%H%M")


_VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg",
    ".wmv", ".flv", ".ts", ".m2ts", ".3gp", ".ogv",
}

# 每页最多顺着历史看这么多条，避免一次请求扫完整条频道。
# 没凑满一页也会停在这里，并允许「加载更多」从断点继续，不丢已看到的消息。
VIDEO_HISTORY_BUDGET = 800
VIDEO_FILTER_BUDGET = 400


@dataclass
class VideoClass:
    """一条消息里的视频，或看起来是视频但 Telegram 不给文件的情况。"""

    kind: str
    downloadable: bool = True
    note: str = ""


def message_video_kind(msg: Any) -> Optional[str]:
    """返回 video / video_note / gif / webpage / live / stream；不含视频则 None。"""
    info = classify_video_message(msg)
    return info.kind if info else None


def kind_label(kind: Optional[str]) -> str:
    return {
        "video": "视频",
        "video_note": "圆形视频",
        "gif": "动态 GIF / MPEG",
        "webpage": "网页视频",
        "live": "实况照片",
        "stream": "直播",
    }.get(kind or "", "视频")


def media_duration(msg: Any) -> Optional[int]:
    docs = list(iter_message_documents(msg))
    primary = getattr(msg, "document", None)
    if primary is not None:
        docs.append(primary)
    for doc in docs:
        for attr in getattr(doc, "attributes", []) or []:
            if isinstance(attr, DocumentAttributeVideo):
                dur = getattr(attr, "duration", None)
                if dur is not None:
                    try:
                        return int(dur)
                    except (TypeError, ValueError):
                        pass
    web = getattr(getattr(msg, "media", None), "webpage", None)
    dur = getattr(web, "duration", None) if web is not None else None
    if dur is not None:
        try:
            return int(dur)
        except (TypeError, ValueError):
            pass
    for obj in (
        getattr(msg, "video", None),
        getattr(msg, "video_note", None),
        getattr(msg, "gif", None),
        getattr(msg, "file", None),
    ):
        dur = getattr(obj, "duration", None) if obj is not None else None
        if dur is not None:
            try:
                return int(dur)
            except (TypeError, ValueError):
                continue
    return None


def media_file_size(msg: Any) -> Optional[int]:
    candidates = list(iter_video_documents(msg))
    candidates.extend((
        getattr(msg, "document", None),
        getattr(msg, "file", None),
        getattr(msg, "video", None),
        getattr(msg, "video_note", None),
        getattr(msg, "gif", None),
    ))
    for obj in candidates:
        size = getattr(obj, "size", None) if obj is not None else None
        if size is not None:
            try:
                return int(size)
            except (TypeError, ValueError):
                continue
    return None


def extract_cached_thumb(msg: Any) -> Optional[bytes]:
    """尽量从已缓存 / 压缩缩略图取出 JPEG，不发起网络请求。"""
    thumbs: list[Any] = []
    seen: set[int] = set()
    for obj in (
        getattr(msg, "document", None),
        getattr(msg, "video", None),
        getattr(msg, "video_note", None),
        getattr(msg, "gif", None),
        getattr(msg, "photo", None),
    ):
        if obj is None:
            continue
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        for t in getattr(obj, "thumbs", None) or []:
            thumbs.append(t)

    cached = None
    stripped = None
    for t in thumbs:
        if isinstance(t, PhotoCachedSize) and getattr(t, "bytes", None):
            area = int(getattr(t, "w", 0) or 0) * int(getattr(t, "h", 0) or 0)
            prev = int(getattr(cached, "w", 0) or 0) * int(getattr(cached, "h", 0) or 0) if cached else -1
            if cached is None or area >= prev:
                cached = t
        elif isinstance(t, PhotoStrippedSize) and getattr(t, "bytes", None):
            stripped = t
    if cached is not None:
        try:
            return bytes(cached.bytes)
        except Exception:
            pass
    if stripped is not None:
        try:
            return stripped_photo_to_jpg(stripped.bytes)
        except Exception:
            return None
    return None


_STANDARD_HEIGHTS = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)


def snap_quality_height(n: int) -> int:
    """Map a real short-side to a conventional rung. Never invent a file, only a label."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    # Nearest standard; if tied, prefer the lower rung so we don't oversell.
    return min(_STANDARD_HEIGHTS, key=lambda std: (abs(n - std), std))


def entity_photo_id(entity: Any) -> Optional[int]:
    photo = getattr(entity, "photo", None)
    if photo is None:
        return None
    name = type(photo).__name__
    if name.endswith("Empty") or "Empty" in name:
        return None
    pid = getattr(photo, "photo_id", None) or getattr(photo, "id", None)
    try:
        return int(pid) if pid is not None else None
    except (TypeError, ValueError):
        return None


def avatar_cache_path(dialog_id: int, photo_id: int) -> Path:
    return AVATAR_DIR / f"{int(dialog_id)}_{int(photo_id)}.jpg"


def load_cached_avatar(dialog_id: int, photo_id: int) -> Optional[bytes]:
    path = avatar_cache_path(dialog_id, photo_id)
    try:
        if path.is_file():
            data = path.read_bytes()
            return data or None
    except OSError:
        return None
    return None


def save_cached_avatar(dialog_id: int, photo_id: int, data: bytes) -> None:
    if not data:
        return
    try:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        for old in AVATAR_DIR.glob(f"{int(dialog_id)}_*.jpg"):
            try:
                old.unlink()
            except OSError:
                pass
        avatar_cache_path(dialog_id, photo_id).write_bytes(data)
    except OSError:
        pass


def document_video_attr(doc: Any) -> Optional[DocumentAttributeVideo]:
    for attr in getattr(doc, "attributes", []) or []:
        if isinstance(attr, DocumentAttributeVideo):
            return attr
    return None


def _doc_mime(doc: Any) -> str:
    return (getattr(doc, "mime_type", None) or "").lower()


def _doc_filename(doc: Any) -> str:
    for attr in getattr(doc, "attributes", None) or []:
        name = getattr(attr, "file_name", None)
        if name:
            return str(name)
    return ""


def _doc_has_attr(doc: Any, kind: type) -> bool:
    for attr in getattr(doc, "attributes", None) or []:
        if isinstance(attr, kind):
            return True
    return False


def document_is_sticker(doc: Any) -> bool:
    return doc is not None and _doc_has_attr(doc, DocumentAttributeSticker)


def document_is_audio(doc: Any) -> bool:
    return doc is not None and _doc_has_attr(doc, DocumentAttributeAudio)


def document_is_video(doc: Any) -> bool:
    """可当作视频文件的 document。贴纸和语音不算。"""
    if doc is None or document_is_sticker(doc) or document_is_audio(doc):
        return False
    if document_video_attr(doc):
        return True
    mime = _doc_mime(doc)
    if _doc_has_attr(doc, DocumentAttributeAnimated) and (mime.startswith("video/") or mime == "image/gif"):
        return True
    if mime.startswith("video/"):
        return True
    ext = Path(_doc_filename(doc)).suffix.lower()
    return ext in _VIDEO_EXTS


def _append_doc(docs: list[Any], seen: set[int], doc: Any) -> None:
    if doc is None:
        return
    # 没有 mime / attributes 的空壳不当成文件
    if not (getattr(doc, "mime_type", None) or getattr(doc, "attributes", None) or getattr(doc, "id", None)):
        return
    did = getattr(doc, "id", None)
    key = int(did) if isinstance(did, int) else id(doc)
    if key in seen:
        return
    seen.add(key)
    docs.append(doc)


def iter_message_documents(msg: Any) -> list[Any]:
    """主文件、备用画质、网页预览文件、实况照片里的视频、已解锁的付费媒体。"""
    docs: list[Any] = []
    seen: set[int] = set()
    media = getattr(msg, "media", None)

    def _take_media(obj: Any) -> None:
        if obj is None:
            return
        _append_doc(docs, seen, getattr(obj, "document", None))
        alts = getattr(obj, "alt_documents", None) or []
        if isinstance(alts, (list, tuple)):
            for alt in alts:
                _append_doc(docs, seen, alt)
        _append_doc(docs, seen, getattr(obj, "video", None))
        web = getattr(obj, "webpage", None)
        if web is not None:
            _append_doc(docs, seen, getattr(web, "document", None))
        for ext in getattr(obj, "extended_media", None) or []:
            _take_media(getattr(ext, "media", None))

    _take_media(media)
    # Telethon 的 Message.document 可能指向网页里的文件；上面已覆盖时会去重
    _append_doc(docs, seen, getattr(msg, "document", None))
    return docs


def iter_video_documents(msg: Any) -> list[Any]:
    """消息上所有真正的视频文件（含备用画质），去重。"""
    return [doc for doc in iter_message_documents(msg) if document_is_video(doc)]


def _kind_from_document(doc: Any) -> Optional[str]:
    if not document_is_video(doc):
        return None
    attr = document_video_attr(doc)
    if attr is not None and getattr(attr, "round_message", False):
        return "video_note"
    mime = _doc_mime(doc)
    if _doc_has_attr(doc, DocumentAttributeAnimated) and (mime.startswith("video/") or mime == "image/gif"):
        return "gif"
    return "video"


def _message_id_text(msg: Any) -> str:
    mid = int(getattr(msg, "id", 0) or 0)
    return f"消息 #{mid}" if mid else "该消息"


def _restriction_text(msg: Any) -> str:
    parts: list[str] = []
    for reason in getattr(msg, "restriction_reason", None) or []:
        text = str(getattr(reason, "text", None) or getattr(reason, "reason", None) or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "；".join(parts)


def classify_video_message(msg: Any) -> Optional[VideoClass]:
    """判断消息是不是视频。

    能下的会返回 downloadable=True。网页嵌入、未解锁的付费视频、直播流
    也会返回，但 downloadable=False，并带上给用户看的说明，避免静默丢掉。
    """
    if msg is None:
        return None
    media = getattr(msg, "media", None)
    docs = iter_message_documents(msg)
    kind: Optional[str] = None
    for doc in docs:
        found = _kind_from_document(doc)
        if not found:
            continue
        if found == "video_note":
            kind = "video_note"
            break
        if kind is None or kind == "gif":
            kind = found
    if kind is None and isinstance(media, MessageMediaDocument) and getattr(media, "video", None):
        main = getattr(media, "document", None)
        if main is None or not (document_is_sticker(main) or document_is_audio(main)):
            kind = "video_note" if getattr(media, "round", None) else "video"
    if isinstance(media, MessageMediaPhoto) and document_is_video(getattr(media, "video", None)):
        kind = "live"

    note = ""
    downloadable = True
    if kind is not None:
        if isinstance(media, MessageMediaPhoto) and kind == "live":
            note = f"{_message_id_text(msg)} 是实况照片，将下载其中的视频。"
            kind = "live"
        if getattr(msg, "video_processing_pending", None):
            extra = f"{_message_id_text(msg)} 的视频仍在处理，暂时可能下不下来。"
            note = f"{note} {extra}".strip() if note else extra
        return VideoClass(kind=kind, downloadable=downloadable, note=note)

    if isinstance(media, MessageMediaWebPage):
        web = getattr(media, "webpage", None)
        wtype = str(getattr(web, "type", None) or "").lower()
        etype = str(getattr(web, "embed_type", None) or "").lower()
        embed = str(getattr(web, "embed_url", None) or getattr(web, "url", None) or "").strip()
        looks_video = wtype in {"video", "video_live"} or "video" in etype
        if looks_video:
            host = ""
            if embed:
                try:
                    host = urlparse(embed).netloc
                except Exception:
                    host = ""
            where = f"（{host}）" if host else ""
            return VideoClass(
                kind="webpage",
                downloadable=False,
                note=f"{_message_id_text(msg)} 是网页嵌入视频{where}，Telegram 没有提供可下载的文件。",
            )

    if isinstance(media, MessageMediaPaidMedia):
        for ext in getattr(media, "extended_media", None) or []:
            if getattr(ext, "video_duration", None):
                return VideoClass(
                    kind="video",
                    downloadable=False,
                    note=f"{_message_id_text(msg)} 是尚未解锁的付费视频，无法下载文件。",
                )
        return None

    if isinstance(media, MessageMediaVideoStream):
        return VideoClass(
            kind="stream",
            downloadable=False,
            note=f"{_message_id_text(msg)} 是直播，Telegram 没有可下载的录像文件。",
        )
    return None


def unlisted_media_notice(msg: Any) -> Optional[str]:
    """不是视频、但也不该一声不吭丢掉的消息。普通文字、图片、贴纸返回 None。"""
    if msg is None or classify_video_message(msg):
        return None
    media = getattr(msg, "media", None)
    restricted = _restriction_text(msg)
    unsupported = isinstance(media, MessageMediaUnsupported) or type(media).__name__ == "MessageMediaUnsupported"
    empty = type(media).__name__ in {"MessageMediaEmpty"}
    if unsupported or (restricted and (media is None or empty or unsupported)):
        why = restricted or "当前接口无法识别这种媒体"
        return f"{_message_id_text(msg)} 因 Telegram 限制或未知媒体，未能确认是不是视频：{why}"
    return None


def quality_pixels(width: int, height: int) -> int:
    return max(0, int(width or 0)) * max(0, int(height or 0))


def quality_short_side(width: int, height: int) -> int:
    w, h = int(width or 0), int(height or 0)
    if w and h:
        return min(w, h)
    return w or h or 0


def quality_label(width: int, height: int) -> str:
    snapped = snap_quality_height(quality_short_side(width, height))
    return f"{snapped}p" if snapped else "原画"


def quality_key(width: int, height: int) -> str:
    label = quality_label(width, height)
    digits = "".join(ch for ch in label if ch.isdigit())
    return digits or QUALITY_HIGHEST


def list_video_qualities(msg: Any) -> list[VideoQuality]:
    items: list[VideoQuality] = []
    for doc in iter_video_documents(msg):
        attr = document_video_attr(doc)
        w = int(getattr(attr, "w", 0) or 0) if attr else 0
        h = int(getattr(attr, "h", 0) or 0) if attr else 0
        try:
            size = int(getattr(doc, "size", 0) or 0) or None
        except (TypeError, ValueError):
            size = None
        items.append(
            VideoQuality(
                label=quality_label(w, h),
                width=w,
                height=h,
                size=size,
                document=doc,
                key=quality_key(w, h),
            )
        )
    items.sort(key=lambda q: (quality_pixels(q.width, q.height), q.size or 0), reverse=True)
    # One file per conventional label; keep the largest.
    best: dict[str, VideoQuality] = {}
    order: list[str] = []
    for q in items:
        if q.label not in best:
            best[q.label] = q
            order.append(q.label)
    return [best[k] for k in order]


def quality_pref_label(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if raw in ("", QUALITY_HIGHEST, "最高", "原画", "max", "best"):
        return "最高"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return "最高"
    return f"{snap_quality_height(int(digits))}p"


def quality_pref_value(label: Optional[str]) -> str:
    raw = (label or "").strip().lower()
    if raw in ("", QUALITY_HIGHEST, "最高", "原画", "max", "best"):
        return QUALITY_HIGHEST
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return QUALITY_HIGHEST
    return str(snap_quality_height(int(digits)))


def pick_video_quality(qualities: list[VideoQuality], preference: Optional[str] = None) -> Optional[VideoQuality]:
    """Pick an existing file only. Never invent or transcode a missing quality."""
    if not qualities:
        return None
    if len(qualities) == 1:
        return qualities[0]
    pref = quality_pref_value(preference)
    if pref == QUALITY_HIGHEST:
        return qualities[0]
    try:
        want = int(pref)
    except ValueError:
        return qualities[0]
    want_label = f"{snap_quality_height(want)}p"
    exact = [
        q
        for q in qualities
        if q.label == want_label or q.key == pref or snap_quality_height(quality_short_side(q.width, q.height)) == snap_quality_height(want)
    ]
    if exact:
        exact.sort(key=lambda q: (quality_pixels(q.width, q.height), q.size or 0), reverse=True)
        return exact[0]
    lower = [
        q
        for q in qualities
        if snap_quality_height(quality_short_side(q.width, q.height))
        and snap_quality_height(quality_short_side(q.width, q.height)) <= snap_quality_height(want)
    ]
    if lower:
        return lower[0]  # already sorted highest-first
    return qualities[-1]


def known_quality_label(quality: Optional[VideoQuality]) -> str:
    """只展示来源里真实有的清晰度。没有宽高时的「原画」不写进行里。"""
    if quality is None:
        return ""
    label = (quality.label or "").strip()
    if not label or label == "原画":
        return ""
    return label


def video_row_subtitle(item: VideoItem, preference: Optional[str] = None) -> str:
    """对话选片行的副标题，例如「1080p · 42.6 MB · 今天 16:08」。

    清晰度用质量选择器同一套已有文件，不在没有清晰度时编一个。
    """
    chosen = pick_video_quality(list(getattr(item, "qualities", None) or []), preference)
    size = chosen.size if chosen is not None and chosen.size else getattr(item, "size", None)
    parts: list[str] = []
    label = known_quality_label(chosen)
    if label:
        parts.append(label)
    parts.append(format_bytes(size))
    parts.append(format_date_short(getattr(item, "date", None)))
    note = (getattr(item, "note", None) or "").strip()
    if note:
        parts.append(note)
    elif not getattr(item, "downloadable", True):
        parts.append("无法下载")
    return " · ".join(part for part in parts if part)


def _photo_size_area(thumb: Any) -> int:
    w = int(getattr(thumb, "w", 0) or 0)
    h = int(getattr(thumb, "h", 0) or 0)
    area = w * h
    if isinstance(thumb, PhotoSizeProgressive):
        try:
            area = area or max(int(s) for s in (thumb.sizes or [0]))
        except (TypeError, ValueError):
            pass
    elif isinstance(thumb, PhotoSize):
        area = area or int(getattr(thumb, "size", 0) or 0)
    elif isinstance(thumb, PhotoCachedSize):
        try:
            area = area or len(thumb.bytes or b"")
        except Exception:
            pass
    return area


def is_raster_photo_size(thumb: Any) -> bool:
    if thumb is None or isinstance(thumb, (PhotoPathSize, PhotoStrippedSize, VideoSize)):
        return False
    return isinstance(thumb, (PhotoSize, PhotoSizeProgressive, PhotoCachedSize))


def collect_thumb_containers(msg: Any) -> list[Any]:
    """Objects that may carry a still thumbnail (cover photo, documents)."""
    out: list[Any] = []
    seen: set[int] = set()

    def _add(obj: Any) -> None:
        if obj is None:
            return
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        out.append(obj)

    media = getattr(msg, "media", None)
    _add(getattr(media, "video_cover", None))
    _add(getattr(msg, "document", None))
    _add(getattr(msg, "video", None))
    _add(getattr(msg, "video_note", None))
    _add(getattr(msg, "gif", None))
    _add(getattr(msg, "photo", None))
    if isinstance(media, MessageMediaDocument) or media is not None:
        _add(getattr(media, "document", None))
        for doc in getattr(media, "alt_documents", None) or []:
            _add(doc)
    return out


def largest_raster_thumb(obj: Any) -> Optional[Any]:
    sizes = list(getattr(obj, "sizes", None) or []) + list(getattr(obj, "thumbs", None) or [])
    best = None
    best_area = -1
    for thumb in sizes:
        if not is_raster_photo_size(thumb):
            continue
        area = _photo_size_area(thumb)
        if area >= best_area:
            best = thumb
            best_area = area
    return best


def video_item_from_message(msg: Any) -> Optional[VideoItem]:
    info = classify_video_message(msg)
    if not info:
        return None
    caption = (getattr(msg, "message", None) or getattr(msg, "raw_text", None) or "")
    if not isinstance(caption, str):
        caption = str(caption or "")
    qualities = list_video_qualities(msg)
    chosen = pick_video_quality(qualities, QUALITY_HIGHEST)
    size = chosen.size if chosen else media_file_size(msg)
    width = chosen.width if chosen else 0
    height = chosen.height if chosen else 0
    return VideoItem(
        message_id=int(getattr(msg, "id", 0) or 0),
        date=getattr(msg, "date", None),
        duration=media_duration(msg),
        size=size,
        filename=original_media_name(msg),
        caption=caption.strip(),
        kind=info.kind,
        thumb_bytes=extract_cached_thumb(msg),
        message=msg,
        width=width,
        height=height,
        qualities=qualities,
        note=info.note,
        downloadable=info.downloadable,
        document_id=primary_document_id(msg),
    )


def primary_document_id(msg: Any) -> Optional[int]:
    """消息上第一个视频文件的 document id。没有则为 None。"""
    for doc in iter_video_documents(msg):
        did = getattr(doc, "id", None)
        if isinstance(did, int):
            return int(did)
    return None


def video_item_document_ids(item: VideoItem) -> set[int]:
    ids: set[int] = set()
    if isinstance(item.document_id, int):
        ids.add(int(item.document_id))
    for quality in item.qualities or []:
        did = getattr(getattr(quality, "document", None), "id", None)
        if isinstance(did, int):
            ids.add(int(did))
    return ids


def video_display_title(item: VideoItem) -> str:
    cap = (item.caption or "").strip()
    if cap:
        return cap.splitlines()[0].strip() or "视频"
    if item.filename:
        return Path(item.filename).name
    return "视频"


def original_media_name(msg: Any) -> Optional[str]:
    docs = list(iter_video_documents(msg))
    media = getattr(msg, "media", None)
    docs.append(getattr(media, "document", None) if media is not None else None)
    docs.append(getattr(msg, "document", None))
    for doc in docs:
        name = _doc_filename(doc) if doc is not None else ""
        if name:
            return name
    return None


def guess_ext(msg: Any, original: Optional[str]) -> str:
    if original and Path(original).suffix:
        ext = Path(original).suffix
        if 1 <= len(ext) <= 8:
            return ext
    doc = getattr(msg, "document", None)
    mime = (getattr(doc, "mime_type", None) or "").lower() if doc else ""
    mapping = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "video/x-matroska": ".mkv",
        "video/mpeg": ".mpg",
        "video/x-msvideo": ".avi",
    }
    return mapping.get(mime, ".mp4")


def build_save_name(msg: Any, chat_title: str) -> str:
    original = original_media_name(msg)
    ext = guess_ext(msg, original)
    date_tag = file_date_tag(getattr(msg, "date", None))
    title = sanitize_filename(chat_title or "chat", max_len=40)
    mid = getattr(msg, "id", 0)
    if original:
        stem = sanitize_filename(Path(original).stem, max_len=50)
        name = f"{date_tag}_{title}_{mid}_{stem}{ext}"
    else:
        name = f"{date_tag}_{title}_{mid}{ext}"
    # 预留目录路径长度，避免超过 Windows 260 限制
    if len(name) > 150:
        name = name[: 150 - len(ext)] + ext
    return name


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, ext = dest.stem, dest.suffix
    for i in range(2, 1000):
        candidate = directory / f"{stem}_{i}{ext}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{datetime.now().strftime('%H%M%S')}{ext}"


def parse_telegram_link(raw: str) -> ParsedLink:
    text = (raw or "").strip()
    if not text:
        raise UserFacingError("请先粘贴一条 Telegram 帖子链接。")

    # tg://resolve?domain=xxx&post=123
    if text.lower().startswith("tg://"):
        u = urlparse(text)
        qs = parse_qs(u.query)
        host = (u.netloc or u.path.lstrip("/")).lower()
        if host in {"resolve", "privatepost"} or "domain" in qs or "channel" in qs:
            domain = (qs.get("domain") or [None])[0]
            channel = (qs.get("channel") or [None])[0]
            post = (qs.get("post") or qs.get("msg_id") or [None])[0]
            comment = (qs.get("comment") or [None])[0]
            if not post or not str(post).isdigit():
                raise UserFacingError("该 tg:// 链接里没有消息 ID。")
            comment_id = int(comment) if comment and str(comment).isdigit() else None
            if domain:
                return ParsedLink(entity=domain.lstrip("@"), message_id=int(post), comment_id=comment_id, raw=text)
            if channel and str(channel).isdigit():
                cid = str(channel)
                entity = int(cid) if cid.startswith("-100") else int("-100" + cid)
                return ParsedLink(entity=entity, message_id=int(post), comment_id=comment_id, raw=text)
        raise UserFacingError("无法解析该 tg:// 链接，请改用 https://t.me/... 形式的帖子链接。")

    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text.lstrip("/")

    u = urlparse(text)
    host = u.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host not in TELEGRAM_HOSTS:
        raise UserFacingError("不是有效的 Telegram 链接（应为 t.me / telegram.me）。")

    parts = [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)
    if not parts:
        raise UserFacingError("链接不完整，请使用类似 https://t.me/用户名/123 的帖子链接。")

    first = parts[0]
    first_l = first.lower()
    blocked = {
        "joinchat", "addstickers", "proxy", "socks", "share", "iv",
        "login", "boost", "invoice", "setlanguage", "socks5",
    }
    if first_l in blocked or first.startswith("+"):
        raise UserFacingError("这是邀请或功能链接，不是帖子链接。请打开具体那条消息，再复制链接。")

    comment_id = None
    if "comment" in qs and str(qs["comment"][0]).isdigit():
        comment_id = int(qs["comment"][0])

    # /s/username/123 公开预览
    if first_l == "s":
        parts = parts[1:]
        if not parts:
            raise UserFacingError("链接不完整。")
        first = parts[0]
        first_l = first.lower()

    if first_l == "c":
        if len(parts) < 3:
            raise UserFacingError("私密频道链接缺少消息 ID，格式应为 t.me/c/频道数字ID/消息ID。")
        channel_raw = parts[1].replace("-100", "")
        if not channel_raw.isdigit():
            raise UserFacingError("频道 ID 无效。")
        # 论坛主题链接：/c/<id>/<topic>/<msg> ，消息 ID 总是最后一段
        msg_part = parts[-1]
        if not msg_part.isdigit():
            raise UserFacingError("消息 ID 无效。")
        return ParsedLink(
            entity=int("-100" + channel_raw),
            message_id=int(msg_part),
            comment_id=comment_id,
            raw=raw,
        )

    username = first[1:] if first.startswith("@") else first
    if not re.match(r"^[A-Za-z0-9_]{3,}$", username) and not username.startswith("+"):
        # 某些公开用户名可能含其他字符；仍尝试，但纯邀请哈希会在前面拦过
        pass
    if username.startswith("+"):
        raise UserFacingError("这是邀请链接，不是帖子链接。")

    msg_id: Optional[int] = None
    numeric_tail = [p for p in parts[1:] if p.isdigit()]
    if numeric_tail:
        msg_id = int(numeric_tail[-1])
    elif "post" in qs and str(qs["post"][0]).isdigit():
        msg_id = int(qs["post"][0])

    if not msg_id:
        raise UserFacingError("链接中没有消息 ID。请复制具体某一条消息的链接（例如 t.me/用户名/123）。")

    return ParsedLink(entity=username, message_id=msg_id, comment_id=comment_id, raw=raw)


def explain_error(exc: BaseException) -> str:
    if isinstance(exc, UserFacingError):
        return str(exc)
    if isinstance(exc, NeedPasswordError):
        return "该账号已开启两步验证，请输入云密码后再登录。"
    if isinstance(exc, FloodWaitError):
        seconds = int(getattr(exc, "seconds", 0) or 0)
        return f"请求过于频繁，Telegram 要求等待 {seconds} 秒后再试。"
    if isinstance(exc, SessionPasswordNeededError):
        return "该账号已开启两步验证，请输入云密码后再登录。"
    if isinstance(exc, PasswordHashInvalidError):
        return "两步验证密码不正确，请重试。"
    if isinstance(exc, PhoneCodeInvalidError):
        return "验证码不正确，请检查后重新输入。"
    if isinstance(exc, PhoneCodeExpiredError):
        return "验证码已过期，请重新发送验证码。"
    if isinstance(exc, PhoneNumberInvalidError):
        return "手机号无效，请加上国际区号（例如中国大陆为 +86）。"
    if isinstance(exc, PhoneNumberBannedError):
        return "该手机号已被 Telegram 限制，无法登录。"
    if isinstance(exc, (SessionRevokedError, AuthKeyUnregisteredError, SessionExpiredError)):
        return "登录已失效（会话被撤销或过期），请重新登录。"
    if isinstance(exc, (ChannelPrivateError, ChatAdminRequiredError)):
        return "当前账号无权访问该对话（未加入，或频道限制了查看权限）。"
    if isinstance(exc, (UsernameNotOccupiedError, UsernameInvalidError)):
        return "找不到该用户名，请检查链接是否正确。"
    if isinstance(exc, (PeerIdInvalidError, ChatIdInvalidError, ChannelInvalidError, MessageIdInvalidError)):
        return "对话或消息 ID 无效，可能消息已删除，或当前账号不在该对话中。"
    if isinstance(exc, FileNotFoundError):
        return f"找不到路径：{exc}"
    if isinstance(exc, PermissionError):
        return "没有写入权限，请更换保存目录，或以适当权限运行。"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return CONNECT_HINT
    if isinstance(exc, OSError):
        winerr = getattr(exc, "winerror", None)
        if exc.errno in (28,) or winerr in (112, 39):
            return "磁盘空间不足，或文件过大无法写入。请更换保存目录或清理空间后再试。"
        if winerr in (10060, 10061, 10065, 10051, 10053, 10054) or exc.errno in (110, 111, 113, 101, 104):
            return CONNECT_HINT
        return f"文件写入失败：{exc}"
    if isinstance(exc, ValueError) and "Could not find the input entity" in str(exc):
        return "无法识别该对话。请先在官方 Telegram 里打开一次该频道/群组，然后再用本工具下载。"
    if isinstance(exc, RPCError):
        return f"Telegram 返回错误：{exc.__class__.__name__}"
    if isinstance(exc, ConnectionError):
        return "网络连接失败，请检查网络后重试。"
    msg = str(exc).strip() or exc.__class__.__name__
    # 避免把过长的内部堆栈直接扔给用户
    if len(msg) > 240:
        msg = msg[:240] + "…"
    return f"操作失败：{msg}"


def dialog_kind(dialog: Any) -> str:
    entity = getattr(dialog, "entity", None)
    if getattr(dialog, "is_user", False):
        return "机器人" if getattr(entity, "bot", False) else "私聊"
    if getattr(entity, "broadcast", False):
        return "频道"
    if getattr(dialog, "is_group", False) or getattr(dialog, "is_channel", False):
        return "群组"
    return "对话"


def dialog_title(dialog: Any) -> str:
    name = getattr(dialog, "name", None) or getattr(dialog, "title", None)
    if name:
        return str(name)
    entity = getattr(dialog, "entity", None)
    parts = [
        getattr(entity, "first_name", None) or "",
        getattr(entity, "last_name", None) or "",
    ]
    title = " ".join(p for p in parts if p).strip()
    return title or getattr(entity, "username", None) or "未命名对话"


@dataclass
class VideoScanState:
    """每个来源的翻页断点。offset 为 0 表示从最新一条开始，下一次只取更旧的消息。"""

    offsets: dict[str, int] = field(default_factory=dict)
    done: dict[str, bool] = field(default_factory=dict)
    noted: set[str] = field(default_factory=set)


@dataclass
class VideoListPage:
    items: list[VideoItem] = field(default_factory=list)
    state: VideoScanState = field(default_factory=VideoScanState)
    has_more: bool = False
    notices: list[str] = field(default_factory=list)
    scanned: int = 0


# history：主时间线，能看到搜索索引里没有的文件视频、网页视频、实况照片。
# 其余是 Telegram 的媒体搜索，用来补上话题里不在主时间线的视频。
_VIDEO_SOURCES: tuple[tuple[str, Optional[type], str], ...] = (
    ("history", None, "历史"),
    ("video", InputMessagesFilterVideo, "视频"),
    ("round", InputMessagesFilterRoundVideo, "圆形视频"),
    ("gif", InputMessagesFilterGif, "动态 GIF"),
    ("document", InputMessagesFilterDocument, "视频文件"),
    ("url", InputMessagesFilterUrl, "网页视频"),
    ("photo", InputMessagesFilterPhotos, "实况照片"),
)

IterMessages = Callable[..., Any]


def _msg_id(msg: Any) -> int:
    try:
        return int(getattr(msg, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _is_video_msg(msg: Any) -> bool:
    return classify_video_message(msg) is not None


async def _read_source(
    iter_messages: IterMessages,
    *,
    offset: int,
    flt: Any,
    raw_budget: int,
    video_limit: int,
) -> tuple[list[Any], list[Any], Optional[int], int, bool]:
    """从新到旧读一个来源。

    返回 (视频消息, 原始消息, 扫到的最旧 id, 原始条数, 是否还没读完)。
    凑满 video_limit 个视频或达到 raw_budget 都会停，并记为还没读完。
    """
    matches: list[Any] = []
    raw: list[Any] = []
    oldest: Optional[int] = None
    scanned = 0
    kwargs: dict[str, Any] = {"offset_id": int(offset or 0), "limit": None}
    if flt is not None:
        kwargs["filter"] = flt
    async for msg in iter_messages(**kwargs):
        if msg is None:
            continue
        mid = _msg_id(msg)
        scanned += 1
        raw.append(msg)
        if mid:
            oldest = mid
        if _is_video_msg(msg):
            matches.append(msg)
            if len(matches) >= video_limit:
                return matches, raw, oldest, scanned, True
        if scanned >= raw_budget:
            return matches, raw, oldest, scanned, True
    return matches, raw, oldest, scanned, False


async def gather_chat_video_page(
    iter_messages: IterMessages,
    *,
    limit: int = 30,
    state: Optional[VideoScanState] = None,
    scan_budget: int = VIDEO_HISTORY_BUDGET,
    filter_budget: int = VIDEO_FILTER_BUDGET,
) -> VideoListPage:
    """列出下一页视频，并给出还能不能继续。

    旧实现只对「视频 / 圆形视频 / GIF」各取 ``limit`` 条原始结果，再丢掉
    识别不了的消息。静态 GIF 会占满名额，按文件发送的视频、网页里的视频、
    实况照片根本不在这三类索引里，返回条数不足时界面还会关掉「加载更多」。

    现在主时间线和各类搜索各自记断点，只往更旧的方向走。某一类没扫完就停在
    最后一条已经检查过的消息上，下一页接着查，不会跳过中间的视频。
    """
    state = state or VideoScanState()
    limit = max(1, int(limit or 30))
    scan_budget = max(1, int(scan_budget or VIDEO_HISTORY_BUDGET))
    filter_budget = max(1, int(filter_budget or VIDEO_FILTER_BUDGET))
    offsets = dict(state.offsets)
    done = {key: bool(state.done.get(key)) for key, _factory, _label in _VIDEO_SOURCES}
    noted = set(state.noted)
    if all(done.values()):
        return VideoListPage(
            state=VideoScanState(offsets=offsets, done=done),
            has_more=False,
        )

    notices: list[str] = []
    seen: set[int] = set()
    pool: list[Any] = []
    reports: dict[str, dict[str, Any]] = {}

    def _add(msg: Any) -> None:
        mid = _msg_id(msg)
        if not mid or mid in seen or not _is_video_msg(msg):
            return
        seen.add(mid)
        pool.append(msg)

    for key, factory, label in _VIDEO_SOURCES:
        if done.get(key):
            continue
        budget = scan_budget if key == "history" else filter_budget
        flt = factory() if factory is not None else None
        try:
            matches, raw, oldest, scanned, truncated = await _read_source(
                iter_messages,
                offset=offsets.get(key, 0),
                flt=flt,
                raw_budget=budget,
                video_limit=limit,
            )
        except (FloodWaitError, UserFacingError):
            raise
        except Exception as exc:
            notices.append(f"列出「{label}」时出错，这一类可能不完整：{explain_error(exc)}")
            done[key] = True
            continue
        reports[key] = {
            "oldest": oldest,
            "truncated": truncated,
            "raw": raw,
            "matches": matches,
            "scanned": scanned,
            "hold_at": 0,
        }

    history_report = reports.get("history")
    if history_report and history_report["truncated"] and history_report["oldest"]:
        history_floor = int(history_report["oldest"])
    elif history_report or done.get("history"):
        # 主时间线这一页已经到头，搜索里更旧的结果也可以放进来。
        history_floor = 0
    else:
        history_floor = int(offsets.get("history") or 0)

    for key, _factory, _label in _VIDEO_SOURCES:
        report = reports.get(key)
        if not report:
            continue
        for msg in report["matches"]:
            mid = _msg_id(msg)
            if key != "history" and history_floor and mid and mid < history_floor:
                report["hold_at"] = history_floor
                continue
            _add(msg)

    pool.sort(key=_msg_id, reverse=True)
    sliced = len(pool) > limit
    if sliced:
        pool = pool[:limit]
        cutoff = _msg_id(pool[-1])
        for key, _factory, _label in _VIDEO_SOURCES:
            if done.get(key):
                continue
            offsets[key] = cutoff
            done[key] = False
        has_more = True
    else:
        for key, _factory, _label in _VIDEO_SOURCES:
            if done.get(key):
                continue
            report = reports.get(key)
            if report is None:
                done[key] = True
                continue
            if report.get("hold_at"):
                # 搜索结果比主时间线更旧时先按住，等历史扫到那里再合并，避免下一页又列一次。
                offsets[key] = int(report["hold_at"])
                done[key] = False
            elif report["truncated"] and report["oldest"]:
                offsets[key] = int(report["oldest"])
                done[key] = False
            else:
                done[key] = True
                if report["oldest"]:
                    offsets[key] = int(report["oldest"])
        has_more = any(not done.get(key) for key, _factory, _label in _VIDEO_SOURCES)

    history_report = history_report or {}
    history_raw = history_report.get("raw") or []
    history_floor = 0 if done.get("history") else int(offsets.get("history") or 0)
    unlisted = 0
    for msg in history_raw:
        mid = _msg_id(msg)
        if history_floor and mid and mid < history_floor:
            continue
        note = unlisted_media_notice(msg)
        if not note:
            continue
        unlisted += 1
        if unlisted <= 5:
            notices.append(note)
    if unlisted > 5:
        notices.append(f"另有 {unlisted - 5} 条消息因限制或未知媒体，未能确认是不是视频。")

    if (
        history_report.get("truncated")
        and not sliced
        and len(pool) < limit
        and history_report.get("scanned")
    ):
        notices.append(
            f"这一页已检查 {int(history_report['scanned'])} 条消息，后面可能还有视频，请点「加载更多」。"
        )

    for key, _factory, label in _VIDEO_SOURCES:
        # 视频 / 圆形视频 / GIF 凑满一页就会停下，按钮本身已经说明还能继续。
        # 文件、链接、图片索引里大部分不是视频，扫完一截却一条都没有时要说清楚。
        if key not in {"document", "url", "photo"} or done.get(key):
            continue
        report = reports.get(key)
        if not report or not report.get("truncated") or sliced:
            continue
        if any(_is_video_msg(msg) for msg in report.get("raw") or []):
            continue
        if key in noted:
            continue
        noted.add(key)
        notices.append(f"「{label}」这一页还没扫描完。请继续点「加载更多」，以免漏掉话题里的视频。")

    # 断点没有前进时停止，避免空转。
    if has_more and not pool and offsets == dict(state.offsets) and done == {
        key: bool(state.done.get(key)) for key, _f, _l in _VIDEO_SOURCES
    }:
        has_more = False
        for key, _factory, _label in _VIDEO_SOURCES:
            done[key] = True

    items: list[VideoItem] = []
    for msg in pool:
        item = video_item_from_message(msg)
        if item is not None:
            items.append(item)
        else:
            notices.append(f"{_message_id_text(msg)} 识别为视频，但无法放进列表。")

    return VideoListPage(
        items=items,
        state=VideoScanState(offsets=dict(offsets), done=dict(done), noted=set(noted)),
        has_more=has_more,
        notices=notices,
        scanned=int(history_report.get("scanned") or 0),
    )


def filter_dialogs(dialogs: Iterable[DialogItem], kind: str = "全部", query: str = "") -> list[DialogItem]:
    """对话列表的筛选和搜索。kind 为「全部」时不过滤类型。"""
    kind = kind or "全部"
    q = (query or "").strip().lower()
    items: list[DialogItem] = []
    for dialog in dialogs:
        if kind != "全部" and dialog.kind != kind:
            continue
        if q:
            hay = f"{dialog.title} {dialog.kind} {dialog.id}".lower()
            if q not in hay:
                continue
        items.append(dialog)
    return items


def visible_chat_selection(
    selected: Optional[DialogItem],
    visible: Iterable[DialogItem],
) -> Optional[DialogItem]:
    """筛选后如果当前项不在可见列表里，只取消高亮，不改已打开的对话。"""
    if selected is None:
        return None
    if any(item.id == selected.id for item in visible):
        return selected
    return None


def startup_login_target(
    *,
    has_credentials: bool,
    authorized: bool,
    phone_pending: bool = False,
    code_dialog: bool = False,
    password_dialog: bool = False,
    api_dialog: bool = False,
    phone_dialog: bool = False,
) -> str:
    """启动 / 连接完成后该打开哪个登录步骤。

    返回 ``api`` / ``phone`` / ``ready`` / ``stay``。
    ``stay`` 表示用户已经在填写验证码或 API，不要把窗口拽回去。
    """
    if not has_credentials:
        return "api"
    if authorized:
        return "ready"
    if phone_pending or code_dialog or password_dialog or api_dialog or phone_dialog:
        return "stay"
    return "phone"


def login_dialog_after_session_loss(has_credentials: bool) -> str:
    """会话失效并清掉本机会话之后，下一步对话框。"""
    return "phone" if has_credentials else "api"


def validate_proxy_settings(kind: str, port_text: str) -> tuple[str, Any]:
    """校验代理类型和端口。直连时端口原样返回，不要求是数字。"""
    kind = (kind or "none").strip().lower()
    if kind not in ("none", "socks5", "http", "off", "direct"):
        raise UserFacingError("代理类型请选择 直连、SOCKS5 或 HTTP。")
    if kind in ("none", "off", "direct"):
        return "none", port_text
    try:
        port = int(str(port_text).strip())
    except (TypeError, ValueError):
        port = 0
    if port <= 0 or port > 65535:
        raise UserFacingError("端口应为 1–65535 之间的数字。")
    return kind, port


def split_link_text(raw: str) -> list[str]:
    """从输入框拆出链接。按空白和逗号分隔，去掉重复。"""
    items: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(",", " ").replace("，", " ").split():
        text = part.strip().strip("，；;")
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def link_failure_text(url: str, exc: BaseException, message_id: Optional[int] = None, title: str = "") -> str:
    """失败说明里写上是哪一条链接（以及消息 id），方便对上列表里的那一行。"""
    detail = explain_error(exc)
    url = (url or "").strip()
    bits: list[str] = []
    if url:
        bits.append(f"链接 {url}")
    title = (title or "").strip()
    if title and title not in (url, "chat"):
        bits.append(title)
    if message_id:
        bits.append(f"消息 #{int(message_id)}")
    if not bits:
        return detail
    prefix = " · ".join(bits)
    if url and url in detail and (not message_id or f"#{int(message_id)}" in detail):
        return detail
    return f"{prefix}：{detail}"


def is_user_stop(exc: BaseException) -> bool:
    return isinstance(exc, UserFacingError) and str(exc).strip().rstrip("。") == "已停止"


@dataclass
class LinkJob:
    raw: str
    parsed: Optional[ParsedLink] = None
    error: str = ""


def plan_link_jobs(links: Iterable[str]) -> list[LinkJob]:
    """逐条解析。某一条不合法时只让这一条失败，并在错误里写上原文。"""
    jobs: list[LinkJob] = []
    for raw in links:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            parsed = parse_telegram_link(text)
        except Exception as exc:
            jobs.append(LinkJob(raw=text, error=link_failure_text(text, exc)))
        else:
            jobs.append(LinkJob(raw=text, parsed=parsed))
    return jobs


# 这些状态都可以直接从列表里删掉。正在下载的会先取消再删。
REMOVABLE_TASK_STATUSES = {"queued", "paused", "stopped", "error", "done", "running"}


# 关闭时正在下载的任务恢复成这个状态，避免界面假装还在跑。
INTERRUPTED_TASK_HINT = "上次关闭时未下完，已停止。可删除后重新下载。"

# 这些状态会写进 data/download_queue.json。已完成的不回到任务列表，只记入下载记录。
PERSISTED_TASK_STATUSES = {"queued", "paused", "stopped", "error", "running"}


def task_wait_seconds(status: str, wait_seconds: int = 0) -> int:
    try:
        seconds = int(wait_seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 0 or status not in ("running", "queued", "paused"):
        return 0
    return seconds


def task_matches_filter(status: str, filt: str) -> bool:
    """已停止和失败只出现在「全部」，不进「已完成」，也不算正在下载。"""
    chosen = filt if filt in TASK_FILTERS else TASK_FILTER_ALL
    if chosen == TASK_FILTER_ALL:
        return True
    if chosen == TASK_FILTER_DONE:
        return status == "done"
    return status in ("running", "queued", "paused")


def task_show_stop(status: str, wait_seconds: int = 0) -> bool:
    if task_wait_seconds(status, wait_seconds):
        return True
    return status in ("running", "queued", "paused")


def task_show_delete(status: str) -> bool:
    return status in {"queued", "paused", "stopped", "error", "done", "running"}


def task_progress_percent(status: str, current: int = 0, total: int = 0) -> int:
    if status == "done":
        return 100
    try:
        current_n = int(current or 0)
        total_n = int(total or 0)
    except (TypeError, ValueError):
        return 0
    if total_n <= 0:
        return 0
    return max(0, min(100, int(current_n * 100 / total_n)))


def task_size_text(status: str, current: int = 0, total: int = 0) -> str:
    try:
        current_n = int(current or 0)
        total_n = int(total or 0)
    except (TypeError, ValueError):
        return ""
    if status == "done":
        size = total_n or current_n
        return format_bytes(size) if size else ""
    if total_n:
        return f"{format_bytes(current_n)} / {format_bytes(total_n)}"
    if current_n:
        return format_bytes(current_n)
    return ""


def task_speed_text(status: str, speed: float = 0.0, wait_seconds: int = 0) -> str:
    wait = task_wait_seconds(status, wait_seconds)
    if wait:
        return f"等待 {wait} 秒"
    if status == "stopped":
        return "已停止"
    if status == "done":
        return "已完成"
    if status == "error":
        return "失败"
    if status in ("queued", "paused"):
        return "排队中"
    if status == "running":
        return format_speed(speed) if speed else "下载中"
    return ""


def task_subtitle(status: str, error: str = "", wait_seconds: int = 0) -> str:
    if task_wait_seconds(status, wait_seconds):
        return FLOOD_ROW_SUBTITLE
    if status == "stopped":
        text = (error or "").strip()
        if text == INTERRUPTED_TASK_HINT or "未下完" in text:
            return STOPPED_ROW_SUBTITLE
        return text
    if status == "error":
        return (error or "").strip()
    return ""


def task_bar_tone(status: str, wait_seconds: int = 0) -> str:
    if task_wait_seconds(status, wait_seconds):
        return "wait"
    if status == "done":
        return "done"
    if status == "error":
        return "error"
    if status == "stopped":
        return "stopped"
    return "active"


def present_task(
    name: str,
    status: str,
    *,
    current: int = 0,
    total: int = 0,
    speed: float = 0.0,
    error: str = "",
    wait_seconds: int = 0,
) -> dict[str, Any]:
    """两种界面共用的任务行。已下载标记不在这里，它只看 peer id + message id。"""
    percent = task_progress_percent(status, current, total)
    return {
        "name": name,
        "status": status,
        "size_text": task_size_text(status, current, total),
        "speed_text": task_speed_text(status, speed, wait_seconds),
        "subtitle": task_subtitle(status, error, wait_seconds),
        "percent": percent,
        "percent_text": f"{percent}%",
        "bar": task_bar_tone(status, wait_seconds),
        "show_stop": task_show_stop(status, wait_seconds),
        "show_delete": task_show_delete(status),
        "wait_seconds": task_wait_seconds(status, wait_seconds),
    }


def summarize_tasks(rows: Iterable[dict]) -> str:
    """筛选条旁的统计。已停止单独列出，已完成不混进下载中。"""
    running = waiting = stopped = queued = done = errors = total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        total += 1
        status = str(row.get("status") or "")
        if task_wait_seconds(status, int(row.get("wait_seconds") or 0)):
            waiting += 1
        elif status == "running":
            running += 1
        elif status == "stopped":
            stopped += 1
        elif status in ("queued", "paused"):
            queued += 1
        elif status == "done":
            done += 1
        elif status == "error":
            errors += 1
    if total == 0:
        return "就绪"
    parts: list[str] = []
    if running:
        parts.append(f"下载中 {running}")
    if waiting:
        parts.append(f"等待 {waiting}")
    if stopped:
        parts.append(f"已停止 {stopped}")
    if errors:
        parts.append(f"失败 {errors}")
    named = running + waiting + stopped + errors
    if queued and named:
        parts.append(f"共 {total}")
    elif queued:
        parts.append(f"排队 {queued}")
    if parts:
        return " · ".join(parts)
    if done:
        return f"已完成 {done}"
    return "就绪"

_UNSET = object()


@dataclass
class QueueTask:
    key: str
    name: str
    status: str
    url: str = ""
    error: str = ""
    current: int = 0
    total: int = 0
    speed: float = 0.0
    peer_id: Optional[int] = None
    message_id: Optional[int] = None
    document_id: Optional[int] = None
    save_path: str = ""


def canonical_peer_id(value: Any) -> Optional[int]:
    """对话列表和链接解析共用的 peer id。频道会变成 -100…，和 dialog.id 一致。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
        return None
    if type(value).__name__ == "DialogItem":
        raw = getattr(value, "id", None)
        return int(raw) if isinstance(raw, int) else None
    try:
        from telethon.utils import get_peer_id

        return int(get_peer_id(value))
    except Exception:
        raw = getattr(value, "id", None)
        return int(raw) if isinstance(raw, int) else None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _task_from_dict(raw: dict) -> Optional[QueueTask]:
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip()
    if not key:
        return None
    status = str(raw.get("status") or "queued").strip() or "queued"
    try:
        current = int(raw.get("current") or 0)
        total = int(raw.get("total") or 0)
    except (TypeError, ValueError):
        current, total = 0, 0
    peer = raw.get("peer_id")
    message_id = raw.get("message_id")
    document_id = raw.get("document_id")
    task = QueueTask(
        key=key,
        name=str(raw.get("name") or key),
        status=status,
        url=str(raw.get("url") or ""),
        error=str(raw.get("error") or ""),
        current=current,
        total=total,
        speed=0.0,
        peer_id=canonical_peer_id(peer),
        message_id=int(message_id) if isinstance(message_id, int) or (isinstance(message_id, str) and message_id.lstrip("-").isdigit()) else None,
        document_id=int(document_id) if isinstance(document_id, int) else None,
        save_path=str(raw.get("save_path") or ""),
    )
    if isinstance(message_id, str) and message_id.lstrip("-").isdigit():
        task.message_id = int(message_id)
    return task


def normalize_task_for_restore(task: QueueTask) -> Optional[QueueTask]:
    """已完成的不回到任务列表。中断中的改为已停止，不假装还在下载。"""
    status = (task.status or "").strip()
    if status == "done":
        return None
    if status not in PERSISTED_TASK_STATUSES and status not in {"paused", "stopped", "queued", "error"}:
        return None
    task.speed = 0.0
    if status in ("running", "paused"):
        task.status = "stopped"
        if not (task.error or "").strip():
            task.error = INTERRUPTED_TASK_HINT
    return task


def restored_link_text(tasks: Iterable[QueueTask]) -> str:
    """把未完成任务里的链接放回输入框，方便再次点「开始下载」。"""
    urls: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        url = (task.url or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return "\n".join(urls)


def _queue_payload(items: dict[str, QueueTask], order: list[str]) -> dict:
    tasks: list[dict] = []
    for key in order:
        item = items.get(key)
        if item is None or item.status not in PERSISTED_TASK_STATUSES:
            continue
        tasks.append(
            {
                "key": item.key,
                "name": item.name,
                "status": item.status,
                "url": item.url,
                "error": item.error,
                "current": item.current,
                "total": item.total,
                "peer_id": item.peer_id,
                "message_id": item.message_id,
                "document_id": item.document_id,
                "save_path": item.save_path,
            }
        )
    return {"version": 1, "tasks": tasks}


def load_unfinished_tasks(path: Path) -> list[QueueTask]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    rows = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    loaded: list[QueueTask] = []
    seen: set[str] = set()
    for row in rows:
        task = _task_from_dict(row)
        if task is None or task.key in seen:
            continue
        task = normalize_task_for_restore(task)
        if task is None:
            continue
        seen.add(task.key)
        loaded.append(task)
    return loaded


class DownloadQueue:
    """链接 / 对话下载任务的状态。界面只负责显示，删除和取消以这里为准。

    ``persist_path`` 指向应用数据目录里的队列文件。只保存未完成项；
    进程重启后正在下载的会变成已停止。
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, QueueTask] = {}
        self._order: list[str] = []
        self._cancelled: set[str] = set()
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path is not None:
            self._load()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._order.clear()
            self._cancelled.clear()
            self._save_locked()

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def get(self, key: str) -> Optional[QueueTask]:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            return QueueTask(**item.__dict__)

    def cancelled(self, key: str) -> bool:
        with self._lock:
            return key in self._cancelled

    def upsert(
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
        index: Optional[int] = None,
        peer_id: Any = _UNSET,
        message_id: Any = _UNSET,
        document_id: Any = _UNSET,
        save_path: Any = _UNSET,
    ) -> None:
        with self._lock:
            if key in self._cancelled and status in ("queued", "running"):
                return
            prev = self._items.get(key)
            if prev is not None and not url:
                url = prev.url
            if peer_id is _UNSET:
                peer_id = prev.peer_id if prev is not None else None
            else:
                peer_id = canonical_peer_id(peer_id)
            if message_id is _UNSET:
                message_id = prev.message_id if prev is not None else None
            elif message_id is not None:
                try:
                    message_id = int(message_id)
                except (TypeError, ValueError):
                    message_id = prev.message_id if prev is not None else None
            if document_id is _UNSET:
                document_id = prev.document_id if prev is not None else None
            elif document_id is not None:
                try:
                    document_id = int(document_id)
                except (TypeError, ValueError):
                    document_id = prev.document_id if prev is not None else None
            if save_path is _UNSET:
                save_path = prev.save_path if prev is not None else ""
            else:
                save_path = str(save_path or "")
            changed = prev is None or any(
                (
                    prev.name != name,
                    prev.status != status,
                    prev.url != url,
                    prev.error != error,
                    prev.peer_id != peer_id,
                    prev.message_id != message_id,
                    prev.document_id != document_id,
                    prev.save_path != save_path,
                    key not in self._order,
                )
            )
            self._items[key] = QueueTask(
                key=key,
                name=name,
                status=status,
                url=url,
                error=error,
                current=current,
                total=total,
                speed=speed,
                peer_id=peer_id,
                message_id=message_id,
                document_id=document_id,
                save_path=save_path,
            )
            if key not in self._order:
                if index is None or index >= len(self._order):
                    self._order.append(key)
                else:
                    self._order.insert(index, key)
            # 进度数字经常变，不为此反复写盘。状态和身份变了才保存。
            if changed:
                self._save_locked()

    def note_identity(
        self,
        key: str,
        *,
        peer_id: Any = None,
        message_id: Any = None,
        document_id: Any = None,
        save_path: Any = None,
    ) -> None:
        with self._lock:
            item = self._items.get(key)
            if item is None or key in self._cancelled:
                return
            name, status, url, error = item.name, item.status, item.url, item.error
            current, total, speed = item.current, item.total, item.speed
        self.upsert(
            key,
            name,
            status,
            url=url,
            error=error,
            current=current,
            total=total,
            speed=speed,
            peer_id=item.peer_id if peer_id is None else peer_id,
            message_id=item.message_id if message_id is None else message_id,
            document_id=item.document_id if document_id is None else document_id,
            save_path=item.save_path if save_path is None else save_path,
        )

    def replace(self, old: str, items: list[tuple[str, str, int, str]]) -> None:
        """用一组子任务替换占位链接。old 若已取消，则子任务也视为取消。"""
        with self._lock:
            if old in self._cancelled or old not in self._items:
                for key, _name, _total, _url in items:
                    self._cancelled.add(key)
                self._drop_locked(old)
                self._save_locked()
                return
            idx = self._order.index(old) if old in self._order else len(self._order)
            parent = self._items.get(old)
            self._drop_locked(old)
            for offset, (key, name, total, url) in enumerate(items):
                if not url and parent is not None:
                    url = parent.url
                self._items[key] = QueueTask(
                    key=key,
                    name=name,
                    status="queued",
                    url=url,
                    total=total,
                    peer_id=parent.peer_id if parent is not None else None,
                )
                self._order.insert(idx + offset, key)
            self._save_locked()

    def remove(self, key: str) -> str:
        """删除一行。排队中或下载中会先标记取消。返回 removed / cancelled / missing。"""
        with self._lock:
            item = self._items.get(key)
            self._cancelled.add(key)
            if item is None:
                self._save_locked()
                return "missing"
            status = item.status
            self._drop_locked(key)
            self._save_locked()
            if status in ("running", "queued"):
                return "cancelled"
            return "removed"

    def stop(self, key: str) -> str:
        """停掉一行，保留在列表里。返回 stopped / ignored / missing。"""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return "missing"
            if item.status not in ("running", "queued", "paused"):
                return "ignored"
            self._cancelled.add(key)
            item.status = "stopped"
            item.speed = 0.0
            if not (item.error or "").strip():
                item.error = ""
            self._save_locked()
            return "stopped"

    def mark_stopped(self) -> None:
        """全局停止：还没结束的改为已停止，已完成和失败的不动。"""
        with self._lock:
            for item in self._items.values():
                if item.status in ("queued", "running", "paused"):
                    item.status = "stopped"
                    item.speed = 0.0
            self._save_locked()

    def prepare_for_exit(self) -> None:
        """退出前把正在下载的改成已停止并写盘。"""
        with self._lock:
            for item in self._items.values():
                if item.status in ("running", "paused"):
                    item.status = "stopped"
                    item.speed = 0.0
                    if not (item.error or "").strip():
                        item.error = INTERRUPTED_TASK_HINT
            self._save_locked()

    def _drop_locked(self, key: str) -> None:
        self._items.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    def _save_locked(self) -> None:
        if self._persist_path is None:
            return
        try:
            _atomic_write_json(self._persist_path, _queue_payload(self._items, self._order))
        except OSError:
            pass

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.is_file():
            return
        loaded = load_unfinished_tasks(self._persist_path)
        with self._lock:
            self._items.clear()
            self._order.clear()
            for task in loaded:
                self._items[task.key] = task
                self._order.append(task.key)
            self._save_locked()


def load_download_records(path: Path) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    records: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        message_id = row.get("message_id")
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            continue
        document_id = row.get("document_id")
        try:
            document_id = int(document_id) if document_id is not None else None
        except (TypeError, ValueError):
            document_id = None
        records.append(
            {
                "peer_id": canonical_peer_id(row.get("peer_id")),
                "message_id": message_id,
                "document_id": document_id,
                "path": str(row.get("path") or ""),
                "url": str(row.get("url") or ""),
            }
        )
    return records


def record_download(
    path: Path,
    *,
    peer_id: Any,
    message_id: int,
    save_path: str,
    document_id: Optional[int] = None,
    url: str = "",
) -> None:
    """记住一次成功的下载。文件后来被删掉时，记录还在，但不再算已下载。"""
    peer = canonical_peer_id(peer_id)
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return
    if peer is None or not save_path:
        return
    records = [row for row in load_download_records(path) if not (
        row.get("peer_id") == peer and row.get("message_id") == message_id
    )]
    records.append(
        {
            "peer_id": peer,
            "message_id": message_id,
            "document_id": int(document_id) if isinstance(document_id, int) else None,
            "path": str(save_path),
            "url": url or "",
        }
    )
    try:
        _atomic_write_json(Path(path), {"version": 1, "items": records})
    except OSError:
        pass


def _record_file_exists(record: dict) -> bool:
    text = str(record.get("path") or "").strip()
    if not text:
        return False
    file = Path(text)
    try:
        return file.is_file() and file.stat().st_size > 0
    except OSError:
        return False


def message_is_downloaded(
    records: Iterable[dict],
    *,
    peer_id: Any,
    message_id: int,
    document_ids: Optional[Iterable[int]] = None,
) -> bool:
    """同一对话里的同一条消息（或同一个视频文件）且文件还在，才算已下载。

    从对话里下载和从链接下载写的是同一条记录。文件被删掉则视为未下载。
    """
    peer = canonical_peer_id(peer_id)
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return False
    docs = {int(item) for item in (document_ids or []) if isinstance(item, int)}
    if peer is None:
        return False
    for record in records:
        if canonical_peer_id(record.get("peer_id")) != peer:
            continue
        if not _record_file_exists(record):
            continue
        if record.get("message_id") == message_id:
            return True
        doc = record.get("document_id")
        if isinstance(doc, int) and doc in docs:
            return True
    return False


def apply_downloaded_marks(items: Iterable[VideoItem], peer_id: Any, records: Iterable[dict]) -> None:
    """给当前对话的视频打上是否已下载。记录可以来自链接或对话下载。"""
    cached = list(records)
    for item in items:
        item.downloaded = message_is_downloaded(
            cached,
            peer_id=peer_id,
            message_id=item.message_id,
            document_ids=video_item_document_ids(item),
        )


def video_downloaded_label(item: VideoItem) -> str:
    return "已下载" if item.downloaded else ""


class TelegramService:
    def __init__(self, api_id: int, api_hash: str, proxy: Optional[tuple] = None) -> None:
        ensure_data_dir()
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.proxy = proxy
        # flood_sleep_threshold=0：遇到限流立即抛出，由界面提示并等待
        self.client = TelegramClient(
            str(SESSION_PATH),
            self.api_id,
            self.api_hash,
            proxy=proxy,
            timeout=8,
            connection_retries=2,
            retry_delay=1,
            use_ipv6=False,
            flood_sleep_threshold=0,
            device_model="TG Video Downloader",
            system_version="Windows",
            # 固定客户端标识，避免每次改版本号被 Telegram 当成新设备
            app_version="1.3",
            lang_code="zh",
            system_lang_code="zh-CN",
        )
        ensure_session_file()

    def persist_session(self) -> None:
        persist_session(self.client)

    async def connect(self) -> None:
        try:
            await asyncio.wait_for(self.client.connect(), timeout=18)
        except asyncio.TimeoutError as exc:
            raise UserFacingError(CONNECT_HINT) from exc
        except OSError as exc:
            raise UserFacingError(CONNECT_HINT) from exc

    async def disconnect(self) -> None:
        try:
            persist_session(self.client)
        except Exception:
            pass
        try:
            await self.client.disconnect()
        except Exception:
            pass
        try:
            backup_session()
        except Exception:
            pass

    async def is_authorized(self) -> bool:
        return bool(await self.client.is_user_authorized())

    async def send_code(self, phone: str) -> None:
        phone = _normalize_phone(phone)
        await self.client.send_code_request(phone)

    async def sign_in(self, phone: str, code: str, password: Optional[str] = None) -> Any:
        phone = _normalize_phone(phone)
        code = (code or "").strip().replace(" ", "")
        try:
            await self.client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            if not (password or "").strip():
                raise NeedPasswordError()
            await self.client.sign_in(password=password.strip())
        me = await self.client.get_me()
        persist_session(self.client)
        return me

    async def sign_in_password(self, password: str) -> Any:
        if not (password or "").strip():
            raise UserFacingError("请输入两步验证密码。")
        await self.client.sign_in(password=password.strip())
        me = await self.client.get_me()
        persist_session(self.client)
        return me

    async def get_me(self) -> Any:
        return await self.client.get_me()

    def me_label(self, me: Any) -> str:
        if not me:
            return "已登录"
        username = getattr(me, "username", None)
        if username:
            return f"@{username}"
        parts = [getattr(me, "first_name", None) or "", getattr(me, "last_name", None) or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or "已登录"

    async def list_dialogs(self, limit: int = 1000) -> list[DialogItem]:
        items: list[DialogItem] = []
        async for d in self.client.iter_dialogs(limit=limit):
            items.append(
                DialogItem(
                    entity=d.entity,
                    title=dialog_title(d),
                    kind=dialog_kind(d),
                    id=int(d.id),
                    photo_id=entity_photo_id(getattr(d, "entity", None)),
                )
            )
        return items

    async def list_chat_videos(
        self,
        entity: Any,
        limit: int = 30,
        offset_id: int = 0,
        state: Optional[VideoScanState] = None,
    ) -> VideoListPage:
        """按时间倒序列出视频。``state`` 为上一页的断点；不传则从最新开始。

        主时间线会识别普通视频、圆形视频、动态 GIF、按文件发送的视频、
        网页里附带的视频文件和实况照片。搜不到或下不了的会留在结果里并写明原因，
        或者写进 ``notices``，不再静默丢掉。
        ``offset_id`` 只在没有 ``state`` 时作为历史起点，兼容旧调用。
        """
        peer = await self.resolve_entity(entity)
        if state is None and int(offset_id or 0):
            state = VideoScanState(offsets={"history": int(offset_id)})

        async def _iter(**kwargs: Any) -> Any:
            async for msg in self.client.iter_messages(peer, **kwargs):
                yield msg

        return await gather_chat_video_page(_iter, limit=limit, state=state)

    async def download_profile_photo(self, item: DialogItem) -> Optional[bytes]:
        """下载对话头像。无照片或失败返回 None，不抛给界面。"""
        if item.photo_id:
            cached = load_cached_avatar(item.id, item.photo_id)
            if cached:
                return cached
        entity = item.entity
        if entity is None or not self.client:
            return None
        if item.photo_id is None and entity_photo_id(entity) is None:
            return None
        try:
            data = await self.client.download_profile_photo(entity, file=bytes, download_big=True)
        except Exception:
            return None
        if isinstance(data, (bytes, bytearray)) and data:
            blob = bytes(data)
            pid = item.photo_id or entity_photo_id(entity)
            if pid:
                item.photo_id = pid
                save_cached_avatar(item.id, pid, blob)
            return blob
        return None

    async def download_video_thumb(self, item: VideoItem) -> Optional[bytes]:
        """拉取最大静帧封面。压缩/缓存图只作占位，成功后替换。失败返回 None。"""
        msg = item.message
        if msg is None or not self.client:
            return None
        for obj in collect_thumb_containers(msg):
            thumb = largest_raster_thumb(obj)
            if thumb is None:
                continue
            # 跳过仅有内存缓存的小图：它们已在列表里当占位用
            if isinstance(thumb, PhotoCachedSize) and not any(
                is_raster_photo_size(t) and not isinstance(t, PhotoCachedSize)
                for t in (list(getattr(obj, "sizes", None) or []) + list(getattr(obj, "thumbs", None) or []))
            ):
                continue
            try:
                data = await self.client.download_media(obj, file=bytes, thumb=thumb)
            except Exception:
                continue
            if isinstance(data, (bytes, bytearray)) and data:
                item.thumb_bytes = bytes(data)
                return item.thumb_bytes
        # 最后再试消息级最大非 VideoSize 缩略图
        try:
            # 显式挑选静帧，避免 thumb=-1 拿到 VideoSize 视频预览
            thumb = None
            for obj in collect_thumb_containers(msg):
                cand = largest_raster_thumb(obj)
                if cand is not None and (thumb is None or _photo_size_area(cand) >= _photo_size_area(thumb)):
                    thumb = cand
            if thumb is not None and not isinstance(thumb, PhotoCachedSize):
                data = await self.client.download_media(msg, file=bytes, thumb=thumb)
                if isinstance(data, (bytes, bytearray)) and data:
                    item.thumb_bytes = bytes(data)
                    return item.thumb_bytes
        except Exception:
            pass
        return None

    async def resolve_entity(self, entity: Any) -> Any:
        try:
            return await self.client.get_entity(entity)
        except ValueError as exc:
            if "input entity" not in str(exc).lower() and "could not find" not in str(exc).lower():
                raise
            # 常见情况：只用 t.me/c/数字 ID，会话缓存里还没有这个 Peer
            await self.client.get_dialogs(limit=300)
            try:
                return await self.client.get_entity(entity)
            except Exception as exc2:
                raise UserFacingError(
                    "无法识别该对话。请先在官方 Telegram 中打开一次该频道/群组，然后再用本工具下载。"
                ) from exc2

    async def get_message(self, entity: Any, message_id: int) -> Any:
        msg = await self.client.get_messages(entity, ids=message_id)
        if isinstance(msg, list):
            msg = msg[0] if msg else None
        if not msg:
            raise UserFacingError("找不到这条消息，可能已被删除，或当前账号无权查看。")
        return msg

    async def resolve_link(self, parsed: ParsedLink) -> tuple[Any, Any]:
        """返回 (entity, message)。若链接带 comment，尽量解析到评论本身。"""
        entity = await self.resolve_entity(parsed.entity)
        message = await self.get_message(entity, parsed.message_id)
        if parsed.comment_id:
            try:
                entity, message = await self._resolve_comment(entity, message, parsed.comment_id)
            except UserFacingError:
                raise
            except Exception as exc:
                raise UserFacingError(
                    "无法解析评论链接。请打开讨论群中的那条评论，直接复制该消息链接再试。"
                ) from exc
        return entity, message

    async def _resolve_comment(self, entity: Any, root: Any, comment_id: int) -> tuple[Any, Any]:
        result = await self.client(GetDiscussionMessageRequest(peer=entity, msg_id=root.id))
        discussion_peer = None
        messages = getattr(result, "messages", None) or []
        if messages:
            discussion_peer = messages[0].peer_id
        if discussion_peer is None and getattr(result, "chats", None):
            discussion_peer = result.chats[0]
        if discussion_peer is None:
            raise UserFacingError("无法定位该帖的讨论群，请改用讨论群里的消息链接。")
        comment = await self.get_message(discussion_peer, comment_id)
        return discussion_peer, comment

    async def album_messages(self, entity: Any, message: Any) -> list[Any]:
        grouped = getattr(message, "grouped_id", None)
        if not grouped:
            return [message]
        start = max(1, message.id - 16)
        end = message.id + 16
        raw = await self.client.get_messages(entity, ids=list(range(start, end + 1)))
        album = [m for m in raw if m and getattr(m, "grouped_id", None) == grouped]
        album.sort(key=lambda m: m.id)
        return album or [message]

    async def iter_recent_videos(
        self,
        entity: Any,
        count: int,
        scan_limit: int = 2000,
    ):
        found = 0
        scanned = 0
        async for msg in self.client.iter_messages(entity, limit=scan_limit):
            scanned += 1
            if message_video_kind(msg):
                found += 1
                yield msg, scanned, found
                if found >= count:
                    return

    async def iter_id_range(self, entity: Any, min_id: int, max_id: int):
        lo, hi = (min_id, max_id) if min_id <= max_id else (max_id, min_id)
        # Telethon：min_id / max_id 均为开区间；从新到旧
        async for msg in self.client.iter_messages(entity, min_id=lo - 1, max_id=hi + 1):
            yield msg

    def entity_title(self, entity: Any) -> str:
        if entity is None:
            return "chat"
        title = getattr(entity, "title", None)
        if title:
            return str(title)
        username = getattr(entity, "username", None)
        if username:
            return str(username)
        parts = [getattr(entity, "first_name", None) or "", getattr(entity, "last_name", None) or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or "chat"

    async def download_message_video(
        self,
        entity: Any,
        message: Any,
        save_dir: Path,
        progress: Optional[ProgressCb] = None,
        chat_title: Optional[str] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        quality: Optional[str] = None,
        document: Any = None,
    ) -> Path:
        info = classify_video_message(message)
        if not info:
            raise UserFacingError("该消息不含视频、圆形视频或动态 GIF。")
        if not info.downloadable:
            raise UserFacingError(info.note or "这条视频无法下载。")
        title = chat_title or self.entity_title(entity)
        qualities = list_video_qualities(message)
        chosen = None
        if document is not None:
            for q in qualities:
                if q.document is document or getattr(q.document, "id", None) == getattr(document, "id", None):
                    chosen = q
                    break
            if chosen is None:
                chosen = VideoQuality(label="原画", width=0, height=0, size=getattr(document, "size", None), document=document)
        else:
            chosen = pick_video_quality(qualities, quality)
        media = chosen.document if chosen is not None else message
        filename = build_save_name(message, title)
        if chosen is not None and len(qualities) > 1 and chosen.label:
            stem = Path(filename).stem
            ext = Path(filename).suffix
            filename = f"{stem}_{chosen.label}{ext}"
        dest = unique_path(Path(save_dir), filename)
        part = dest.with_name(dest.name + ".part")

        def _cb(current: int, total: int) -> None:
            if should_cancel and should_cancel():
                raise UserFacingError("已停止。")
            if progress:
                progress(int(current or 0), int(total or 0))

        try:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
            path = await self.client.download_media(media, file=str(part), progress_callback=_cb)
            if not path:
                raise UserFacingError("下载失败：未获得文件（可能该消息没有可下载的媒体）。")
            src = Path(path)
            if dest.exists() and dest.resolve() != src.resolve():
                dest = unique_path(Path(save_dir), filename)
            src.replace(dest)
            return dest
        except Exception:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
            raise


def _normalize_phone(phone: str) -> str:
    phone = (phone or "").strip().replace(" ", "").replace("-", "")
    if not phone:
        raise UserFacingError("请输入手机号（需带国际区号，例如 +86）。")
    if re.fullmatch(r"1\d{10}", phone):
        phone = "+86" + phone
    elif re.fullmatch(r"86\d{11}", phone):
        phone = "+" + phone
    if not phone.startswith("+"):
        raise UserFacingError("手机号需以 + 和国区号开头，例如中国大陆为 +86138xxxxxxxx。也可直接填写 11 位手机号。")
    if not re.match(r"^\+\d{6,15}$", phone):
        raise UserFacingError("手机号格式不正确，请使用 +国区号+号码。")
    return phone


def session_files() -> Iterable[Path]:
    base = str(SESSION_PATH)
    for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
        yield Path(base + suffix)


def session_backup_path() -> Path:
    return Path(str(SESSION_PATH) + ".session.bak")


def persist_session(client: Any) -> None:
    """把 Telethon SQLite 会话立刻写盘，避免进程被强制结束时丢失登录。"""
    sess = getattr(client, "session", None)
    if sess is None:
        return
    save = getattr(sess, "save", None)
    if callable(save):
        save()
    conn = getattr(sess, "_conn", None) or getattr(sess, "conn", None)
    if conn is not None:
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except Exception:
            pass
        try:
            conn.commit()
        except Exception:
            pass
    backup_session()


def backup_session() -> None:
    src = Path(str(SESSION_PATH) + ".session")
    dst = session_backup_path()
    try:
        if src.is_file() and src.stat().st_size > 0:
            shutil.copy2(src, dst)
    except OSError:
        pass


def restore_session_backup() -> bool:
    src = session_backup_path()
    dst = Path(str(SESSION_PATH) + ".session")
    try:
        if src.is_file() and src.stat().st_size > 0:
            if (not dst.is_file()) or dst.stat().st_size == 0:
                shutil.copy2(src, dst)
                return True
    except OSError:
        return False
    return False


def ensure_session_file() -> None:
    dst = Path(str(SESSION_PATH) + ".session")
    if dst.is_file() and dst.stat().st_size > 0:
        return
    restore_session_backup()


def delete_session_files() -> None:
    for p in list(session_files()) + [session_backup_path()]:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def clear_session_only() -> None:
    """只清除本机登录会话，保留 api_id / 代理等配置。"""
    delete_session_files()


def clear_credentials(keep_folder: bool = True) -> None:
    """清除登录会话和 API 凭据。界面风格、代理、保存路径仍留在本机。"""
    cfg = load_config()
    data: dict = {}
    if keep_folder and cfg.get("last_save_folder"):
        data["last_save_folder"] = cfg.get("last_save_folder")
    if cfg.get("video_quality"):
        data["video_quality"] = cfg.get("video_quality")
    if cfg.get("ui_mode"):
        data["ui_mode"] = normalize_ui_mode(cfg.get("ui_mode"))
    for key in (
        "proxy_type",
        "proxy_host",
        "proxy_port",
        "proxy_user",
        "proxy_pass",
        "noticed_personal_use",
    ):
        if key in cfg and cfg.get(key) not in (None, ""):
            data[key] = cfg.get(key)
    delete_session_files()
    save_config(data)
