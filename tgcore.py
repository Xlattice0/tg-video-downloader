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
    InputMessagesFilterGif,
    InputMessagesFilterRoundVideo,
    InputMessagesFilterVideo,
    MessageMediaDocument,
    PhotoCachedSize,
    PhotoPathSize,
    PhotoSize,
    PhotoSizeProgressive,
    PhotoStrippedSize,
    VideoSize,
)
from telethon.utils import stripped_photo_to_jpg

APP_NAME = "TG视频下载"
APP_VERSION = "1.3.15"
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


def message_video_kind(msg: Any) -> Optional[str]:
    """返回 video / video_note / gif；不含视频则 None。"""
    if msg is None:
        return None
    if getattr(msg, "video_note", None):
        return "video_note"
    if getattr(msg, "video", None):
        return "video"
    if getattr(msg, "gif", None):
        return "gif"
    doc = getattr(msg, "document", None)
    if not doc:
        return None
    mime = (getattr(doc, "mime_type", None) or "").lower()
    for attr in getattr(doc, "attributes", []) or []:
        if isinstance(attr, DocumentAttributeVideo):
            return "video_note" if getattr(attr, "round_message", False) else "video"
        if isinstance(attr, DocumentAttributeAnimated) and mime.startswith("video/"):
            return "gif"
    if mime.startswith("video/"):
        return "video"
    media = getattr(msg, "media", None)
    for alt in getattr(media, "alt_documents", None) or []:
        mime = (getattr(alt, "mime_type", None) or "").lower()
        for attr in getattr(alt, "attributes", []) or []:
            if isinstance(attr, DocumentAttributeVideo):
                return "video_note" if getattr(attr, "round_message", False) else "video"
            if isinstance(attr, DocumentAttributeAnimated) and mime.startswith("video/"):
                return "gif"
        if mime.startswith("video/"):
            return "video"
    return None


def kind_label(kind: Optional[str]) -> str:
    return {
        "video": "视频",
        "video_note": "圆形视频",
        "gif": "动态 GIF / MPEG",
    }.get(kind or "", "视频")


def media_duration(msg: Any) -> Optional[int]:
    doc = getattr(msg, "document", None)
    for attr in getattr(doc, "attributes", []) or []:
        if isinstance(attr, DocumentAttributeVideo):
            dur = getattr(attr, "duration", None)
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


def document_is_video(doc: Any) -> bool:
    if doc is None:
        return False
    if document_video_attr(doc):
        return True
    mime = (getattr(doc, "mime_type", None) or "").lower()
    return mime.startswith("video/")


def iter_video_documents(msg: Any) -> list[Any]:
    """Main document plus MessageMediaDocument.alt_documents, de-duplicated."""
    docs: list[Any] = []
    seen: set[int] = set()
    media = getattr(msg, "media", None)
    main = getattr(media, "document", None) or getattr(msg, "document", None)
    alts = getattr(media, "alt_documents", None) or []
    if not isinstance(alts, (list, tuple)):
        alts = []
    for doc in (main, *alts):
        if doc is None or not document_is_video(doc):
            continue
        did = getattr(doc, "id", None)
        key = int(did) if isinstance(did, int) else id(doc)
        if key in seen:
            continue
        seen.add(key)
        docs.append(doc)
    return docs


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
    kind = message_video_kind(msg)
    if not kind:
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
        kind=kind,
        thumb_bytes=extract_cached_thumb(msg),
        message=msg,
        width=width,
        height=height,
        qualities=qualities,
    )


def video_display_title(item: VideoItem) -> str:
    cap = (item.caption or "").strip()
    if cap:
        return cap.splitlines()[0].strip() or "视频"
    if item.filename:
        return Path(item.filename).name
    return "视频"


def original_media_name(msg: Any) -> Optional[str]:
    doc = getattr(msg, "document", None)
    if not doc:
        return None
    for attr in getattr(doc, "attributes", []) or []:
        name = getattr(attr, "file_name", None)
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
    ) -> list[VideoItem]:
        """按时间倒序列出可下载视频（普通视频、圆形视频、动态 GIF）。

        使用 Telegram 的媒体过滤器，避免扫描大量纯文本消息。三类过滤器
        需分三次请求，再按消息 ID 从新到旧合并，取前 ``limit`` 条。
        ``offset_id`` 为已列出的最旧消息 ID（只取比它更旧的）；0 表示从最新开始。
        缩略图优先用缓存 / 压缩图，不在此处走网络。
        """
        peer = await self.resolve_entity(entity)
        limit = max(1, int(limit or 30))
        offset_id = int(offset_id or 0)
        filters = (
            InputMessagesFilterVideo(),
            InputMessagesFilterRoundVideo(),
            InputMessagesFilterGif(),
        )
        collected: list[Any] = []
        seen: set[int] = set()
        last_err: Optional[BaseException] = None
        any_ok = False
        for flt in filters:
            try:
                async for msg in self.client.iter_messages(
                    peer,
                    limit=limit,
                    offset_id=offset_id,
                    filter=flt,
                ):
                    if msg is None:
                        continue
                    mid = int(getattr(msg, "id", 0) or 0)
                    if not mid or mid in seen:
                        continue
                    if not message_video_kind(msg):
                        continue
                    seen.add(mid)
                    collected.append(msg)
                any_ok = True
            except (UserFacingError, FloodWaitError):
                raise
            except Exception as exc:
                # 某类过滤器在该对话不可用时，仍尽量返回其他类型
                last_err = exc
                continue
        if not any_ok and last_err is not None:
            raise last_err
        collected.sort(key=lambda m: int(getattr(m, "id", 0) or 0), reverse=True)
        items: list[VideoItem] = []
        for msg in collected[:limit]:
            item = video_item_from_message(msg)
            if item:
                items.append(item)
        return items

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
        kind = message_video_kind(message)
        if not kind:
            raise UserFacingError("该消息不含视频、圆形视频或动态 GIF。")
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
    cfg = load_config()
    folder = None
    if keep_folder:
        folder = cfg.get("last_save_folder")
    quality = cfg.get("video_quality")
    delete_session_files()
    data: dict = {}
    if folder:
        data["last_save_folder"] = folder
    if quality:
        data["video_quality"] = quality
    save_config(data)
