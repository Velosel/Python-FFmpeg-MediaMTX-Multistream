import json
import sqlite3
import sys
import re
import shutil
import socket
import ssl
import subprocess
import textwrap
import threading
import time
import webbrowser
import winsound
from collections import deque
from datetime import datetime
from pathlib import Path

import requests


BASE = Path(r"C:\RTMPStreamer")
CONFIG_FILE = BASE / "chat_config.json"
OUTPUT_FILE = BASE / "chat_overlay.txt"
CHAT_DATABASE_FILE = BASE / "chat_history.sqlite3"

# Optional native Windows desktop chat overlay.
# This uses only Win32/GDI from the Python standard library: no Tkinter,
# no browser/WebView, no third-party overlay package, and no render loop.
OVERLAY_WINDOW_ENABLED = True
OVERLAY_STATE_FILE = BASE / "chat_overlay_window.json"
OVERLAY_DEFAULT_X = 20
OVERLAY_DEFAULT_Y = 120
OVERLAY_DEFAULT_WIDTH = 430
OVERLAY_DEFAULT_HEIGHT = 330
OVERLAY_ALPHA = 220          # 0..255; background transparency only.
OVERLAY_MAX_VISIBLE_LINES = 18
OVERLAY_START_LOCKED = False
OVERLAY_NEW_MESSAGE_DURATION = 5.0
OVERLAY_BLINK_INTERVAL_MS = 300
OVERLAY_BRIGHT_GREEN = 0x0000FF00

# Audible notification for every accepted chat message. Windows controls the
# final output level through the system sound volume.
CHAT_BEEP_ENABLED = True
CHAT_BEEP_FREQUENCY = 1200
CHAT_BEEP_DURATION_MS = 180

# Native overlay history/search. The right scrollbar represents the full
# SQLite history and can be dragged through the complete message archive.
OVERLAY_SEARCH_DEBOUNCE_MS = 45
OVERLAY_SEARCH_SUGGESTIONS = 7
OVERLAY_SEARCH_ROW_HEIGHT = 20
OVERLAY_SEARCH_MAX_TEXT = 70
OVERLAY_SEARCH_TIMER_ID = 1702
OVERLAY_SCROLL_LINES_PER_WHEEL = 3
OVERLAY_SCROLLBAR_WIDTH = 16

# Global hotkeys while the aggregator is running:
#   Ctrl+Alt+L = lock/unlock position + click-through mode
#   Ctrl+Alt+H = hide/show the overlay
OVERLAY_REGISTER_HOTKEYS = True

MAX_MESSAGES = 18
MAX_LINE_LENGTH = 42

# The FFmpeg chat panel (chat_overlay.txt) is small and MultistreamApp.py
# anchors its text to the bottom of the panel. The file must therefore never
# hold more lines than fit in the panel, or the oldest lines would run out of
# it. Keep STREAM_PANEL_MAX_LINES in sync with the panel height there.
STREAM_PANEL_MAX_LINES = 11
STREAM_PANEL_MAX_LINES_PER_MESSAGE = 3  # longer messages end with an ellipsis

# Validate/refresh Twitch at startup and periodically.
TOKEN_VALIDATE_INTERVAL = 3600
TOKEN_REFRESH_MARGIN = 600  # refresh if <= 10 minutes remain

# Twitch IRC keepalive: Twitch sends PING roughly every 5 minutes. If the
# socket is silent, ping Twitch ourselves and reconnect if nothing comes back.
TWITCH_PING_AFTER = 60      # seconds of silence before we send a PING
TWITCH_IDLE_TIMEOUT = 150   # seconds of silence before we reconnect

# Give up on the interactive Twitch CLI Device Code Flow after this long.
DCF_TIMEOUT = 600

messages = deque(maxlen=MAX_MESSAGES)
lock = threading.Lock()  # guards `messages` AND the overlay file write
db_lock = threading.Lock()
chat_db = None
chat_session_id = None
config_lock = threading.Lock()
twitch_refresh_lock = threading.Lock()
twitch_access_token = ""
twitch_access_token_lock = threading.Lock()

# Created by main() on Windows. Chat worker threads only call notify_update(),
# so the Win32 window itself always runs on its own message-loop thread.
native_overlay = None
modified_message_ids = set()  # SQLite row IDs whose text was edited.


class TwitchTransientError(RuntimeError):
    """A temporary network/server problem. The credentials are not at fault,
    so this must never trigger re-authorization (Device Code Flow)."""


class YouTubeConfigError(RuntimeError):
    """A YouTube configuration problem that retrying will not fix."""


import ctypes
from ctypes import wintypes

# =============================================================
# Native Windows desktop overlay
# =============================================================

class NativeChatOverlay:
    """
    Very-low-overhead Windows chat overlay.

    Design goals:
      - One borderless, always-on-top native Win32 window.
      - No timer/render loop: it redraws only when chat changes.
      - No extra pip dependency; uses ctypes + GDI only.
      - Drag anywhere while unlocked.
      - Ctrl+Alt+L locks it in place and makes it click-through.
      - Ctrl+Alt+H hides/shows it.
      - Position/size/lock/visibility are persisted separately from
        chat_config.json.
    """

    WM_APP_REFRESH = 0x8001
    WM_TIMER = 0x0113
    BLINK_TIMER_ID = 1701
    HOTKEY_LOCK = 1001
    HOTKEY_VISIBILITY = 1002

    # Win32 constants.
    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    WS_THICKFRAME = 0x00040000  # native resize border
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020

    GWL_EXSTYLE = -20
    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_HIDEWINDOW = 0x0080

    SW_SHOWNOACTIVATE = 4
    SW_HIDE = 0

    LWA_ALPHA = 0x00000002

    WM_PAINT = 0x000F
    WM_ERASEBKGND = 0x0014
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_MOUSEMOVE = 0x0200
    WM_NCHITTEST = 0x0084
    WM_HOTKEY = 0x0312
    WM_EXITSIZEMOVE = 0x0232
    WM_MOUSEACTIVATE = 0x0021
    WM_SIZE = 0x0005
    WM_VSCROLL = 0x0115
    WM_MOUSEWHEEL = 0x020A
    WM_COMMAND = 0x0111
    WM_SETFONT = 0x0030
    WM_GETTEXTLENGTH = 0x000E
    WM_GETTEXT = 0x000D

    EM_SETCUEBANNER = 0x1501
    EN_CHANGE = 0x0300
    EDIT_CONTROL_ID = 1101

    WS_CHILD = 0x40000000
    WS_VSCROLL = 0x00200000
    ES_LEFT = 0x0000
    ES_AUTOHSCROLL = 0x0080
    ES_NOHIDESEL = 0x0100
    WS_EX_CLIENTEDGE = 0x00000200

    SB_VERT = 1
    SB_LINEUP = 0
    SB_LINEDOWN = 1
    SB_PAGEUP = 2
    SB_PAGEDOWN = 3
    SB_THUMBPOSITION = 4
    SB_THUMBTRACK = 5
    SB_TOP = 6
    SB_BOTTOM = 7
    SB_ENDSCROLL = 8

    SIF_RANGE = 0x0001
    SIF_PAGE = 0x0002
    SIF_POS = 0x0004
    SIF_TRACKPOS = 0x0010
    SIF_ALL = SIF_RANGE | SIF_PAGE | SIF_POS | SIF_TRACKPOS

    HTCLIENT = 1
    HTLEFT = 10
    HTRIGHT = 11
    HTTOP = 12
    HTTOPLEFT = 13
    HTTOPRIGHT = 14
    HTBOTTOM = 15
    HTBOTTOMLEFT = 16
    HTBOTTOMRIGHT = 17
    HTTRANSPARENT = -1
    MA_NOACTIVATE = 3

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000

    IDC_ARROW = 32512

    def __init__(self):
        self.enabled = sys.platform == "win32"
        self.thread = None
        self.hwnd = None
        self.class_atom = None
        self.class_name = "RavaelvChatOverlayWindow"
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._pending_lock = threading.Lock()
        self._refresh_pending = False
        self._dragging = False
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._locked = bool(OVERLAY_START_LOCKED)
        self._visible = True
        self._x = int(OVERLAY_DEFAULT_X)
        self._y = int(OVERLAY_DEFAULT_Y)
        self._width = int(OVERLAY_DEFAULT_WIDTH)
        self._height = int(OVERLAY_DEFAULT_HEIGHT)
        self._resize_border = 8
        self._font = None
        self._header_font = None
        self._bold_font = None
        self._search_hwnd = None
        self._search_font = None
        self._scroll_hwnd = None
        self._search_query = ""
        self._search_suggestions = []
        self._search_suggestion_hitboxes = []
        self._scroll_offset = 0
        self._history_total = 0
        self._history_page_size = 1
        self._blink_on = True
        self._subclass_proc = None
        self._wndproc = None
        self._user32 = None
        self._gdi32 = None
        self._kernel32 = None
        self._load_state()

    def _load_state(self):
        try:
            data = json.loads(OVERLAY_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for key, default in (
            ("x", self._x),
            ("y", self._y),
            ("width", self._width),
            ("height", self._height),
        ):
            try:
                value = int(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            if key in ("width", "height"):
                value = max(260, min(value, 1000))
            setattr(self, f"_{key}", value)

        if "locked" in data:
            self._locked = bool(data["locked"])
        if "visible" in data:
            self._visible = bool(data["visible"])

    def _save_state(self):
        state = {
            "x": self._x,
            "y": self._y,
            "width": self._width,
            "height": self._height,
            "locked": self._locked,
            "visible": self._visible,
        }
        try:
            atomic_write_text(
                OVERLAY_STATE_FILE,
                json.dumps(state, indent=4),
            )
        except OSError as e:
            print(f"Chat overlay state save failed: {e}")

    def start(self):
        if not self.enabled:
            print("Native chat overlay disabled: Windows Win32 is required.")
            return False

        self.thread = threading.Thread(
            target=self._thread_main,
            name="NativeChatOverlay",
            daemon=True,
        )
        self.thread.start()
        self._ready_event.wait(timeout=5)

        if not self.hwnd:
            print("Native chat overlay could not be created.")
            return False

        print(
            "Native chat overlay enabled. "
            "Ctrl+Alt+L = lock/unlock, Ctrl+Alt+H = hide/show."
        )
        return True

    def stop(self):
        if not self.enabled or not self.hwnd:
            return
        self._stop_event.set()
        try:
            self._user32.PostMessageW(self.hwnd, 0x0012, 0, 0)  # WM_QUIT
        except Exception:
            pass

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def notify_update(self):
        """Request one coalesced repaint from any chat worker thread."""
        if not self.enabled or not self.hwnd or not self._visible:
            return

        with self._pending_lock:
            if self._refresh_pending:
                return
            self._refresh_pending = True

        try:
            self._user32.PostMessageW(
                self.hwnd,
                self.WM_APP_REFRESH,
                0,
                0,
            )
        except Exception:
            with self._pending_lock:
                self._refresh_pending = False

    def _snapshot_lines(self):
        """Return newest messages first, with per-message highlight state.

        Each returned entry is (text, is_highlighted, blink_visible). New messages remain
        highlighted for OVERLAY_NEW_MESSAGE_DURATION seconds. The overlay
        itself stores no timestamps; chat persistence is handled by SQLite.
        """
        now = time.monotonic()
        blink_on = self._blink_on

        with lock:
            snapshot = list(messages)

        rendered = []
        visible_lines = 0

        # Newest message first. This makes a new message appear at the top
        # and pushes older messages downward.
        for platform, username, message, created_at, message_id, highlight_until in reversed(snapshot):
            prefix = {
                "T": "[T]",
                "Y": "[YT]",
                "TT": "[TT]",
            }.get(platform, "[?]")
            clean = " ".join(str(message).split())
            user = " ".join(str(username).split())
            if not clean:
                continue

            full = f"{prefix} {user}: {clean}"
            wrapped = textwrap.wrap(
                full,
                width=MAX_LINE_LENGTH,
                break_long_words=False,
                break_on_hyphens=False,
            ) or [""]

            if visible_lines + len(wrapped) > OVERLAY_MAX_VISIBLE_LINES:
                remaining = OVERLAY_MAX_VISIBLE_LINES - visible_lines
                if remaining <= 0:
                    break
                wrapped = wrapped[:remaining]

            modified = message_id in modified_message_ids
            highlighted = now < highlight_until
            for line in wrapped:
                rendered.append((line, highlighted, highlighted and blink_on, modified))

            visible_lines += len(wrapped)
            if visible_lines >= OVERLAY_MAX_VISIBLE_LINES:
                break

        if not rendered:
            rendered = [("Waiting for messages...", False, False, False)]

        return rendered

    def _has_active_highlights(self):
        now = time.monotonic()
        with lock:
            return any(
                now < item[5]
                for item in messages
            )

    def _start_blink_timer(self):
        if not self.hwnd or not self._has_active_highlights():
            return
        self._user32.SetTimer(
            self.hwnd,
            self.BLINK_TIMER_ID,
            OVERLAY_BLINK_INTERVAL_MS,
            None,
        )

    def _stop_blink_timer(self):
        if self.hwnd:
            self._user32.KillTimer(
                self.hwnd,
                self.BLINK_TIMER_ID,
            )

    @staticmethod
    def _loword(value):
        return value & 0xFFFF

    @staticmethod
    def _hiword(value):
        return (value >> 16) & 0xFFFF

    def _set_click_through(self, click_through):
        if not self.hwnd:
            return

        exstyle = self._user32.GetWindowLongPtrW(
            self.hwnd,
            self.GWL_EXSTYLE,
        )

        if click_through:
            exstyle |= self.WS_EX_TRANSPARENT
        else:
            exstyle &= ~self.WS_EX_TRANSPARENT

        self._user32.SetWindowLongPtrW(
            self.hwnd,
            self.GWL_EXSTYLE,
            exstyle,
        )

    def _apply_lock_state(self):
        self._set_click_through(self._locked)
        if self._search_hwnd:
            self._user32.EnableWindow(self._search_hwnd, not self._locked)
        if self._scroll_hwnd:
            self._user32.EnableWindow(self._scroll_hwnd, not self._locked)
        if self.hwnd:
            self._user32.SetWindowPos(
                self.hwnd,
                self.HWND_TOPMOST,
                self._x,
                self._y,
                self._width,
                self._height,
                self.SWP_NOACTIVATE,
            )
            self._user32.InvalidateRect(self.hwnd, None, False)

    def _toggle_lock(self):
        self._locked = not self._locked
        self._apply_lock_state()
        self._save_state()
        state = "locked" if self._locked else "unlocked"
        print(f"Chat overlay: {state}.")

    def _toggle_visibility(self):
        self._visible = not self._visible
        if self.hwnd:
            self._user32.ShowWindow(
                self.hwnd,
                self.SW_SHOWNOACTIVATE if self._visible else self.SW_HIDE,
            )
            if self._visible:
                self._position_search_box()
                self._history_total = self._history_count()
                self._update_scrollbar(force_count=False)
                self._user32.InvalidateRect(self.hwnd, None, False)
        self._save_state()
        print(
            "Chat overlay: "
            + ("shown." if self._visible else "hidden.")
        )

    def _begin_drag(self):
        if self._locked or not self.hwnd:
            return

        point = self._POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        rect = self._RECT()
        self._user32.GetWindowRect(self.hwnd, ctypes.byref(rect))

        self._drag_offset_x = point.x - rect.left
        self._drag_offset_y = point.y - rect.top
        self._dragging = True
        self._user32.SetCapture(self.hwnd)

    def _drag_move(self):
        if not self._dragging or self._locked or not self.hwnd:
            return

        point = self._POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        self._x = int(point.x - self._drag_offset_x)
        self._y = int(point.y - self._drag_offset_y)

        self._user32.SetWindowPos(
            self.hwnd,
            self.HWND_TOPMOST,
            self._x,
            self._y,
            0,
            0,
            self.SWP_NOSIZE | self.SWP_NOACTIVATE,
        )

    def _end_drag(self):
        if not self._dragging:
            return
        self._dragging = False
        self._user32.ReleaseCapture()
        self._save_state()

    def _get_search_text(self):
        if not self._search_hwnd or not self._user32:
            return self._search_query

        try:
            length = int(self._user32.GetWindowTextLengthW(self._search_hwnd))
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(
                self._search_hwnd,
                buffer,
                length + 1,
            )
            return " ".join(buffer.value.split())
        except Exception:
            return self._search_query

    def _search_suggestions_query(self):
        query = self._search_query.strip()
        if not query:
            return []

        pattern = f"%{query}%"
        seen = set()
        results = []
        with db_lock:
            if chat_db is None:
                return []
            rows = chat_db.execute(
                """
                SELECT username, message, platform, created_at
                FROM messages
                WHERE username LIKE ? COLLATE NOCASE
                   OR message LIKE ? COLLATE NOCASE
                   OR platform LIKE ? COLLATE NOCASE
                ORDER BY id DESC
                LIMIT 100
                """,
                (pattern, pattern, pattern),
            ).fetchall()

        # React-like suggestions: recent unique users/messages, with the
        # actual matching text highlighted by the native renderer.
        for username, message, platform, _created_at in rows:
            user = " ".join(str(username).split())
            text = " ".join(str(message).split())
            if not user or not text:
                continue

            candidates = [
                ("user", user, platform),
                ("text", text, platform),
            ]
            for kind, value, source_platform in candidates:
                key = (kind, value.casefold())
                if key in seen:
                    continue
                if query.casefold() not in value.casefold():
                    continue
                seen.add(key)
                results.append((kind, value[:OVERLAY_SEARCH_MAX_TEXT], source_platform))
                if len(results) >= OVERLAY_SEARCH_SUGGESTIONS:
                    return results

        return results

    def _refresh_search_suggestions(self):
        self._search_suggestions = self._search_suggestions_query()
        self._search_suggestion_hitboxes = []

    def _draw_highlighted_text(self, hdc, text, x, y, right, height, query):
        if not query:
            self._user32.DrawTextW(
                hdc, text, -1,
                ctypes.byref(self._RECT(x, y, right, y + height)),
                0x00000020 | 0x00000800,
            )
            return

        lower_text = text.casefold()
        lower_query = query.casefold()
        start = lower_text.find(lower_query)
        if start < 0:
            self._user32.DrawTextW(
                hdc, text, -1,
                ctypes.byref(self._RECT(x, y, right, y + height)),
                0x00000020 | 0x00000800,
            )
            return

        before = text[:start]
        match = text[start:start + len(query)]
        after = text[start + len(query):]

        self._gdi32.SetTextColor(hdc, 0x00F3F3F3)
        r = self._RECT(x, y, right, y + height)
        self._user32.DrawTextW(hdc, before, -1, ctypes.byref(r), 0x00000020 | 0x00000800)

        size = self._SIZE()
        self._gdi32.GetTextExtentPoint32W(
            hdc, before, len(before), ctypes.byref(size)
        )
        mx = x + size.cx

        self._gdi32.SetTextColor(hdc, OVERLAY_BRIGHT_GREEN)
        self._gdi32.SelectObject(hdc, self._bold_font)
        self._user32.DrawTextW(
            hdc, match, -1,
            ctypes.byref(self._RECT(mx, y, right, y + height)),
            0x00000020 | 0x00000800,
        )
        self._gdi32.SelectObject(hdc, self._font)

        self._gdi32.GetTextExtentPoint32W(
            hdc, match, len(match), ctypes.byref(size)
        )
        ax = mx + size.cx
        self._gdi32.SetTextColor(hdc, 0x00F3F3F3)
        self._user32.DrawTextW(
            hdc, after, -1,
            ctypes.byref(self._RECT(ax, y, right, y + height)),
            0x00000020 | 0x00000800,
        )

    def _render_search_suggestions(self, rect):
        self._search_suggestion_hitboxes = []
        if not self._search_query.strip() or not self._search_suggestions:
            return

        left = 8
        top = 66
        right = max(left + 100, rect.right - OVERLAY_SCROLLBAR_WIDTH - 8)
        row_h = OVERLAY_SEARCH_ROW_HEIGHT
        bottom = top + row_h * len(self._search_suggestions)

        bg = self._gdi32.CreateSolidBrush(0x00202020)
        self._user32.FillRect(hdc := self._current_hdc, ctypes.byref(self._RECT(left, top, right, bottom)), bg)
        self._gdi32.DeleteObject(bg)

        query = self._search_query.strip()
        for index, (kind, value, platform) in enumerate(self._search_suggestions):
            y = top + index * row_h
            self._search_suggestion_hitboxes.append((left, y, right, y + row_h, kind, value))

            # Subtle alternating row surface. The matching substring is
            # rendered bright green + bold, similar to a React search UI.
            if index % 2 == 0:
                row_brush = self._gdi32.CreateSolidBrush(0x00272727)
                self._user32.FillRect(hdc, ctypes.byref(self._RECT(left, y, right, y + row_h)), row_brush)
                self._gdi32.DeleteObject(row_brush)

            self._gdi32.SelectObject(hdc, self._font)
            self._draw_highlighted_text(
                hdc,
                f"{platform}  {kind}: {value}",
                left + 8,
                y + 2,
                right - 8,
                row_h - 2,
                query,
            )

    def _select_search_suggestion(self, x, y):
        for left, top, right, bottom, _kind, value in self._search_suggestion_hitboxes:
            if left <= x < right and top <= y < bottom:
                if self._search_hwnd:
                    self._user32.SetWindowTextW(self._search_hwnd, value)
                    self._user32.SetFocus(self._search_hwnd)
                self._search_query = value
                self._search_suggestions = []
                self._search_suggestion_hitboxes = []
                self._scroll_offset = 0
                self._history_total = self._history_count()
                self._update_scrollbar(force_count=False)
                self._user32.InvalidateRect(self.hwnd, None, False)
                self._user32.UpdateWindow(self.hwnd)
                return True
        return False

    def _history_count(self):
        query = self._search_query.strip()
        with db_lock:
            if chat_db is None:
                return 0
            if not query:
                row = chat_db.execute(
                    "SELECT COUNT(*) FROM messages"
                ).fetchone()
            else:
                pattern = f"%{query}%"
                row = chat_db.execute(
                    """
                    SELECT COUNT(*)
                    FROM messages
                    WHERE username LIKE ? COLLATE NOCASE
                       OR message LIKE ? COLLATE NOCASE
                       OR platform LIKE ? COLLATE NOCASE
                    """,
                    (pattern, pattern, pattern),
                ).fetchone()
        return int(row[0] if row else 0)

    def _history_rows(self, offset, limit):
        query = self._search_query.strip()
        offset = max(0, int(offset))
        limit = max(1, int(limit))

        with db_lock:
            if chat_db is None:
                return []

            if not query:
                cursor = chat_db.execute(
                    """
                    SELECT id, platform, username, message, created_at, is_modified
                    FROM messages
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
            else:
                pattern = f"%{query}%"
                cursor = chat_db.execute(
                    """
                    SELECT id, platform, username, message, created_at, is_modified
                    FROM messages
                    WHERE username LIKE ? COLLATE NOCASE
                       OR message LIKE ? COLLATE NOCASE
                       OR platform LIKE ? COLLATE NOCASE
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (pattern, pattern, pattern, limit, offset),
                )

            return cursor.fetchall()

    def _highlight_map(self):
        with lock:
            return {
                item[4]: item[5]
                for item in messages
                if len(item) >= 6
            }

    def _calculate_visible_message_page(self, client_height):
        header_height = 32
        search_y = header_height + 5
        search_height = 25
        content_y = search_y + search_height + 8
        line_height = 17
        available = max(1, client_height - content_y - 8)
        return max(1, available // line_height)

    def _update_scrollbar(self, client_height=None, force_count=False):
        if not self.hwnd or not self._scroll_hwnd:
            return

        if client_height is None:
            rect = self._RECT()
            self._user32.GetClientRect(self.hwnd, ctypes.byref(rect))
            client_height = rect.bottom

        self._history_page_size = self._calculate_visible_message_page(
            client_height
        )

        if force_count or self._history_total <= 0:
            self._history_total = self._history_count()

        max_offset = max(
            0,
            self._history_total - self._history_page_size,
        )
        self._scroll_offset = max(
            0,
            min(self._scroll_offset, max_offset),
        )

        # Keep a real scrollbar control permanently visible, even when the
        # history currently fits inside the viewport. When there is not yet
        # enough history to scroll, the thumb simply fills the track.
        si = self._SCROLLINFO()
        si.cbSize = ctypes.sizeof(self._SCROLLINFO)
        si.fMask = self.SIF_RANGE | self.SIF_PAGE | self.SIF_POS
        si.nMin = 0
        si.nMax = max(1, self._history_total - 1)
        si.nPage = max(1, self._history_page_size)
        si.nPos = min(self._scroll_offset, max_offset)
        self._user32.SetScrollInfo(
            self._scroll_hwnd,
            self.SB_VERT,
            ctypes.byref(si),
            True,
        )

        # Do not disable the control: the user should always see the sidebar.
        self._user32.EnableWindow(
            self._scroll_hwnd,
            not self._locked,
        )

        rect = self._RECT()
        self._user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        content_y = 32 + 5 + 25 + 8
        self._user32.SetWindowPos(
            self._scroll_hwnd,
            None,
            max(0, rect.right - OVERLAY_SCROLLBAR_WIDTH),
            content_y,
            OVERLAY_SCROLLBAR_WIDTH,
            max(1, rect.bottom - content_y),
            self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
        )

    def _set_scroll_offset(self, value):
        # The scrollbar is virtualized: the thumb represents the entire
        # SQLite archive, while only the visible page is queried/rendered.
        # This lets the history grow indefinitely without loading the whole
        # database into RAM.
        max_offset = max(
            0,
            self._history_total - self._history_page_size,
        )
        self._scroll_offset = max(
            0,
            min(int(value), max_offset),
        )
        self._update_scrollbar(force_count=False)
        self._user32.InvalidateRect(self.hwnd, None, False)
        self._user32.UpdateWindow(self.hwnd)

    def _handle_vscroll(self, code):
        if not self.hwnd:
            return

        max_offset = max(
            0,
            self._history_total - self._history_page_size,
        )
        new_offset = self._scroll_offset

        if code == self.SB_LINEUP:
            new_offset -= 1
        elif code == self.SB_LINEDOWN:
            new_offset += 1
        elif code == self.SB_PAGEUP:
            new_offset -= max(1, self._history_page_size)
        elif code == self.SB_PAGEDOWN:
            new_offset += max(1, self._history_page_size)
        elif code == self.SB_TOP:
            new_offset = 0
        elif code == self.SB_BOTTOM:
            new_offset = max_offset
        elif code in {self.SB_THUMBTRACK, self.SB_THUMBPOSITION}:
            si = self._SCROLLINFO()
            si.cbSize = ctypes.sizeof(self._SCROLLINFO)
            si.fMask = self.SIF_TRACKPOS | self.SIF_POS
            scroll_target = self._scroll_hwnd or self.hwnd
            self._user32.GetScrollInfo(
                scroll_target,
                self.SB_VERT,
                ctypes.byref(si),
            )
            new_offset = int(
                si.nTrackPos
                if code == self.SB_THUMBTRACK
                else si.nPos
            )
        else:
            return

        new_offset = max(0, min(new_offset, max_offset))
        if new_offset != self._scroll_offset:
            self._scroll_offset = new_offset
            self._update_scrollbar(force_count=False)
            self._user32.InvalidateRect(self.hwnd, None, False)
            self._user32.UpdateWindow(self.hwnd)

    def _handle_mousewheel(self, delta):
        # Windows commonly reports +/-120 per wheel notch.
        steps = max(
            1,
            int(abs(delta) / 120) * OVERLAY_SCROLL_LINES_PER_WHEEL,
        )
        self._handle_vscroll(
            self.SB_LINEUP if delta > 0 else self.SB_LINEDOWN
        )
        for _ in range(max(0, steps - 1)):
            self._handle_vscroll(
                self.SB_LINEUP if delta > 0 else self.SB_LINEDOWN
            )

    def _apply_search(self):
        self._search_query = self._get_search_text()
        self._refresh_search_suggestions()
        self._scroll_offset = 0
        self._history_total = self._history_count()
        self._update_scrollbar(force_count=False)
        if self.hwnd:
            self._user32.InvalidateRect(self.hwnd, None, False)
            self._user32.UpdateWindow(self.hwnd)

    def _schedule_search(self):
        if not self.hwnd:
            return
        self._user32.KillTimer(
            self.hwnd,
            OVERLAY_SEARCH_TIMER_ID,
        )
        self._user32.SetTimer(
            self.hwnd,
            OVERLAY_SEARCH_TIMER_ID,
            OVERLAY_SEARCH_DEBOUNCE_MS,
            None,
        )

    def _position_search_box(self):
        if not self._search_hwnd:
            return
        rect = self._RECT()
        self._user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        width = max(
            120,
            rect.right - 16 - OVERLAY_SCROLLBAR_WIDTH,
        )
        self._user32.SetWindowPos(
            self._search_hwnd,
            None,
            8,
            38,
            width,
            25,
            self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
        )

    def _render_history(self, rect):
        # Query only enough rows to fill the current viewport. The database
        # remains the source of truth, so scroll/search can reach the entire
        # archive without loading all messages into RAM.
        line_height = 17
        header_height = 32
        search_y = header_height + 5
        search_height = 25
        content_y = search_y + search_height + 8
        available_lines = max(
            1,
            (rect.bottom - content_y - 8) // line_height,
        )
        query_limit = max(20, available_lines * 2)
        rows = self._history_rows(
            self._scroll_offset,
            query_limit,
        )

        highlights = self._highlight_map()
        now = time.monotonic()
        rendered = []
        visible_lines = 0

        for row_id, platform, username, message, _created_at, is_modified in rows:
            prefix = {
                "T": "[T]",
                "Y": "[YT]",
                "TT": "[TT]",
            }.get(platform, "[?]")
            clean = " ".join(str(message).split())
            user = " ".join(str(username).split())
            if not clean:
                continue

            full = f"{prefix} {user}: {clean}"
            wrapped = textwrap.wrap(
                full,
                width=MAX_LINE_LENGTH,
                break_long_words=False,
                break_on_hyphens=False,
            ) or [""]

            modified = bool(is_modified) or row_id in modified_message_ids
            highlighted = now < highlights.get(row_id, 0.0)
            for line in wrapped:
                if visible_lines >= available_lines:
                    break
                rendered.append(
                    (
                        line,
                        highlighted,
                        highlighted and self._blink_on,
                        modified,
                    )
                )
                visible_lines += 1

            if visible_lines >= available_lines:
                break

        if not rendered:
            if self._search_query:
                rendered = [("No matching messages.", False, False, False)]
            elif self._history_total <= 0:
                rendered = [("Waiting for messages...", False, False, False)]
            else:
                rendered = [("No messages in this position.", False, False, False)]

        y = content_y
        for line, highlighted, blink_visible, modified in rendered:
            selected_font = self._bold_font if (highlighted or modified) else self._font
            self._gdi32.SelectObject(self._current_hdc, selected_font)
            if modified:
                # Persistent red state means: this message was edited.
                color = 0x000000FF
            elif highlighted:
                color = (
                    OVERLAY_BRIGHT_GREEN
                    if blink_visible
                    else 0x00F3F3F3
                )
            else:
                color = 0x00F3F3F3
            self._gdi32.SetTextColor(self._current_hdc, color)
            text_rect = self._RECT(
                12,
                y,
                max(12, rect.right - OVERLAY_SCROLLBAR_WIDTH - 6),
                y + line_height,
            )
            self._user32.DrawTextW(
                self._current_hdc,
                line,
                -1,
                ctypes.byref(text_rect),
                0x00000020 | 0x00000800,
            )
            y += line_height

    def _render(self):
        if not self.hwnd or not self._visible:
            return

        ps = self._PAINTSTRUCT()
        hdc = self._user32.BeginPaint(self.hwnd, ctypes.byref(ps))
        if not hdc:
            return

        try:
            rect = self._RECT()
            self._user32.GetClientRect(self.hwnd, ctypes.byref(rect))

            # Dark translucent panel.
            bg = self._gdi32.CreateSolidBrush(0x00141414)
            self._user32.FillRect(hdc, ctypes.byref(rect), bg)
            self._gdi32.DeleteObject(bg)

            # Header.
            header_height = 32
            header_rect = self._RECT(0, 0, rect.right, header_height)
            header_brush = self._gdi32.CreateSolidBrush(0x00BFEFFF)
            self._user32.FillRect(
                hdc,
                ctypes.byref(header_rect),
                header_brush,
            )
            self._gdi32.DeleteObject(header_brush)

            old_font = self._gdi32.SelectObject(hdc, self._header_font)
            self._gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
            self._gdi32.SetTextColor(hdc, 0x00000000)

            header_text_rect = self._RECT(12, 5, rect.right - 12, header_height)
            self._user32.DrawTextW(
                hdc,
                "LIVE CHAT",
                -1,
                ctypes.byref(header_text_rect),
                0x00000020 | 0x00000800 | 0x00000004,  # SINGLELINE | NOPREFIX | VCENTER
            )

            self._current_hdc = hdc
            # Paint the history first, then paint the search suggestions on
            # top of it.  The suggestion dropdown intentionally overlaps the
            # history area; reversing this order prevents history text from
            # showing through / over the suggestion rows.
            self._render_history(rect)
            self._render_search_suggestions(rect)
            self._current_hdc = None

            self._gdi32.SelectObject(hdc, old_font)
        finally:
            self._user32.EndPaint(self.hwnd, ctypes.byref(ps))

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_APP_REFRESH:
            # Start a low-frequency timer only while there are active new
            # messages. No timer runs when the overlay is idle.
            self._start_blink_timer()
            self._blink_on = True

            # Do not call BeginPaint() directly from a custom application
            # message. BeginPaint() is intended for WM_PAINT. Instead, mark
            # the client area dirty and let Windows deliver WM_PAINT; this
            # fixes the case where the overlay showed its initial frame but
            # never refreshed when a new Twitch/YouTube message arrived.
            with self._pending_lock:
                self._refresh_pending = False

            # Every new live message must appear at the top. Return the live
            # view to the newest message regardless of the previous history
            # position. Older messages remain available by dragging the right
            # scrollbar downward through the complete SQLite archive.
            self._scroll_offset = 0

            self._history_total = self._history_count()
            self._update_scrollbar(force_count=False)

            if self.hwnd and self._visible:
                self._user32.InvalidateRect(
                    self.hwnd,
                    None,
                    False,
                )
                self._user32.UpdateWindow(self.hwnd)
            return 0

        if msg == self.WM_TIMER:
            if int(wparam) == self.BLINK_TIMER_ID:
                if self._has_active_highlights():
                    self._blink_on = not self._blink_on
                    self._user32.InvalidateRect(self.hwnd, None, False)
                    self._user32.UpdateWindow(self.hwnd)
                else:
                    self._stop_blink_timer()
                    self._blink_on = True
                    self._user32.InvalidateRect(self.hwnd, None, False)
                    self._user32.UpdateWindow(self.hwnd)
            elif int(wparam) == OVERLAY_SEARCH_TIMER_ID:
                self._user32.KillTimer(
                    self.hwnd,
                    OVERLAY_SEARCH_TIMER_ID,
                )
                self._apply_search()
            return 0

        if msg == self.WM_SIZE:
            self._position_search_box()
            self._update_scrollbar(
                client_height=self._hiword(lparam),
                force_count=False,
            )
            self._user32.InvalidateRect(self.hwnd, None, False)
            return 0

        if msg == self.WM_VSCROLL:
            self._handle_vscroll(self._loword(wparam))
            return 0

        if msg == self.WM_MOUSEWHEEL:
            delta = ctypes.c_short(self._hiword(wparam)).value
            self._handle_mousewheel(delta)
            return 0

        if msg == self.WM_COMMAND:
            control_id = self._loword(wparam)
            notification = self._hiword(wparam)
            if (
                control_id == self.EDIT_CONTROL_ID
                and notification == self.EN_CHANGE
            ):
                self._schedule_search()
                return 0
            return 0

        if msg == self.WM_HOTKEY:
            hotkey_id = int(wparam)
            if hotkey_id == self.HOTKEY_LOCK:
                self._toggle_lock()
            elif hotkey_id == self.HOTKEY_VISIBILITY:
                self._toggle_visibility()
            return 0

        if msg == self.WM_LBUTTONDOWN:
            point_x = ctypes.c_short(self._loword(lparam)).value
            point_y = ctypes.c_short(self._hiword(lparam)).value
            if self._select_search_suggestion(point_x, point_y):
                return 0
            self._begin_drag()
            return 0

        if msg == self.WM_MOUSEMOVE:
            if self._dragging:
                self._drag_move()
            return 0

        if msg == self.WM_LBUTTONUP:
            self._end_drag()
            return 0

        if msg == self.WM_NCHITTEST:
            if self._locked:
                return self.HTTRANSPARENT

            # Return native sizing hit-tests around the edges so Windows can
            # perform the actual resize operation. The center remains a
            # client area, which our drag handler uses to move the overlay.
            # WM_NCHITTEST packs signed 16-bit screen coordinates into
            # LPARAM. Convert explicitly so overlays on a monitor with a
            # negative desktop coordinate also resize correctly.
            x = ctypes.c_short(self._loword(lparam)).value
            y = ctypes.c_short(self._hiword(lparam)).value

            point = self._POINT(x, y)
            self._user32.ScreenToClient(
                self.hwnd,
                ctypes.byref(point),
            )

            rect = self._RECT()
            self._user32.GetClientRect(
                self.hwnd,
                ctypes.byref(rect),
            )

            border = self._resize_border
            left = point.x < border
            right = point.x >= rect.right - border
            top = point.y < border
            bottom = point.y >= rect.bottom - border

            if top and left:
                return self.HTTOPLEFT
            if top and right:
                return self.HTTOPRIGHT
            if bottom and left:
                return self.HTBOTTOMLEFT
            if bottom and right:
                return self.HTBOTTOMRIGHT
            if left:
                return self.HTLEFT
            if right:
                return self.HTRIGHT
            if top:
                return self.HTTOP
            if bottom:
                return self.HTBOTTOM

            return self.HTCLIENT

        if msg == self.WM_MOUSEACTIVATE:
            # The search EDIT control must be able to activate and receive
            # keyboard focus. Returning MA_NOACTIVATE here prevents a mouse
            # click from giving the native EDIT control focus.
            return 1  # MA_ACTIVATE

        if msg == self.WM_EXITSIZEMOVE:
            if self.hwnd:
                rect = self._RECT()
                self._user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
                self._x = rect.left
                self._y = rect.top
                self._width = max(260, rect.right - rect.left)
                self._height = max(180, rect.bottom - rect.top)
                self._save_state()
                self._user32.InvalidateRect(
                    self.hwnd,
                    None,
                    False,
                )
                self._user32.UpdateWindow(self.hwnd)
            return 0

        if msg == self.WM_PAINT:
            self._render()
            return 0

        if msg == self.WM_ERASEBKGND:
            return 1

        if msg == self.WM_CLOSE:
            self._save_state()
            self._user32.DestroyWindow(hwnd)
            return 0

        if msg == self.WM_DESTROY:
            self._stop_blink_timer()
            if OVERLAY_REGISTER_HOTKEYS:
                self._user32.UnregisterHotKey(
                    hwnd,
                    self.HOTKEY_LOCK,
                )
                self._user32.UnregisterHotKey(
                    hwnd,
                    self.HOTKEY_VISIBILITY,
                )

            if self._font:
                self._gdi32.DeleteObject(self._font)
                self._font = None
            if self._header_font:
                self._gdi32.DeleteObject(self._header_font)
                self._header_font = None
            if self._bold_font:
                self._gdi32.DeleteObject(self._bold_font)
                self._bold_font = None
            if self._search_font:
                self._gdi32.DeleteObject(self._search_font)
                self._search_font = None
            self._search_hwnd = None
            self._scroll_hwnd = None

            self._user32.PostQuitMessage(0)
            return 0

        return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _thread_main(self):
        try:
            self._thread_main_impl()
        except Exception as e:
            # Always unblock start() if native initialization fails.
            print(f"Native chat overlay initialization failed: {e}")
            self._ready_event.set()

    def _thread_main_impl(self):
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._kernel32 = ctypes.windll.kernel32

        # API prototypes used by this class. Explicit pointer-sized argument
        # and return types are important on 64-bit Windows. Without argtypes,
        # ctypes defaults unspecified parameters to c_int; passing a 64-bit
        # HINSTANCE/HWND/HMENU can then raise:
        #   OverflowError: int too long to convert
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self._user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,       # dwExStyle
            wintypes.LPCWSTR,     # lpClassName
            wintypes.LPCWSTR,     # lpWindowName
            wintypes.DWORD,       # dwStyle
            ctypes.c_int,         # X
            ctypes.c_int,         # Y
            ctypes.c_int,         # nWidth
            ctypes.c_int,         # nHeight
            wintypes.HWND,        # hWndParent
            wintypes.HMENU,       # hMenu
            wintypes.HINSTANCE,   # hInstance
            wintypes.LPVOID,      # lpParam
        ]
        self._user32.CreateWindowExW.restype = wintypes.HWND

        self._user32.LoadCursorW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
        ]
        self._user32.LoadCursorW.restype = wintypes.HCURSOR

        # Pointer-sized APIs must be declared explicitly on Win64.
        self._user32.GetWindowLongPtrW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
        ]
        self._user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self._user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_ssize_t,
        ]
        self._user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self._user32.ScreenToClient.restype = wintypes.BOOL
        self._user32.UpdateWindow.argtypes = [wintypes.HWND]
        self._user32.UpdateWindow.restype = wintypes.BOOL
        self._user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        self._user32.EnableWindow.restype = wintypes.BOOL
        self._user32.SetScrollInfo.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.BOOL,
        ]
        self._user32.SetScrollInfo.restype = ctypes.c_int
        self._user32.GetScrollInfo.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        self._user32.GetScrollInfo.restype = wintypes.BOOL
        self._user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.SendMessageW.restype = ctypes.c_ssize_t
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.SetTimer.argtypes = [
            wintypes.HWND,
            ctypes.c_size_t,
            wintypes.UINT,
            ctypes.c_void_p,
        ]
        self._user32.SetTimer.restype = ctypes.c_size_t
        self._user32.KillTimer.argtypes = [
            wintypes.HWND,
            ctypes.c_size_t,
        ]
        self._user32.KillTimer.restype = wintypes.BOOL
        self._user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND,
            wintypes.COLORREF,
            wintypes.BYTE,
            wintypes.DWORD,
        ]
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.SetCapture.argtypes = [wintypes.HWND]
        self._user32.ReleaseCapture.argtypes = []
        self._user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        self._user32.EndPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        self._user32.DrawTextW.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.RECT),
            wintypes.UINT,
        ]
        self._user32.FillRect.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.HBRUSH,
        ]
        self._user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.BeginPaint.restype = wintypes.HDC

        self._gdi32.CreateFontW.restype = wintypes.HFONT
        self._gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self._gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self._gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self._gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self._gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.SIZE)]
        self._gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
        self._gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self._gdi32.DeleteObject.restype = wintypes.BOOL

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HCURSOR),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        self._user32.RegisterClassExW.argtypes = [
            ctypes.POINTER(WNDCLASSEXW),
        ]
        self._user32.RegisterClassExW.restype = wintypes.ATOM

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", wintypes.BYTE * 32),
            ]

        class SCROLLINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("fMask", wintypes.UINT),
                ("nMin", ctypes.c_int),
                ("nMax", ctypes.c_int),
                ("nPage", wintypes.UINT),
                ("nPos", ctypes.c_int),
                ("nTrackPos", ctypes.c_int),
            ]

        self._POINT = wintypes.POINT
        self._RECT = wintypes.RECT
        self._SIZE = wintypes.SIZE
        self._PAINTSTRUCT = PAINTSTRUCT
        self._SCROLLINFO = SCROLLINFO

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._wndproc = WNDPROC(self._window_proc)

        hinstance = self._kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = None
        # IDC_ARROW is a Win32 MAKEINTRESOURCE value, not a real Unicode
        # string pointer. Cast it explicitly so ctypes accepts it as LPCWSTR.
        arrow_cursor = ctypes.cast(
            ctypes.c_void_p(self.IDC_ARROW),
            wintypes.LPCWSTR,
        )
        wc.hCursor = self._user32.LoadCursorW(None, arrow_cursor)
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self.class_name
        wc.hIconSm = None

        atom = self._user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            # If a stale class registration exists, CreateWindowExW can still
            # use it with the same instance/callback on this process.
            pass
        self.class_atom = atom

        exstyle = (
            self.WS_EX_TOPMOST
            | self.WS_EX_TOOLWINDOW
            | self.WS_EX_LAYERED
        )
        if self._locked:
            exstyle |= self.WS_EX_TRANSPARENT

        self.hwnd = self._user32.CreateWindowExW(
            exstyle,
            self.class_name,
            "Ravaelv Live Chat",
            self.WS_POPUP | self.WS_THICKFRAME,
            self._x,
            self._y,
            self._width,
            self._height,
            None,
            None,
            hinstance,
            None,
        )

        if not self.hwnd:
            self._ready_event.set()
            print("Native chat overlay: CreateWindowExW failed.")
            return

        self._font = self._gdi32.CreateFontW(
            -13, 0, 0, 0, 400, 0, 0, 0,
            1, 0, 0, 0, 0, "Segoe UI",
        )
        self._header_font = self._gdi32.CreateFontW(
            -17, 0, 0, 0, 700, 0, 0, 0,
            1, 0, 0, 0, 0, "Segoe UI",
        )
        self._bold_font = self._gdi32.CreateFontW(
            -13, 0, 0, 0, 700, 0, 0, 0,
            1, 0, 0, 0, 0, "Segoe UI",
        )
        self._search_font = self._gdi32.CreateFontW(
            -13, 0, 0, 0, 400, 0, 0, 0,
            1, 0, 0, 0, 0, "Segoe UI",
        )

        # Native EDIT control for full-history search. It is a child control
        # rather than a custom text renderer, so typing uses the standard
        # Windows edit pipeline with almost no CPU overhead.
        edit_style = (
            self.WS_CHILD
            | self.WS_VISIBLE
            | self.ES_LEFT
            | self.ES_AUTOHSCROLL
            | self.ES_NOHIDESEL
            | 0x00010000  # WS_TABSTOP
        )
        self._search_hwnd = self._user32.CreateWindowExW(
            self.WS_EX_CLIENTEDGE,
            "EDIT",
            "",
            edit_style,
            8,
            38,
            max(120, self._width - 16),
            25,
            self.hwnd,
            ctypes.cast(
                ctypes.c_void_p(self.EDIT_CONTROL_ID),
                wintypes.HMENU,
            ),
            hinstance,
            None,
        )
        if self._search_hwnd:
            self._user32.SendMessageW(
                self._search_hwnd,
                self.WM_SETFONT,
                self._search_font,
                1,
            )
            cue = ctypes.c_wchar_p("Search chat history...")
            self._user32.SendMessageW(
                self._search_hwnd,
                self.EM_SETCUEBANNER,
                0,
                ctypes.cast(cue, ctypes.c_void_p).value,
            )

        # Permanent native vertical scrollbar in the content area. Unlike the
        # parent WS_VSCROLL non-client scrollbar, this child remains visible
        # even when there is not enough history to scroll yet.
        self._scroll_hwnd = self._user32.CreateWindowExW(
            0,
            "SCROLLBAR",
            "",
            self.WS_CHILD | self.WS_VISIBLE | 0x00000001,  # SBS_VERT
            max(0, self._width - OVERLAY_SCROLLBAR_WIDTH),
            70,
            OVERLAY_SCROLLBAR_WIDTH,
            max(1, self._height - 70),
            self.hwnd,
            ctypes.cast(ctypes.c_void_p(self.EDIT_CONTROL_ID + 1), wintypes.HMENU),
            hinstance,
            None,
        )
        if self._scroll_hwnd:
            self._user32.EnableWindow(
                self._scroll_hwnd,
                not self._locked,
            )

        self._user32.SetLayeredWindowAttributes(
            self.hwnd,
            0,
            OVERLAY_ALPHA,
            self.LWA_ALPHA,
        )

        self._user32.SetWindowPos(
            self.hwnd,
            self.HWND_TOPMOST,
            self._x,
            self._y,
            self._width,
            self._height,
            self.SWP_NOACTIVATE,
        )

        if OVERLAY_REGISTER_HOTKEYS:
            self._user32.RegisterHotKey(
                self.hwnd,
                self.HOTKEY_LOCK,
                self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT,
                ord("L"),
            )
            self._user32.RegisterHotKey(
                self.hwnd,
                self.HOTKEY_VISIBILITY,
                self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT,
                ord("H"),
            )

        self._user32.ShowWindow(
            self.hwnd,
            self.SW_SHOWNOACTIVATE if self._visible else self.SW_HIDE,
        )
        self._position_search_box()
        self._history_total = self._history_count()
        self._update_scrollbar(force_count=False)
        self._user32.UpdateWindow(self.hwnd)
        self._ready_event.set()

        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            result = self._user32.GetMessageW(
                ctypes.byref(msg),
                None,
                0,
                0,
            )
            if result <= 0:
                break
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

        if self.hwnd:
            try:
                self._save_state()
            except Exception:
                pass


# =============================================================
# Configuration
# =============================================================

def atomic_write_text(path, text, retries=8, delay=0.05):
    """
    Write `text` to `path` through a temp file + replace().

    On Windows the replace can fail transiently with PermissionError when
    another process (FFmpeg reading the overlay, an antivirus scanner, an
    editor) has the target open, so retry briefly. As a last resort write
    the target directly rather than lose the data.

    Callers must serialise writes to the same path (they use a lock).
    """
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")

    for attempt in range(retries):
        try:
            temp.write_text(text, encoding="utf-8")
            temp.replace(path)
            return
        except OSError:
            time.sleep(delay * (attempt + 1))

    try:
        temp.unlink()
    except OSError:
        pass

    path.write_text(text, encoding="utf-8")

def load_config():
    with config_lock:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            config = json.load(f)

    # oauth_token is runtime-only. Older config files may still contain it;
    # remove it from the in-memory configuration so startup always derives
    # the access token from the refresh token / Device Code Flow.
    twitch = config.get("twitch")
    if isinstance(twitch, dict):
        twitch.pop("oauth_token", None)

    return config


def save_config(config):
    # oauth_token is intentionally runtime-only and is never written to disk.
    twitch = config.get("twitch")
    if isinstance(twitch, dict):
        twitch.pop("oauth_token", None)

    # Atomic replacement. This is important because the refresh
    # token can rotate and the old one may immediately become
    # invalid after a successful refresh.
    with config_lock:
        atomic_write_text(
            CONFIG_FILE,
            json.dumps(config, indent=4),
        )


def update_config_section(section, values):
    """Read-modify-write ONE config section from the file on disk.

    Workers that own a single section use this instead of save_config() with
    the shared in-memory config, so they can never overwrite newer data from
    another worker (for example a Twitch refresh token rotated meanwhile).
    """
    with config_lock:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            current = json.load(f)

        current.setdefault(section, {}).update(values)

        twitch = current.get("twitch")
        if isinstance(twitch, dict):
            twitch.pop("oauth_token", None)

        atomic_write_text(
            CONFIG_FILE,
            json.dumps(current, indent=4),
        )


# =============================================================
# Chat history database
# =============================================================

def _db_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def init_chat_database():
    """Open the persistent SQLite database and start a new chat session."""
    global chat_db, chat_session_id

    CHAT_DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        CHAT_DATABASE_FILE,
        check_same_thread=False,
        timeout=30,
    )
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            username TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(session_id, platform, username),
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            platform TEXT NOT NULL,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    # Message identity/state used to detect edits by comparing the same
    # platform message ID against the last value stored in SQLite.
    # Existing databases are migrated in-place.
    for column_sql in (
        "ALTER TABLE messages ADD COLUMN source_message_id TEXT",
        "ALTER TABLE messages ADD COLUMN modified_at TEXT",
        "ALTER TABLE messages ADD COLUMN is_modified INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            connection.execute(column_sql)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_source_id "
        "ON messages(platform, source_message_id) "
        "WHERE source_message_id IS NOT NULL"
    )

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_modified "
        "ON messages(is_modified, id)"
    )

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_time ON messages(session_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_user_time ON messages(platform, username, created_at)"
    )

    started_at = _db_timestamp()
    cursor = connection.execute(
        "INSERT INTO sessions(started_at) VALUES (?)",
        (started_at,),
    )
    connection.commit()

    with db_lock:
        chat_db = connection
        chat_session_id = cursor.lastrowid
        modified_message_ids.clear()
        for (row_id,) in connection.execute(
            "SELECT id FROM messages WHERE is_modified = 1"
        ).fetchall():
            modified_message_ids.add(int(row_id))

    print(f"Chat history database: {CHAT_DATABASE_FILE}")
    print(f"Chat history session started: {started_at}")


def record_chat_message(
    platform,
    username,
    message,
    created_at=None,
    source_message_id=None,
):
    """Insert a new message, or update an existing message with the same
    platform/source ID when its text changed.

    Returns ``(row_id, state)`` where state is ``new``, ``duplicate`` or
    ``modified``. The source ID is the stable platform message identifier,
    which makes the comparison deterministic instead of guessing based on
    username/time.
    """
    timestamp = created_at or _db_timestamp()
    source_message_id = (
        str(source_message_id).strip() if source_message_id else None
    )

    with db_lock:
        if chat_db is None or chat_session_id is None:
            return None, "new"

        try:
            if source_message_id:
                existing = chat_db.execute(
                    """
                    SELECT id, username, message
                    FROM messages
                    WHERE platform = ? AND source_message_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (platform, source_message_id),
                ).fetchone()

                if existing:
                    row_id, old_username, old_message = existing
                    if (
                        str(old_message) == str(message)
                        and str(old_username) == str(username)
                    ):
                        return row_id, "duplicate"

                    # Same platform message ID, different content: this is
                    # an edit. Preserve the original created_at and row ID.
                    modified_at = _db_timestamp()
                    chat_db.execute(
                        """
                        UPDATE messages
                        SET username = ?,
                            message = ?,
                            modified_at = ?,
                            is_modified = 1
                        WHERE id = ?
                        """,
                        (username, message, modified_at, row_id),
                    )
                    chat_db.commit()
                    return row_id, "modified"

            cursor = chat_db.execute(
                """
                INSERT INTO messages(
                    session_id, created_at, platform, username, message,
                    source_message_id, modified_at, is_modified
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                (
                    chat_session_id,
                    timestamp,
                    platform,
                    username,
                    message,
                    source_message_id,
                ),
            )

            chat_db.execute(
                """
                INSERT INTO participants(
                    session_id, platform, username,
                    first_seen_at, last_seen_at, message_count
                ) VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(session_id, platform, username)
                DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    message_count = participants.message_count + 1
                """,
                (
                    chat_session_id,
                    platform,
                    username,
                    timestamp,
                    timestamp,
                ),
            )
            chat_db.commit()
            return cursor.lastrowid, "new"
        except sqlite3.Error as e:
            try:
                chat_db.rollback()
            except sqlite3.Error:
                pass

            print(f"Chat history write failed: {e}")
            return None, "new"

def close_chat_database():
    """Close the current session cleanly and close SQLite."""
    global chat_db, chat_session_id

    with db_lock:
        connection = chat_db
        session_id = chat_session_id
        chat_db = None
        chat_session_id = None

        if connection is None:
            return

        try:
            ended_at = _db_timestamp()
            if session_id is not None:
                connection.execute(
                    "UPDATE sessions SET ended_at = ? WHERE id = ?",
                    (ended_at, session_id),
                )
                connection.commit()
                print(f"Chat history session ended: {ended_at}")
        finally:
            connection.close()


# =============================================================
# Overlay writer
# =============================================================

def _stream_panel_lines():
    """Wrap the stored messages (oldest -> newest) and keep the newest whole
    messages that fit in STREAM_PANEL_MAX_LINES lines. Caller holds `lock`."""
    blocks = []

    for platform, username, message, created_at, message_id, highlight_until in messages:
        prefix = {
            "T": "[T]",
            "Y": "[YT]",
            "TT": "[TT]",
        }.get(platform, "[?]")

        message = " ".join(str(message).split())

        if not message:
            continue

        wrapped = textwrap.wrap(
            f"{prefix} {username}: {message}",
            width=MAX_LINE_LENGTH,
            break_long_words=False,
            break_on_hyphens=False,
        )

        if len(wrapped) > STREAM_PANEL_MAX_LINES_PER_MESSAGE:
            wrapped = wrapped[:STREAM_PANEL_MAX_LINES_PER_MESSAGE]
            wrapped[-1] = wrapped[-1][: MAX_LINE_LENGTH - 1].rstrip() + "\u2026"

        blocks.append(wrapped)

    selected = []
    used = 0

    for block in reversed(blocks):
        if used + len(block) > STREAM_PANEL_MAX_LINES:
            break

        selected.append(block)
        used += len(block)

    selected.reverse()

    return [line for block in selected for line in block]


def _write_overlay_locked():
    """Render and write the plain-text chat overlay file.

    SQLite receives the full message history; this text file is only the
    current chat snapshot read by the FFmpeg stream overlay (oldest -> newest,
    limited to what fits in the stream panel). The native window displays
    newest first independently.
    """
    try:
        atomic_write_text(OUTPUT_FILE, "\n".join(_stream_panel_lines()))
    except OSError as e:
        print(f"Overlay write failed: {e}")


def write_overlay():
    with lock:
        _write_overlay_locked()


def play_chat_beep():
    """Play a short, clearly audible notification for a chat update."""
    if not CHAT_BEEP_ENABLED or sys.platform != "win32":
        return

    try:
        winsound.Beep(
            CHAT_BEEP_FREQUENCY,
            CHAT_BEEP_DURATION_MS,
        )
    except (RuntimeError, OSError):
        # Do not let an unavailable Windows sound device interrupt chat.
        pass


def add_message(platform, username, message, source_message_id=None):
    """Add a new chat message or detect an edit of an existing message.

    New messages receive the existing green/5-second blink treatment.
    Edited messages are moved to the top of the live overlay and marked red
    + bold so the streamer can immediately see that the text changed.
    """
    global modified_message_ids

    username = " ".join(str(username).split())
    message = " ".join(str(message).split())

    if not username or not message:
        return

    created_at = _db_timestamp()
    database_message_id, state = record_chat_message(
        platform,
        username,
        message,
        created_at,
        source_message_id,
    )

    if state == "duplicate":
        return

    if database_message_id is None:
        database_message_id = messages[-1][4] + 1 if messages else 1

    if state == "modified":
        modified_message_ids.add(database_message_id)

        # Update the existing live-feed entry if it is still in the recent
        # deque. Otherwise surface the edited message at the top so the
        # streamer does not miss an edit to an older message.
        replaced = False
        with lock:
            for index, item in enumerate(messages):
                if item[4] == database_message_id:
                    messages[index] = (
                        platform,
                        username,
                        message,
                        item[3],
                        database_message_id,
                        0.0,
                    )
                    replaced = True
                    break

            if not replaced:
                messages.append(
                    (
                        platform,
                        username,
                        message,
                        created_at,
                        database_message_id,
                        0.0,
                    )
                )

            _write_overlay_locked()

        # Edited-message notification: same audible alert, but the visual
        # state is red rather than the normal green new-message state.
        play_chat_beep()

        if native_overlay is not None:
            native_overlay.notify_update()
        return

    highlight_until = time.monotonic() + OVERLAY_NEW_MESSAGE_DURATION

    with lock:
        messages.append(
            (
                platform,
                username,
                message,
                created_at,
                database_message_id,
                highlight_until,
            )
        )
        _write_overlay_locked()

    # Audible notification: one beep for every accepted new chat message.
    play_chat_beep()

    if native_overlay is not None:
        native_overlay.notify_update()


# =============================================================
# Twitch OAuth
# =============================================================

def set_runtime_twitch_access_token(token):
    global twitch_access_token

    with twitch_access_token_lock:
        twitch_access_token = token


def get_runtime_twitch_access_token():
    with twitch_access_token_lock:
        return twitch_access_token


def validate_twitch_token(twitch, token=None):
    if token is None:
        token = get_runtime_twitch_access_token()

    token = token.strip()
    client_id = twitch.get("client_id", "").strip()

    if not token:
        raise RuntimeError(
            "Twitch runtime access token is empty."
        )

    if not client_id:
        raise RuntimeError("Twitch client_id is empty.")

    try:
        response = requests.get(
            "https://id.twitch.tv/oauth2/validate",
            headers={
                "Authorization": f"OAuth {token}",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        raise TwitchTransientError(
            f"Twitch token validation request failed: {e}"
        ) from e

    if response.status_code == 401:
        raise RuntimeError(
            "Twitch access token is invalid or revoked."
        )

    if response.status_code >= 500 or response.status_code == 429:
        raise TwitchTransientError(
            f"Twitch validation temporarily unavailable "
            f"(HTTP {response.status_code})."
        )

    response.raise_for_status()

    data = response.json()

    if data.get("client_id") != client_id:
        raise RuntimeError(
            "Twitch token belongs to a different Client ID."
        )

    login = data.get("login", "").lower()
    expected_login = twitch.get("username", "").strip().lower()

    if expected_login and login != expected_login:
        raise RuntimeError(
            f"Twitch token belongs to '{login}', "
            f"but config username is '{expected_login}'."
        )

    scopes = set(data.get("scopes", []))

    if "chat:read" not in scopes:
        raise RuntimeError(
            "Twitch token does not contain chat:read."
        )

    return data


def refresh_twitch_token(config):
    """
    Refresh the Twitch user access token.

    Twitch requires the refresh-token parameters in the POST body as
    application/x-www-form-urlencoded data. requests.post(..., data=...)
    performs the correct form encoding automatically.

    The refresh operation is protected by a process-local lock so that
    multiple worker paths in this process cannot rotate the same refresh
    token simultaneously.
    """
    with twitch_refresh_lock:
        # Reload the latest config while holding the lock. A successful
        # refresh can rotate the refresh token, so we must use the newest
        # value from disk/config rather than a stale copy.
        current_config = load_config()
        twitch = current_config.get("twitch", {})

        client_id = twitch.get("client_id", "").strip()
        client_secret = twitch.get("client_secret", "").strip()
        refresh_token = twitch.get("refresh_token", "").strip()

        if not client_id:
            raise RuntimeError("Missing Twitch client_id.")

        if not refresh_token:
            raise RuntimeError(
                "Missing Twitch refresh_token. "
                "Twitch cannot refresh the access token without one."
            )

        print("Twitch OAuth: refreshing access token...")

        # Twitch documents this endpoint as an
        # application/x-www-form-urlencoded POST.
        form_data = {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        # Confidential clients send the client secret. Public clients
        # (including supported Device Code Flow public clients) can omit it.
        if client_secret:
            form_data["client_secret"] = client_secret

        try:
            response = requests.post(
                "https://id.twitch.tv/oauth2/token",
                data=form_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=15,
            )
        except requests.RequestException as e:
            raise TwitchTransientError(
                f"Twitch token refresh request failed: {e}"
            ) from e

        if not response.ok:
            try:
                details = response.json()
            except Exception:
                details = response.text

            message = (
                f"Twitch token refresh failed "
                f"(HTTP {response.status_code}): {details}"
            )

            # Server-side/rate-limit problems are temporary. Only a
            # definite rejection means the refresh token is unusable.
            if response.status_code >= 500 or response.status_code == 429:
                raise TwitchTransientError(message)

            raise RuntimeError(message)

        data = response.json()

        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")

        if not new_access_token:
            raise RuntimeError(
                "Twitch refresh response did not contain access_token."
            )

        set_runtime_twitch_access_token(new_access_token)

        # Twitch may rotate the refresh token. Always persist the newest
        # value returned by the refresh endpoint.
        if new_refresh_token:
            twitch["refresh_token"] = new_refresh_token

        current_config["twitch"] = twitch

        try:
            save_config(current_config)
            print(
                "Twitch OAuth: access token refreshed and "
                "chat_config.json updated."
            )
        except OSError as e:
            print(
                "Twitch OAuth: access token refreshed, but "
                f"chat_config.json could not be updated: {e}"
            )

        # Keep the caller's config object in sync as well.
        config["twitch"] = dict(twitch)

        return data


def bootstrap_twitch_tokens_via_cli(config):
    """
    Bootstrap Twitch OAuth through Device Code Flow (DCF).

    This intentionally avoids the Twitch CLI's localhost OAuth redirect
    flow, so the application does not depend on http://localhost:3000 and
    cannot hit redirect_mismatch errors.

    The script:
      1. Uses the Client ID/Secret already in chat_config.json.
      2. Runs: twitch token -u --dcf -s "chat:read"
      3. Detects the Twitch activation URL and opens it automatically.
      4. Waits while the user authorizes the application.
      5. Captures the access token + refresh token from the CLI output.
      6. Saves both tokens to chat_config.json.
    """
    twitch = config.get("twitch", {})

    client_id = twitch.get("client_id", "").strip()
    client_secret = twitch.get("client_secret", "").strip()

    if not client_id:
        raise RuntimeError(
            "Cannot bootstrap Twitch OAuth: missing client_id."
        )

    twitch_exe = shutil.which("twitch")

    if not twitch_exe:
        raise RuntimeError(
            "Twitch CLI was not found in PATH. "
            "Verify that 'twitch' runs from PowerShell."
        )

    print(
        "Twitch OAuth: starting Device Code Flow automatically..."
    )
    print(
        "Twitch OAuth: the browser will open for one-time authorization."
    )

    # Keep the installed Twitch CLI synchronized with this application.
    # This is useful for CLI environments that rely on stored credentials.
    configure_cmd = [
        twitch_exe,
        "configure",
        "-i",
        client_id,
    ]

    if client_secret:
        configure_cmd.extend([
            "-s",
            client_secret,
        ])

    configure = subprocess.run(
        configure_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    if configure.returncode != 0:
        details = (configure.stderr or configure.stdout).strip()
        raise RuntimeError(
            "Twitch CLI configure failed."
            + (f" Details: {details}" if details else "")
        )

    # Device Code Flow does not use the localhost OAuth redirect.
    token_cmd = [
        twitch_exe,
        "token",
        "-u",
        "-s",
        "chat:read",
        "--dcf",
    ]

    # For a confidential Twitch client, pass the secret explicitly.
    # Public clients can omit the secret.
    if client_id:
        token_cmd.extend([
            "--client-id",
            client_id,
        ])

    if client_secret:
        token_cmd.extend([
            "--secret",
            client_secret,
        ])

    # Run interactively so the activation URL/code can be shown immediately.
    process = subprocess.Popen(
        token_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    output_lines = []
    activation_opened = False

    # Do not wait forever if the user never authorizes the device code.
    watchdog = threading.Timer(DCF_TIMEOUT, process.kill)
    watchdog.daemon = True
    watchdog.start()

    try:
        for raw_line in iter(process.stdout.readline, ""):
            line = raw_line.rstrip("\r\n")

            if not line:
                continue

            output_lines.append(line)

            # Twitch CLI DCF normally prints a URL such as:
            # https://www.twitch.tv/activate?device-code=XXXX
            url_match = re.search(
                r"https://www\.twitch\.tv/activate\S*",
                line,
                flags=re.IGNORECASE,
            )

            if url_match and not activation_opened:
                activation_url = url_match.group(0)

                # Open the activation page automatically.
                try:
                    webbrowser.open(activation_url)
                    activation_opened = True
                    print(
                        "Twitch OAuth: activation page opened "
                        "in your default browser."
                    )
                except Exception as e:
                    print(
                        "Twitch OAuth: could not open the browser "
                        f"automatically: {e}"
                    )
                    print(
                        f"Twitch OAuth: open this URL manually: {activation_url}"
                    )

            # Redact token values when displaying the CLI output.
            display_line = re.sub(
                r"(User Access Token:\s*)\S+",
                r"\1[stored]",
                line,
                flags=re.IGNORECASE,
            )
            display_line = re.sub(
                r"(Refresh Token:\s*)\S+",
                r"\1[stored]",
                display_line,
                flags=re.IGNORECASE,
            )

            print(display_line, flush=True)

    finally:
        watchdog.cancel()
        if process.stdout:
            process.stdout.close()

    return_code = process.wait()

    cli_output = "\n".join(output_lines)

    if return_code != 0:
        raise RuntimeError(
            "Twitch CLI Device Code Flow failed. "
            "Check the CLI output above and complete the authorization."
        )

    access_match = re.search(
        r"User Access Token:\s*(\S+)",
        cli_output,
        flags=re.IGNORECASE,
    )
    refresh_match = re.search(
        r"Refresh Token:\s*(\S+)",
        cli_output,
        flags=re.IGNORECASE,
    )

    if not access_match or not refresh_match:
        raise RuntimeError(
            "Twitch CLI completed but its output did not contain "
            "both an access token and refresh token."
        )

    new_access_token = access_match.group(1).strip()
    new_refresh_token = refresh_match.group(1).strip()

    if not new_access_token or not new_refresh_token:
        raise RuntimeError(
            "Twitch CLI returned an empty access token or refresh token."
        )

    set_runtime_twitch_access_token(new_access_token)
    twitch["refresh_token"] = new_refresh_token
    config["twitch"] = twitch

    save_config(config)

    print(
        "Twitch OAuth: new access token and refresh token "
        "captured and saved to chat_config.json."
    )

    return twitch


def _refresh_or_bootstrap(config, reason):
    refresh_token = (
        config.get("twitch", {})
        .get("refresh_token", "")
        .strip()
    )

    if refresh_token:
        try:
            refresh_twitch_token(config)
            return validate_twitch_token(
                config["twitch"]
            )
        except TwitchTransientError:
            # Network/server trouble is not a credential problem. Do not
            # open a browser for re-authorization because of it.
            raise
        except Exception as refresh_error:
            print(
                f"Twitch OAuth: refresh failed ({reason}); "
                "starting Device Code Flow..."
            )

            try:
                bootstrap_twitch_tokens_via_cli(config)
                return validate_twitch_token(
                    config["twitch"]
                )
            except Exception as cli_error:
                raise RuntimeError(
                    "Twitch OAuth recovery failed. "
                    f"Refresh: {refresh_error}; CLI: {cli_error}"
                ) from cli_error

    bootstrap_twitch_tokens_via_cli(config)

    return validate_twitch_token(
        config["twitch"]
    )


def ensure_twitch_token(config):
    """
    Ensure a valid Twitch access token exists in memory.

    The access token is never required in chat_config.json. The script
    obtains it from the stored refresh token, or falls back to Twitch
    Device Code Flow when no usable refresh token exists.

    Raises TwitchTransientError for temporary network/server problems.
    """
    current_token = get_runtime_twitch_access_token()

    if not current_token:
        return _refresh_or_bootstrap(
            config,
            "access token missing",
        )

    twitch = config.get("twitch", {})

    try:
        data = validate_twitch_token(
            twitch,
            current_token,
        )
    except TwitchTransientError:
        raise
    except Exception as validation_error:
        print(
            f"Twitch OAuth validation failed: "
            f"{validation_error}"
        )

        return _refresh_or_bootstrap(
            config,
            "access token invalid",
        )

    # Kept outside the try block above: a failed recovery here must not
    # be mistaken for a validation failure and retried a second time.
    expires_in = int(
        data.get("expires_in", 0)
    )

    print(
        f"Twitch OAuth: valid for approximately "
        f"{expires_in // 60} minutes."
    )

    if expires_in <= TOKEN_REFRESH_MARGIN:
        print(
            "Twitch OAuth: token is near expiry; "
            "refreshing..."
        )

        return _refresh_or_bootstrap(
            config,
            "token near expiry",
        )

    return data


def parse_twitch_privmsg(line):
    line = line.strip("\r\n")

    if not line:
        return None

    tags = {}
    if line.startswith("@"): 
        try:
            tag_text, line = line.split(" ", 1)
            for item in tag_text[1:].split(";"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    tags[key] = value
        except ValueError:
            return None

    if " PRIVMSG #" not in line:
        return None

    if not line.startswith(":"):
        return None

    try:
        prefix, remainder = line[1:].split(" ", 1)
    except ValueError:
        return None

    username = prefix.split("!", 1)[0]

    try:
        message = remainder.split(" :", 1)[1]
    except IndexError:
        return None

    if message.startswith("\x01ACTION ") and message.endswith("\x01"):
        message = message[len("\x01ACTION "):-1]

    message = " ".join(message.split())

    if not username or not message:
        return None

    # Twitch IRC's @id tag is the stable message identifier.
    source_message_id = tags.get("id", "").strip() or None
    return username, message, source_message_id


# =============================================================
# Twitch IRC worker
# =============================================================

def twitch_worker(config):
    twitch = config.get("twitch", {})

    if not twitch.get("enabled", False):
        print("Twitch chat disabled.")
        return

    username = twitch.get("username", "").strip().lower()
    channel = twitch.get("channel", "").strip().lstrip("#").lower()

    if not username or not channel:
        print(
            "Twitch chat: username/channel not configured."
        )
        return

    # Obtain the first access token. A temporary network problem at
    # startup must not kill the worker for the whole session, so it is
    # retried. Credential/configuration problems need the user's
    # attention and end the worker.
    while True:
        try:
            ensure_twitch_token(config)
            break
        except TwitchTransientError as e:
            print(
                f"Twitch OAuth: temporary problem ({e}); "
                "retrying in 15s."
            )
            time.sleep(15)
        except Exception as e:
            print(f"Twitch OAuth startup error: {e}")
            return

    last_validation = time.time()
    failures = 0

    print(
        f"Twitch chat: connecting as '{username}' "
        f"to #{channel}..."
    )

    while True:
        sock = None

        try:
            # Reload in case the refresh operation rotated the
            # refresh token. The access token itself is runtime-only.
            current_config = load_config()
            twitch = current_config["twitch"]
            oauth_token = get_runtime_twitch_access_token()

            if not oauth_token:
                raise RuntimeError(
                    "Twitch runtime access token is unavailable."
                )

            raw = socket.create_connection(
                ("irc.chat.twitch.tv", 6697),
                timeout=15,
            )

            context = ssl.create_default_context()

            sock = context.wrap_socket(
                raw,
                server_hostname="irc.chat.twitch.tv",
            )

            sock.settimeout(30)

            def send(line):
                sock.sendall(
                    (line + "\r\n").encode("utf-8")
                )

            send(f"PASS oauth:{oauth_token}")
            send(f"NICK {username}")
            send(
                "CAP REQ :twitch.tv/membership "
                "twitch.tv/tags twitch.tv/commands"
            )

            authenticated = False
            buffer = ""
            deadline = time.time() + 15

            while time.time() < deadline and not authenticated:
                data = sock.recv(8192)

                if not data:
                    raise ConnectionError(
                        "Twitch closed the connection during authentication."
                    )

                buffer += data.decode(
                    "utf-8",
                    errors="replace",
                )

                while "\r\n" in buffer:
                    line, buffer = buffer.split(
                        "\r\n",
                        1,
                    )

                    if line.startswith("PING"):
                        send("PONG :tmi.twitch.tv")
                        continue

                    if "Login authentication failed" in line:
                        raise RuntimeError(
                            "Twitch authentication failed."
                        )

                    parts = line.split()

                    if len(parts) >= 2 and parts[1] == "001":
                        authenticated = True
                        break

            if not authenticated:
                raise RuntimeError(
                    "Twitch authentication timed out."
                )

            print("Twitch chat: authenticated.")

            send(f"JOIN #{channel}")

            print(
                f"Twitch chat: joined #{channel}"
            )

            failures = 0
            buffer = ""
            last_rx = time.time()

            while True:
                # Re-validate approximately every hour.
                if time.time() - last_validation >= TOKEN_VALIDATE_INTERVAL:
                    old_token = get_runtime_twitch_access_token()

                    try:
                        current_config = load_config()
                        ensure_twitch_token(current_config)
                        last_validation = time.time()
                    except TwitchTransientError as e:
                        # The connection is healthy; a network blip while
                        # validating is no reason to drop it. Try again
                        # in a minute.
                        print(
                            f"Twitch OAuth: temporary problem while "
                            f"validating ({e}); retrying in 60s."
                        )
                        last_validation = (
                            time.time() - TOKEN_VALIDATE_INTERVAL + 60
                        )
                    except Exception as e:
                        print(
                            f"Twitch OAuth validation failed "
                            f"while connected: {e}"
                        )
                        raise ConnectionError(
                            "Restarting Twitch IRC after OAuth validation failure."
                        ) from e
                    else:
                        # A refresh changes the runtime access token.
                        # Reconnect IRC so PASS uses the new token.
                        if get_runtime_twitch_access_token() != old_token:
                            raise ConnectionError(
                                "Twitch access token refreshed; "
                                "reconnecting IRC."
                            )

                try:
                    data = sock.recv(8192)
                except socket.timeout:
                    # A quiet chat is normal, not an error. Only treat the
                    # connection as dead if Twitch stays silent even after
                    # we ping it.
                    idle = time.time() - last_rx

                    if idle >= TWITCH_IDLE_TIMEOUT:
                        raise ConnectionError(
                            f"No data from Twitch for {int(idle)}s."
                        )

                    if idle >= TWITCH_PING_AFTER:
                        send("PING :tmi.twitch.tv")

                    continue

                if not data:
                    raise ConnectionError(
                        "Twitch connection closed."
                    )

                last_rx = time.time()

                buffer += data.decode(
                    "utf-8",
                    errors="replace",
                )

                while "\r\n" in buffer:
                    line, buffer = buffer.split(
                        "\r\n",
                        1,
                    )

                    if line.startswith("PING"):
                        send("PONG :tmi.twitch.tv")
                        continue

                    if line.startswith(":tmi.twitch.tv RECONNECT"):
                        raise ConnectionError(
                            "Twitch asked the client to reconnect."
                        )

                    parsed = parse_twitch_privmsg(line)

                    if parsed:
                        msg_username, message, source_message_id = parsed
                        add_message(
                            "T",
                            msg_username,
                            message,
                            source_message_id=source_message_id,
                        )

        except Exception as e:
            failures += 1

            print(f"Twitch chat error: {e}")

            # A disconnected IRC session may be caused by an
            # invalid/expired token. Try validation + refresh
            # before reconnecting.
            try:
                current_config = load_config()
                ensure_twitch_token(current_config)
                last_validation = time.time()
            except Exception as auth_error:
                print(
                    f"Twitch OAuth recovery failed: {auth_error}"
                )
                time.sleep(15)

        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        # Back off on repeated failures (3s, 6s, 12s ... capped at 60s)
        # so a persistent problem cannot hammer Twitch.
        time.sleep(min(3 * (2 ** (failures - 1)), 60))


# =============================================================
# YouTube helpers
# =============================================================

YOUTUBE_DISCOVERY_INTERVAL = 60
YOUTUBE_RETRY_INTERVAL = 5
YOUTUBE_MAX_RESULTS = 200
YOUTUBE_MAX_SEEN_IDS = 500
YOUTUBE_DISCOVERY_MAX_VIDEOS = 50  # playlistItems supports up to 50
YOUTUBE_QUOTA_BACKOFF = 900  # wait this long after quota exhaustion
YOUTUBE_USE_STREAM_LIST = True       # preferred low-latency chat transport
YOUTUBE_POLL_FALLBACK = True         # REST fallback if grpc is unavailable
YOUTUBE_STREAM_RECONNECT_DELAY = 5
YOUTUBE_EMERGENCY_SEARCH = True     # last-resort discovery only
YOUTUBE_SEARCH_FALLBACK_INTERVAL = 1200  # 20 min; <=72 search calls/day


def youtube_api_error(response, default_message):
    """Return a useful, safe error description from a YouTube API response."""
    try:
        data = response.json()
        error = data.get("error", {})
        message = error.get("message", "")
        errors = error.get("errors", [])
        reason = errors[0].get("reason", "") if errors else ""

        details = []
        if reason:
            details.append(reason)
        if message:
            details.append(message)

        if details:
            return f"{default_message} ({response.status_code}: {'; '.join(details)})"
    except Exception:
        pass

    return f"{default_message} (HTTP {response.status_code})"


def resolve_youtube_channel(api_key, handle):
    """Resolve a YouTube @handle to its channel ID and uploads playlist ID."""
    handle = handle.strip()

    if handle and not handle.startswith("@"):
        handle = "@" + handle

    response = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={
            "part": "id,contentDetails",
            "forHandle": handle,
            "key": api_key,
        },
        timeout=15,
    )

    if not response.ok:
        message = youtube_api_error(
            response,
            "YouTube channel lookup failed",
        )

        # HTTP 400 means the request itself is wrong (for example an
        # invalid API key). Retrying will not help.
        if response.status_code == 400:
            raise YouTubeConfigError(message)

        raise RuntimeError(message)

    items = response.json().get("items", [])

    if not items:
        raise YouTubeConfigError(
            f"YouTube handle '{handle}' was not found."
        )

    item = items[0]
    channel_id = item.get("id", "").strip()
    uploads_playlist_id = (
        item.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
        .strip()
    )

    if not channel_id:
        raise YouTubeConfigError(
            f"YouTube handle '{handle}' did not return a channel ID."
        )

    if not uploads_playlist_id:
        raise YouTubeConfigError(
            f"YouTube channel '{channel_id}' did not return an uploads playlist."
        )

    return channel_id, uploads_playlist_id


def resolve_uploads_playlist_for_channel(api_key, channel_id):
    """Return the uploads playlist ID of a channel ID."""
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={
            "part": "contentDetails",
            "id": channel_id,
            "key": api_key,
        },
        timeout=15,
    )

    if not response.ok:
        message = youtube_api_error(
            response,
            "YouTube channel details lookup failed",
        )

        if response.status_code == 400:
            raise YouTubeConfigError(message)

        raise RuntimeError(message)

    items = response.json().get("items", [])

    if not items:
        raise YouTubeConfigError(
            f"YouTube channel '{channel_id}' was not found."
        )

    uploads_playlist_id = (
        items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
        .strip()
    )

    if not uploads_playlist_id:
        raise YouTubeConfigError(
            f"YouTube channel '{channel_id}' has no uploads playlist."
        )

    return uploads_playlist_id


def find_youtube_live_video_id(api_key, uploads_playlist_id):
    """
    Find the currently active livestream without relying on
    snippet.liveBroadcastContent.

    activeLiveChatId is the authoritative signal we need: it only exists for
    a live broadcast whose live chat is currently active.

    The function returns (video_id, live_chat_id), or (None, None).
    """
    playlist_response = requests.get(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        params={
            "part": "contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": YOUTUBE_DISCOVERY_MAX_VIDEOS,
            "key": api_key,
        },
        timeout=15,
    )

    if not playlist_response.ok:
        raise RuntimeError(
            youtube_api_error(
                playlist_response,
                "YouTube uploads playlist lookup failed",
            )
        )

    video_ids = []

    for item in playlist_response.json().get("items", []):
        video_id = (
            item.get("contentDetails", {})
            .get("videoId", "")
            .strip()
        )

        if video_id:
            video_ids.append(video_id)

    if not video_ids:
        print(
            "YouTube discovery: uploads playlist returned no videos."
        )
        return None, None

    video_response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,liveStreamingDetails,status",
            "id": ",".join(video_ids),
            "key": api_key,
        },
        timeout=15,
    )

    if not video_response.ok:
        raise RuntimeError(
            youtube_api_error(
                video_response,
                "YouTube recent-video lookup failed",
            )
        )

    items = video_response.json().get("items", [])

    inspected = 0
    live_candidates = 0

    for item in items:
        inspected += 1

        video_id = item.get("id", "").strip()
        snippet = item.get("snippet", {})
        details = item.get("liveStreamingDetails", {})
        chat_id = details.get("activeLiveChatId", "").strip()

        # Do NOT require snippet.liveBroadcastContent == "live".
        # activeLiveChatId is the signal we actually need.
        if chat_id:
            live_candidates += 1

            title = " ".join(
                str(snippet.get("title", ""))
                .split()
            )

            print(
                "YouTube discovery: active live chat candidate found: "
                f"{video_id} — {title}"
            )

            return video_id, chat_id

    print(
        "YouTube discovery: inspected "
        f"{inspected} recent videos; activeLiveChatId candidates: "
        f"{live_candidates}."
    )

    # Useful diagnostics without exposing secrets.
    if items:
        newest = items[0]
        newest_id = newest.get("id", "")
        newest_title = " ".join(
            str(newest.get("snippet", {}).get("title", ""))
            .split()
        )
        newest_state = newest.get(
            "snippet", {}
        ).get(
            "liveBroadcastContent",
            "unknown",
        )

        print(
            "YouTube discovery: newest inspected video: "
            f"{newest_id} | state={newest_state} | "
            f"title={newest_title}"
        )

    return None, None


def find_youtube_live_video_id_search_fallback(
    api_key,
    channel_id,
):
    """
    Emergency discovery fallback using search.list.

    This is intentionally throttled by the caller. It exists for the edge
    case where the current live broadcast has not yet appeared in the
    uploads playlist/API cache.

    Returns (video_id, live_chat_id), or (None, None).
    """
    if not channel_id:
        return None, None

    print(
        "YouTube discovery: running throttled Search.List fallback."
    )

    response = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video",
            "maxResults": 1,
            "key": api_key,
        },
        timeout=15,
    )

    if not response.ok:
        message = youtube_api_error(
            response,
            "YouTube Search.List fallback failed",
        )

        if response.status_code in {403, 429}:
            print(
                "YouTube discovery: Search.List fallback unavailable: "
                f"{message}"
            )
            return None, None

        raise RuntimeError(message)

    items = response.json().get("items", [])

    if not items:
        print(
            "YouTube discovery: Search.List fallback found no live video."
        )
        return None, None

    video_id = (
        items[0]
        .get("id", {})
        .get("videoId", "")
        .strip()
    )

    if not video_id:
        return None, None

    details_response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,liveStreamingDetails",
            "id": video_id,
            "key": api_key,
        },
        timeout=15,
    )

    if not details_response.ok:
        raise RuntimeError(
            youtube_api_error(
                details_response,
                "YouTube fallback video lookup failed",
            )
        )

    details_items = details_response.json().get("items", [])

    if not details_items:
        return None, None

    details = details_items[0].get(
        "liveStreamingDetails",
        {},
    )

    chat_id = details.get(
        "activeLiveChatId",
        "",
    ).strip()

    if not chat_id:
        print(
            "YouTube discovery: fallback found a live video, "
            "but activeLiveChatId is not available yet."
        )
        return None, None

    return video_id, chat_id


def discover_youtube_chat(api_key, uploads_playlist_id):
    """Discover the current live video and its active chat ID."""
    return find_youtube_live_video_id(
        api_key,
        uploads_playlist_id,
    )


def parse_youtube_api_reason(exception_text):
    """Extract a useful YouTube error reason from a requests/API error string."""
    text = str(exception_text).lower()

    if "livechatended" in text or "live chat is not active" in text:
        return "liveChatEnded"

    if "livechatdisabled" in text:
        return "liveChatDisabled"

    if "livechatnotfound" in text:
        return "liveChatNotFound"

    if "quotaexceeded" in text or "dailylimitexceeded" in text:
        return "quotaExceeded"

    if "ratelimitexceeded" in text:
        return "rateLimitExceeded"

    return "unknown"


def _extract_youtube_text_message(item):
    """Return (message_id, display_name, text) for a text chat event."""
    message_id = getattr(item, "id", "")

    snippet = getattr(item, "snippet", None)
    author = getattr(item, "author_details", None)

    if snippet is None or author is None:
        return None

    # streamList uses enum value 1 for TEXT_MESSAGE_EVENT.
    if int(getattr(snippet, "type", 0)) != 1:
        return None

    details = getattr(
        snippet,
        "text_message_details",
        None,
    )

    if details is None:
        return None

    message = getattr(details, "message_text", "")
    username = getattr(author, "display_name", "") or "YouTube"

    if not message:
        return None

    return message_id, username, message


def _build_youtube_stream_client():
    """
    Build a minimal dynamic protobuf/gRPC client for
    V3DataLiveChatMessageService.StreamList.

    The official API uses a server-streaming gRPC method. We intentionally
    define only the protobuf fields needed by this application; unknown
    fields are ignored by protobuf, so this remains compatible with the
    larger official message definition.
    """
    import grpc
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    if not hasattr(message_factory, "GetMessageClass"):
        raise ImportError(
            "protobuf is too old (message_factory.GetMessageClass is missing). "
            "Upgrade with: python -m pip install --upgrade grpcio protobuf"
        )

    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "ravaelv_stream_list_minimal.proto"
    fd.syntax = "proto2"
    fd.package = "youtube.api.v3"

    service = fd.service.add()
    service.name = "V3DataLiveChatMessageService"
    method = service.method.add()
    method.name = "StreamList"
    method.input_type = ".youtube.api.v3.LiveChatMessageListRequest"
    method.output_type = ".youtube.api.v3.LiveChatMessageListResponse"
    method.server_streaming = True
    method.client_streaming = False

    # Request
    req = fd.message_type.add()
    req.name = "LiveChatMessageListRequest"

    f = req.field.add()
    f.name = "live_chat_id"
    f.number = 1
    f.label = 1
    f.type = 9

    f = req.field.add()
    f.name = "page_token"
    f.number = 99
    f.label = 1
    f.type = 9

    f = req.field.add()
    f.name = "part"
    f.number = 100
    f.label = 3
    f.type = 9

    # Response
    resp = fd.message_type.add()
    resp.name = "LiveChatMessageListResponse"

    f = resp.field.add()
    f.name = "offline_at"
    f.number = 2
    f.label = 1
    f.type = 9

    f = resp.field.add()
    f.name = "next_page_token"
    f.number = 100602
    f.label = 1
    f.type = 9

    f = resp.field.add()
    f.name = "items"
    f.number = 1007
    f.label = 3
    f.type = 11
    f.type_name = ".youtube.api.v3.LiveChatMessage"

    # Message
    msg = fd.message_type.add()
    msg.name = "LiveChatMessage"

    f = msg.field.add()
    f.name = "id"
    f.number = 101
    f.label = 1
    f.type = 9

    f = msg.field.add()
    f.name = "snippet"
    f.number = 2
    f.label = 1
    f.type = 11
    f.type_name = ".youtube.api.v3.LiveChatMessageSnippet"

    f = msg.field.add()
    f.name = "author_details"
    f.number = 3
    f.label = 1
    f.type = 11
    f.type_name = ".youtube.api.v3.LiveChatMessageAuthorDetails"

    # Author details
    author = fd.message_type.add()
    author.name = "LiveChatMessageAuthorDetails"

    f = author.field.add()
    f.name = "display_name"
    f.number = 103
    f.label = 1
    f.type = 9

    # Text-message details
    text_details = fd.message_type.add()
    text_details.name = "LiveChatTextMessageDetails"

    f = text_details.field.add()
    f.name = "message_text"
    f.number = 1
    f.label = 1
    f.type = 9

    # Snippet and type enum
    snippet = fd.message_type.add()
    snippet.name = "LiveChatMessageSnippet"

    type_wrapper = snippet.nested_type.add()
    type_wrapper.name = "TypeWrapper"

    enum = type_wrapper.enum_type.add()
    enum.name = "Type"

    for enum_name, enum_number in (
        ("INVALID_TYPE", 0),
        ("TEXT_MESSAGE_EVENT", 1),
        ("TOMBSTONE", 2),
        ("FAN_FUNDING_EVENT", 3),
        ("CHAT_ENDED_EVENT", 4),
        ("SPONSOR_ONLY_MODE_STARTED_EVENT", 5),
        ("SPONSOR_ONLY_MODE_ENDED_EVENT", 6),
        ("NEW_SPONSOR_EVENT", 7),
        ("USER_BANNED_EVENT", 10),
        ("SUPER_CHAT_EVENT", 15),
        ("SUPER_STICKER_EVENT", 16),
        ("MEMBER_MILESTONE_CHAT_EVENT", 17),
        ("MEMBERSHIP_GIFTING_EVENT", 18),
        ("GIFT_MEMBERSHIP_RECEIVED_EVENT", 19),
        ("POLL_EVENT", 20),
        ("GIFT_EVENT", 21),
    ):
        enum_value = enum.value.add()
        enum_value.name = enum_name
        enum_value.number = enum_number

    f = snippet.field.add()
    f.name = "type"
    f.number = 1
    f.label = 1
    f.type = 14
    f.type_name = (
        ".youtube.api.v3.LiveChatMessageSnippet.TypeWrapper.Type"
    )

    f = snippet.field.add()
    f.name = "text_message_details"
    f.number = 19
    f.label = 1
    f.type = 11
    f.type_name = ".youtube.api.v3.LiveChatTextMessageDetails"

    pool = descriptor_pool.DescriptorPool()

    try:
        pool.Add(fd)
    except Exception:
        # Fresh pool per call normally prevents this path, but keep the
        # function resilient if protobuf internals change.
        pass

    request_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName(
            "youtube.api.v3.LiveChatMessageListRequest"
        )
    )
    response_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName(
            "youtube.api.v3.LiveChatMessageListResponse"
        )
    )

    credentials = grpc.ssl_channel_credentials()

    channel = grpc.secure_channel(
        "dns:///youtube.googleapis.com:443",
        credentials,
        options=[
            ("grpc.keepalive_time_ms", 60000),
            ("grpc.keepalive_timeout_ms", 20000),
        ],
    )

    stream_rpc = channel.unary_stream(
        "/youtube.api.v3.V3DataLiveChatMessageService/StreamList",
        request_serializer=lambda request: request.SerializeToString(),
        response_deserializer=response_cls.FromString,
    )

    return channel, request_cls, stream_rpc


def youtube_stream_list_chat(
    api_key,
    live_chat_id,
    remember_message_id,
    next_page_token=None,
):
    """
    Consume YouTube live chat with the official server-streaming API.

    Returns:
        ("ended", next_page_token) when the live chat ended.
        ("reconnect", next_page_token) when the stream dropped temporarily.
    """
    try:
        import grpc
    except ImportError:
        raise RuntimeError(
            "grpcio is not installed. "
            "Install it with: python -m pip install --upgrade grpcio protobuf"
        )

    channel, request_cls, stream_rpc = _build_youtube_stream_client()

    try:
        page_token = next_page_token

        while True:
            request = request_cls(
                live_chat_id=live_chat_id,
                part=[
                    "id",
                    "snippet",
                    "authorDetails",
                ],
            )

            if page_token:
                request.page_token = page_token

            metadata = (
                ("x-goog-api-key", api_key),
            )

            try:
                responses = stream_rpc(
                    request,
                    metadata=metadata,
                )

                got_response = False

                for response in responses:
                    got_response = True

                    if getattr(response, "offline_at", ""):
                        print(
                            "YouTube chat: livestream ended "
                            "(streamList offlineAt)."
                        )
                        return "ended", getattr(
                            response,
                            "next_page_token",
                            "",
                        )

                    for item in getattr(response, "items", []):
                        parsed = _extract_youtube_text_message(item)

                        if not parsed:
                            continue

                        message_id, username, message = parsed

                        add_message(
                            "Y",
                            username,
                            message,
                            source_message_id=message_id,
                        )

                    page_token = getattr(
                        response,
                        "next_page_token",
                        "",
                    )

                if not got_response:
                    time.sleep(
                        YOUTUBE_STREAM_RECONNECT_DELAY
                    )
                else:
                    time.sleep(1)

            except grpc.RpcError as e:
                status = e.code()
                details = e.details() or ""
                detail_text = details.lower()

                if (
                    status in {
                        grpc.StatusCode.NOT_FOUND,
                        grpc.StatusCode.FAILED_PRECONDITION,
                    }
                    and (
                        "live_chat_ended" in detail_text
                        or "live_chat_disabled" in detail_text
                        or "live_chat_not_found" in detail_text
                    )
                ):
                    return "ended", page_token

                if status == grpc.StatusCode.PERMISSION_DENIED and (
                    "live_chat_ended" in detail_text
                    or "live_chat_disabled" in detail_text
                    or "live_chat_not_found" in detail_text
                ):
                    return "ended", page_token

                print(
                    "YouTube streamList: temporary stream error: "
                    f"{status.name}: {details}. "
                    f"Reconnecting in {YOUTUBE_STREAM_RECONNECT_DELAY}s."
                )

                time.sleep(
                    YOUTUBE_STREAM_RECONNECT_DELAY
                )

    finally:
        channel.close()


def youtube_poll_chat(
    api_key,
    live_chat_id,
    remember_message_id,
    page_token=None,
):
    """
    REST polling fallback for installations that do not have grpcio.
    """
    while True:
        params = {
            "liveChatId": live_chat_id,
            "part": "id,snippet,authorDetails",
            "maxResults": YOUTUBE_MAX_RESULTS,
            "key": api_key,
        }

        if page_token:
            params["pageToken"] = page_token

        response = requests.get(
            "https://www.googleapis.com/youtube/v3/liveChat/messages",
            params=params,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                youtube_api_error(
                    response,
                    "YouTube live chat request failed",
                )
            )

        data = response.json()

        if data.get("offlineAt"):
            return "ended", page_token

        for item in data.get("items", []):
            message_id = item.get("id", "")

            snippet = item.get(
                "snippet",
                {},
            )

            if snippet.get("type") != "textMessageEvent":
                continue

            author = item.get(
                "authorDetails",
                {},
            )

            message = snippet.get(
                "textMessageDetails",
                {},
            ).get(
                "messageText",
                "",
            )

            add_message(
                "Y",
                author.get(
                    "displayName",
                    "YouTube",
                ),
                message,
                source_message_id=message_id,
            )

        page_token = data.get(
            "nextPageToken"
        )

        delay = data.get(
            "pollingIntervalMillis",
            1000,
        )

        time.sleep(
            max(int(delay), 500) / 1000
        )


def youtube_worker(config):
    youtube = config.get("youtube", {})

    if not youtube.get("enabled", False):
        print("YouTube chat disabled.")
        return

    api_key = youtube.get("api_key", "").strip()
    handle = youtube.get("handle", "").strip()
    channel_id = youtube.get("channel_id", "").strip()
    uploads_playlist_id = youtube.get(
        "uploads_playlist_id",
        "",
    ).strip()

    if not api_key:
        print("YouTube chat: API key is not configured.")
        return

    if not handle and not channel_id and not uploads_playlist_id:
        print(
            "YouTube chat: configure 'handle', 'channel_id', "
            "or 'uploads_playlist_id'."
        )
        return

    def resolve_with_retry(description, resolver):
        """Run a lookup, retrying temporary failures."""
        while True:
            print(description)

            try:
                return resolver()
            except YouTubeConfigError as e:
                print(
                    f"YouTube chat: lookup failed: {e}"
                )
                return None
            except Exception as e:
                print(
                    f"YouTube chat: lookup failed: {e}. "
                    f"Retrying in {YOUTUBE_DISCOVERY_INTERVAL}s."
                )
                time.sleep(
                    YOUTUBE_DISCOVERY_INTERVAL
                )

    # Quota-efficient precedence:
    # uploads_playlist_id > channel_id > handle.
    if not uploads_playlist_id and channel_id:
        uploads_playlist_id = resolve_with_retry(
            (
                "YouTube chat: resolving uploads playlist "
                f"for {channel_id}..."
            ),
            lambda: resolve_uploads_playlist_for_channel(
                api_key,
                channel_id,
            ),
        )

        if not uploads_playlist_id:
            return

        print(
            "YouTube chat: uploads playlist resolved to "
            f"{uploads_playlist_id}"
        )

        youtube["uploads_playlist_id"] = uploads_playlist_id

        try:
            update_config_section(
                "youtube",
                {"uploads_playlist_id": uploads_playlist_id},
            )
        except (OSError, ValueError) as e:
            print(
                "YouTube chat: could not cache uploads playlist ID: "
                f"{e}"
            )

    elif not uploads_playlist_id and handle:
        result = resolve_with_retry(
            f"YouTube chat: resolving channel handle '{handle}'...",
            lambda: resolve_youtube_channel(
                api_key,
                handle,
            ),
        )

        if result is None:
            return

        channel_id, uploads_playlist_id = result

        print(
            f"YouTube chat: channel ID resolved to {channel_id}"
        )
        print(
            "YouTube chat: uploads playlist resolved to "
            f"{uploads_playlist_id}"
        )

        # Cache the resolved identifiers so future starts do not need
        # another handle lookup.
        youtube["channel_id"] = channel_id
        youtube["uploads_playlist_id"] = uploads_playlist_id
        config["youtube"] = youtube

        try:
            update_config_section(
                "youtube",
                {
                    "channel_id": channel_id,
                    "uploads_playlist_id": uploads_playlist_id,
                },
            )
        except (OSError, ValueError) as e:
            print(
                "YouTube chat: could not cache resolved channel IDs: "
                f"{e}"
            )

    if not uploads_playlist_id:
        print(
            "YouTube chat: no uploads playlist ID is available."
        )
        return

    current_video_id = None
    current_chat_id = None
    page_token = None
    last_emergency_search = 0.0

    seen_ids = deque(
        maxlen=YOUTUBE_MAX_SEEN_IDS
    )
    seen_id_set = set()

    def remember_message_id(message_id):
        if not message_id:
            return False

        if message_id in seen_id_set:
            return True

        if len(seen_ids) >= seen_ids.maxlen:
            oldest = seen_ids.popleft()
            seen_id_set.discard(oldest)

        seen_ids.append(message_id)
        seen_id_set.add(message_id)
        return False

    print(
        "YouTube chat: automatic livestream discovery enabled "
        "(uploads playlist first; throttled Search.List fallback)."
    )
    print(
        "YouTube chat: channel ID = "
        f"{channel_id} | uploads playlist = {uploads_playlist_id}"
    )

    while True:
        try:
            if not current_chat_id:
                print(
                    "YouTube chat: looking for an active livestream..."
                )

                discovered_video_id, discovered_chat_id = (
                    discover_youtube_chat(
                        api_key,
                        uploads_playlist_id,
                    )
                )

                if not discovered_video_id:
                    now = time.time()

                    # The normal path is quota-cheap and uses the uploads
                    # playlist. Only use Search.List occasionally as an
                    # emergency fallback for API propagation edge cases.
                    if (
                        YOUTUBE_EMERGENCY_SEARCH
                        and channel_id
                        and (
                            now - last_emergency_search
                            >= YOUTUBE_SEARCH_FALLBACK_INTERVAL
                        )
                    ):
                        last_emergency_search = now

                        try:
                            (
                                discovered_video_id,
                                discovered_chat_id,
                            ) = find_youtube_live_video_id_search_fallback(
                                api_key,
                                channel_id,
                            )
                        except Exception as fallback_error:
                            print(
                                "YouTube discovery: Search.List fallback "
                                f"failed: {fallback_error}"
                            )

                    if not discovered_video_id:
                        print(
                            "YouTube chat: no active livestream found. "
                            f"Retrying in {YOUTUBE_DISCOVERY_INTERVAL}s."
                        )

                        time.sleep(
                            YOUTUBE_DISCOVERY_INTERVAL
                        )
                        continue

                current_video_id = discovered_video_id
                current_chat_id = discovered_chat_id
                page_token = None

                print(
                    "YouTube chat: livestream detected: "
                    f"https://www.youtube.com/watch?v={current_video_id}"
                )
                print(
                    "YouTube chat: live chat connected."
                )

            if YOUTUBE_USE_STREAM_LIST:
                try:
                    result, page_token = (
                        youtube_stream_list_chat(
                            api_key,
                            current_chat_id,
                            remember_message_id,
                            page_token,
                        )
                    )

                    if result == "ended":
                        current_video_id = None
                        current_chat_id = None
                        page_token = None
                        print(
                            "YouTube chat: current livestream ended. "
                            "Waiting for next discovery."
                        )
                        time.sleep(
                            YOUTUBE_DISCOVERY_INTERVAL
                        )

                    continue

                except ImportError as e:
                    if not YOUTUBE_POLL_FALLBACK:
                        raise

                    print(
                        f"YouTube chat: streamList unavailable ({e}); "
                        "falling back to REST polling."
                    )

                except RuntimeError as e:
                    if (
                        "grpcio is not installed" in str(e)
                        and YOUTUBE_POLL_FALLBACK
                    ):
                        print(
                            "YouTube chat: grpcio unavailable; "
                            "falling back to REST polling."
                        )
                    else:
                        raise

            result, page_token = youtube_poll_chat(
                api_key,
                current_chat_id,
                remember_message_id,
                page_token,
            )

            if result == "ended":
                current_video_id = None
                current_chat_id = None
                page_token = None
                print(
                    "YouTube chat: current livestream ended. "
                    "Waiting for next discovery."
                )
                time.sleep(
                    YOUTUBE_DISCOVERY_INTERVAL
                )

        except Exception as e:
            reason = parse_youtube_api_reason(e)

            print(
                f"YouTube chat error: {e}"
            )

            if reason in {
                "liveChatEnded",
                "liveChatNotFound",
                "liveChatDisabled",
            }:
                current_video_id = None
                current_chat_id = None
                page_token = None

                time.sleep(
                    YOUTUBE_DISCOVERY_INTERVAL
                )
                continue

            if reason == "quotaExceeded":
                print(
                    "YouTube chat: API quota exhausted. "
                    f"Waiting {YOUTUBE_QUOTA_BACKOFF // 60} minutes."
                )
                time.sleep(
                    YOUTUBE_QUOTA_BACKOFF
                )
                continue

            if not current_chat_id:
                time.sleep(
                    YOUTUBE_DISCOVERY_INTERVAL
                )
                continue

            if reason == "rateLimitExceeded":
                time.sleep(15)
            else:
                time.sleep(
                    YOUTUBE_RETRY_INTERVAL
                )


# =============================================================
# TikTok placeholder
# =============================================================

def tiktok_worker(config):
    tiktok = config.get("tiktok", {})

    if not tiktok.get("enabled", False):
        print("TikTok chat disabled.")
        return

    print(
        "TikTok chat is enabled, but no official "
        "general LIVE-chat adapter is configured."
    )


# =============================================================
# Main
# =============================================================

def main():
    global native_overlay

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    init_chat_database()

    if OVERLAY_WINDOW_ENABLED:
        native_overlay = NativeChatOverlay()
        native_overlay.start()


    # Always start from an empty panel. Otherwise the stream would briefly
    # show the previous session's chat until the first new message arrives.
    try:
        atomic_write_text(OUTPUT_FILE, "Waiting for messages...")
    except OSError as e:
        print(f"Overlay reset failed: {e}")

    try:
        config = load_config()
    except FileNotFoundError:
        print(f"Config file not found: {CONFIG_FILE}")
        close_chat_database()
        return
    except json.JSONDecodeError as e:
        print(f"Config file {CONFIG_FILE} is not valid JSON: {e}")
        close_chat_database()
        return

    workers = [
        threading.Thread(
            target=twitch_worker,
            args=(config,),
            daemon=True,
        ),
        threading.Thread(
            target=youtube_worker,
            args=(config,),
            daemon=True,
        ),
        threading.Thread(
            target=tiktok_worker,
            args=(config,),
            daemon=True,
        ),
    ]

    for worker in workers:
        worker.start()

    print("Unified chat aggregator running.")
    print(f"Overlay file: {OUTPUT_FILE}")
    print(f"Chat database: {CHAT_DATABASE_FILE}")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nChat aggregator stopped.")
    finally:
        if native_overlay is not None:
            native_overlay.stop()
            native_overlay = None
        close_chat_database()


if __name__ == "__main__":
    main()
