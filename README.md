<div align="center">

# Multistream — Python + FFmpeg + MediaMTX

**Encode once on your Windows PC. Relay everywhere.**

A Windows live-streaming stack built on Python, FFmpeg, MediaMTX and NVIDIA NVENC — no OBS required.
Twitch and YouTube chat are merged into one on-stream panel, a native desktop chat window, and a searchable history.

![Platform](https://img.shields.io/badge/platform-Windows%2011-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-required-007808?logo=ffmpeg&logoColor=white)
![Encoder](https://img.shields.io/badge/encoder-NVIDIA%20NVENC-76B900?logo=nvidia&logoColor=white)
![Relay](https://img.shields.io/badge/relay-MediaMTX-1f6feb)
![Twitch](https://img.shields.io/badge/Twitch-live-9146FF?logo=twitch&logoColor=white)

</div>

---

## Overview

USERNAME Multistream captures the desktop, a Logitech BRIO webcam and the BRIO microphone, composes them with a webcam overlay and broadcast graphics, encodes **one** H.264/AAC stream with NVENC, and publishes it over RTMP to a local [MediaMTX](https://mediamtx.org/) server. MediaMTX then relays that already-encoded stream to streaming platforms.

A separate Python process, the **chat aggregator**, reads Twitch and YouTube chat and feeds three consumers: the chat panel inside the stream, an optional native Windows chat window, and a SQLite history database.

> **Core idea:** encode one stream on the PC, then let MediaMTX relay the already-encoded stream to multiple platforms — no re-encoding per platform.

### Current status

| Capability | Status |
|---|---|
| Twitch streaming | ✅ Working |
| Twitch chat (IRC/TLS) | ✅ Working |
| Twitch OAuth validation and refresh | ✅ Working |
| YouTube chat | ✅ Working — automatic livestream discovery, `streamList` with REST fallback |
| Native desktop chat window | ✅ Working |
| Chat history (SQLite) | ✅ Working |
| YouTube stream relay | 🧪 Configurable through MediaMTX forwarding |
| TikTok chat | ⏸️ Disabled |

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tested Environment](#tested-environment)
- [Quick Start](#quick-start)
- [Repository Structure](#repository-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [MediaMTX Configuration](#mediamtx-configuration)
- [Encoding Profile](#encoding-profile)
- [BRIO Webcam and Microphone](#brio-webcam-and-microphone)
- [Stream Graphics](#stream-graphics)
- [Unified Chat](#unified-chat)
- [Platform Setup](#platform-setup)
- [Configuration Reference](#configuration-reference)
- [Running the System](#running-the-system)
- [Performance and Realtime Targets](#performance-and-realtime-targets)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Design Principles](#design-principles)
- [Roadmap](#roadmap)
- [References](#references)
- [License](#license)

---

## Features

**Video**
- Full Windows desktop capture (`gdigrab`)
- Logitech BRIO 4K Stream Edition webcam and microphone (DirectShow)
- Webcam picture-in-picture overlay
- Broadcast graphics rendered inside FFmpeg
- NVIDIA NVENC H.264 hardware encoding, AAC audio
- Python-controlled FFmpeg pipeline, no OBS dependency

**Relay**
- Local RTMP ingest through MediaMTX
- MediaMTX stream forwarding to platform ingest servers

**Chat**
- Twitch chat over IRC/TLS, with runtime-only access tokens, automatic refresh and Device Code Flow fallback
- YouTube Live Chat with automatic livestream discovery from the channel `@handle`
- Low-latency YouTube chat through `liveChatMessages.streamList` (gRPC), with REST polling as a fallback
- Chat panel inside the stream, rendered by FFmpeg
- Native Windows chat window: draggable, resizable, lockable and click-through, with search and history scrolling
- SQLite chat history with sessions, participants and timestamps

**Launcher**
- `StreamStart.bat` starts MediaMTX, the stream and the chat aggregator in order

---

## Architecture

### Video path

```text
desktop (gdigrab 1920x1080) + BRIO webcam (MJPEG 640x360) + BRIO microphone + chat_overlay.txt
    -> FFmpeg filters (scale, overlay, drawbox, drawtext)
    -> NVENC H.264 + AAC, 1280x720 @ 30 FPS
    -> RTMP  rtmp://127.0.0.1/live
    -> MediaMTX
    -> Twitch          (YouTube: experimental, TikTok: disabled)
```

### Chat path

```text
Twitch IRC (TLS) ---------------.
YouTube streamList (gRPC) ------+--> chat aggregator --+--> chat_overlay.txt --> FFmpeg drawtext --> stream
YouTube REST polling (fallback) |                       +--> native chat window (desktop)
TikTok (disabled) --------------'                       +--> chat_history.sqlite3
```

The chat process is deliberately separate from the video encoder: **a chat failure never stops the video stream.**

### YouTube discovery

The YouTube worker finds the current livestream from the channel's uploads playlist instead of `search.list`:

```text
1. channels.list        @handle -> channel ID + uploads playlist ID   (cached in chat_config.json)
2. playlistItems.list   recent uploads (up to 50 video IDs)
3. videos.list          the video that has an activeLiveChatId
4. streamList           follow that live chat (REST polling if gRPC is unavailable)
```

Discovery only runs when there is no current chat or when the current livestream ends. A throttled `search.list` call remains as a last-resort fallback; see [YouTube](#youtube) for its cost.

---

## Tested Environment

| Component | Tested value |
|---|---|
| Operating system | Windows 11 |
| Python | 3.14.x |
| FFmpeg | 9.0.1 |
| MediaMTX | v1.21.0 |
| Python wrapper | `ffmpeg-python` |
| GPU | NVIDIA GeForce GTX 1650 |
| Integrated GPU | Intel UHD Graphics 630 |
| Webcam | Logitech BRIO 4K Stream Edition |
| Desktop capture | `gdigrab` |
| Webcam capture | DirectShow / MJPEG |
| Final video | 1280×720 @ 30 FPS |
| Video codec | H.264 NVENC |
| Video bitrate | 4000 kbps CBR |
| Audio codec | AAC |
| Audio bitrate | 128 kbps |
| Audio sample rate | 48 kHz |
| GOP | 60 frames (2 seconds) |
| Local RTMP | `rtmp://127.0.0.1/live` |

> [!NOTE]
> This is the tested baseline, not a requirement that every installation use exactly these versions.

---

## Quick Start

Assumes Python, FFmpeg (with `h264_nvenc`) and MediaMTX are already installed. Full setup is in [Installation](#installation).

**One click:** run `StreamStart.bat`. It checks that every required file exists, then opens three windows:

```text
[1/3] MediaMTX
[2/3] MultistreamApp          (3 s later)
[3/3] Chat Aggregator         (3 s later)
```

**Manually:** use three PowerShell windows.

```powershell
# 1. Relay
cd C:\MediaMTX
.\mediamtx.exe
```

```powershell
# 2. Stream
cd C:\RTMPStreamer
.\.venv\Scripts\python.exe MultistreamApp.py
```

```powershell
# 3. Chat aggregator
cd C:\RTMPStreamer
```powershell
# 3. Chat aggregator
cd C:\RTMPStreamer
.\.venv\Scripts\Activate.ps1
python MultistreamApp.py
.\.venv\Scripts\python.exe chat_aggregator_auto_refresh_youtube_auto.py
```
```

---

## Repository Structure

The project lives in one folder. Every script and the launcher find their files relative to their own location, so the folder can be placed anywhere.

```text
PROJECT\
├── README.md
├── LICENSE
├── SECURITY.md
├── .gitignore
├── .gitattributes
├── requirements.txt
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SETUP.md
│   ├── CHAT.md
│   └── TROUBLESHOOTING.md
├── FFmpeg\                       # optional bundled FFmpeg (bin\ffmpeg.exe or ffmpeg.exe)
├── MediaMTX\
│   ├── mediamtx.exe
│   └── mediamtx.yml              # may contain your stream key — keep it out of git
├── RTMPStreamer\
│   ├── StreamStart.bat           # Used to start everything at once after you setup the project properly.
│   ├── MultistreamApp.py
│   ├── chat_aggregator_auto_refresh_youtube_auto.py
│   ├── requirements.txt
│   ├── .venv\
│   ├── chat_config.json           # private — never commit
│   ├── chat_overlay.txt           # generated: chat panel text for FFmpeg
│   ├── chat_history.sqlite3       # generated: chat history (plus -wal / -shm files)
│   └── chat_overlay_window.json   # generated: native window position, size, lock, visibility

## Requirements

**Hardware**
- Windows 11 PC
- NVIDIA GPU with NVENC support (tested: GTX 1650)
- Webcam and microphone (tested: Logitech BRIO 4K Stream Edition)
- Stable network with sufficient upload capacity

**Software**
- Python
- FFmpeg with `h264_nvenc` (on `PATH`, or in the project's `FFmpeg` folder when using `StreamStart.bat`)
- MediaMTX
- Python packages: `ffmpeg-python`, `requests`, `grpcio`, `protobuf`
- Twitch CLI, with `twitch` available on `PATH` (optional — only needed for the Device Code Flow fallback)

`grpcio` and `protobuf` power the YouTube `streamList` transport. Without them the aggregator still works and falls back to REST polling.

---

## Installation

### 1. Python and virtual environment

Download Python from <https://www.python.org/downloads/windows/>, then verify:

```powershell
python --version
python -m pip --version
```

Create the project and a virtual environment:

```powershell
New-Item -ItemType Directory -Path C:\RTMPStreamer -Force
cd C:\RTMPStreamer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

<details>
<summary>PowerShell blocks activation?</summary>

Use the `cmd` activation script instead:

```powershell
cmd /k C:\RTMPStreamer\.venv\Scripts\activate.bat
```

</details>

### 2. Python packages

```powershell
python -m pip install --upgrade pip
python -m pip install ffmpeg-python requests
python -m pip install --upgrade grpcio protobuf
python -m pip freeze > requirements.txt
```

> [!IMPORTANT]
> `ffmpeg-python` is only a Python wrapper. It does **not** install `ffmpeg.exe`.

> [!NOTE]
> Keep `grpcio` and `protobuf` up to date. An old `protobuf` cannot build the `streamList` client; the aggregator then reports the problem and uses REST polling instead.

### 3. FFmpeg

Install a Windows FFmpeg build and make sure it is on `PATH` (or, for the launcher only, unpack it into the `FFmpeg` folder next to `RTMPStreamer`):

```powershell
ffmpeg -version
where.exe ffmpeg
```

Tested version: `FFmpeg 9.0.1`.

### 4. Verify NVIDIA NVENC

```powershell
ffmpeg -hide_banner -encoders | findstr /i "h264_nvenc"
```

Expected output includes `h264_nvenc`. Without it, the production stream cannot use the intended hardware encoding path.

### 5. Launcher

`StreamStart.bat` sits in the `RTMPStreamer` folder and needs no editing. It derives every path from its own location:

| Item | Expected location |
|---|---|
| Scripts, `.venv`, `chat_config.json` | Same folder as `StreamStart.bat` |
| MediaMTX | `..\MediaMTX\mediamtx.exe` |
| FFmpeg | `..\FFmpeg\bin\ffmpeg.exe` or `..\FFmpeg\ffmpeg.exe`; otherwise FFmpeg must be on `PATH` |

Before starting anything it checks that MediaMTX, the virtual environment, both scripts, `chat_config.json` and FFmpeg are found, and stops with a clear message if one is missing. A bundled FFmpeg is added to `PATH` for the windows it opens, so a system-wide install is optional.

---

## MediaMTX Configuration

### Install

```powershell
New-Item -ItemType Directory -Path C:\MediaMTX -Force
```

Download MediaMTX from <https://github.com/bluenviron/mediamtx/releases> and place:

```text
C:\MediaMTX\mediamtx.exe
C:\MediaMTX\mediamtx.yml
```

Verify and validate (tested: `v1.21.0`):

```powershell
cd C:\MediaMTX
.\mediamtx.exe --version
.\mediamtx.exe --validate-conf=.\mediamtx.yml
```

Start:

```powershell
.\mediamtx.exe
```

Expected log lines:

```text
configuration loaded from C:\MediaMTX\mediamtx.yml
[RTMP] started with listener on :1935
```

### RTMP path

The production publisher uses `rtmp://127.0.0.1/live`. The path must accept a publisher:

```yaml
paths:
  live:
    source: publisher
```

Verify the listener:

```powershell
Test-NetConnection 127.0.0.1 -Port 1935
```

Expected: `TcpTestSucceeded : True`.

---

## Encoding Profile

The first implementation used `libx264`. The desktop + webcam + overlay pipeline measured **below realtime**, so production uses `h264_nvenc` on the GTX 1650.

| Setting | Value |
|---|---|
| Resolution | 1280×720 |
| Frame rate | 30 FPS |
| Codec | H.264 NVENC (`p4`, tune `ll`) |
| Rate control | CBR |
| Bitrate | 4000 kbps (maxrate 4000k, bufsize 8000k) |
| GOP | 60 frames (2 s) |
| B-frames | 0 |
| Profile / pixel format | `high` / `yuv420p` |
| Audio | AAC, 128 kbps, 48 kHz, stereo |

These values are constants at the top of `MultistreamApp.py` (`VIDEO_BITRATE`, `VIDEO_MAXRATE`, `VIDEO_BUFSIZE`, `AUDIO_BITRATE`, `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `GOP_SECONDS`).

Healthy target:

```text
fps ≈ 30    speed ≈ 1.00x    dup ≈ 0    drop ≈ 0
```

### Pipeline

```text
inputs
    desktop           1920x1080 -> scaled to 1280x720
    BRIO webcam       640x360 MJPEG -> scaled to 320x180
    BRIO microphone   48 kHz stereo
filters
    webcam overlay (bottom-right)
    broadcast graphics + chat panel
encode
    NVENC H.264 4000 kbps + AAC 128 kbps
output
    RTMP /live -> MediaMTX
```

---

## BRIO Webcam and Microphone

List devices:

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

Production devices:

| Role | Device name |
|---|---|
| Webcam | `BRIO 4K Stream Edition` |
| Microphone | `Microphone (BRIO 4K Stream Edition)` |

Check supported camera modes:

```powershell
ffmpeg -list_options true -f dshow -i video="BRIO 4K Stream Edition"
```

Production capture is **640×360 @ 30 FPS MJPEG**, scaled to **320×180** for the final picture-in-picture. A 320×180 overlay doesn't need 1080p or 4K capture, and the lower capture resolution reduces USB and decode pressure.

---

## Stream Graphics

All graphics are rendered inside the FFmpeg pipeline. OBS is not required.

| Position | Element |
|---|---|
| Top-left | `LIVE STREAM` / `Twitch.tv/USERNAME` |
| Top-right | `● LIVE` |
| Bottom-left | `LIVE CHAT` header + chat panel |
| Bottom-right | BRIO webcam picture-in-picture |

The title, channel name and social text come from `STREAM_TITLE`, `CHANNEL_NAME` and `SOCIAL_TEXT` in `MultistreamApp.py`. Panel geometry comes from the `CHAT_*` constants.

### Chat panel

FFmpeg reads `chat_overlay.txt` with `drawtext` and reloads it twice per second (`CHAT_RELOAD_FRAMES`). Three details keep it reliable:

- **`expansion=none`** — chat text is untrusted. With `drawtext`'s default expansion, a single `%` in any message makes FFmpeg skip rendering *all* chat text, and `%{...}` would be evaluated as a function.
- **Bottom-anchored text** — the newest line sits at the bottom of the panel and older lines grow upward, so new messages are never pushed out of view.
- **Bounded file** — the aggregator writes at most `STREAM_PANEL_MAX_LINES` (11) lines, so the text always fits the panel.

`MultistreamApp.py` also creates a placeholder `chat_overlay.txt` if none exists, because `drawtext` cannot start without its text file.

---

## Unified Chat

The aggregator merges platform messages into one local feed:

```text
[T]  username: message
[YT] username: message
[TT] username: message
```

Every accepted message goes to three places.

### 1. Stream panel (`chat_overlay.txt`)

- shows the newest messages that fit in **11 lines**, oldest first;
- wraps lines at **42 characters**;
- keeps whole messages, cutting any single message to **3 lines** with an ellipsis;
- writes atomically (temp file, then replace) under one lock, retrying briefly if Windows has the file locked, so FFmpeg never reads a half-written file;
- resets to `Waiting for messages...` when the aggregator starts, so a previous session's chat never appears on stream.

### 2. Native chat window

An optional borderless, always-on-top Windows window, built with `ctypes` and GDI — no Tkinter, browser or extra package. It redraws only when chat changes.

| Feature | Behavior |
|---|---|
| Order | Newest message at the top |
| New messages | Highlighted in bright green and blinking for 5 seconds |
| Move / resize | Drag anywhere to move; drag the edges to resize |
| Lock | `Ctrl+Alt+L` locks the position and makes the window click-through |
| Hide / show | `Ctrl+Alt+H` |
| Search | Search box with suggestions; matches username, message text and platform |
| History | The right scrollbar and mouse wheel scroll through the complete SQLite history |
| Persistence | Position, size, lock and visibility are saved to `chat_overlay_window.json` |

> [!NOTE]
> The native window is an ordinary window on your desktop, so `gdigrab` normally captures it like any other window. If you don't want it in the stream, move it outside the captured area (for example to a second monitor), hide it with `Ctrl+Alt+H`, or set `OVERLAY_WINDOW_ENABLED = False`.

### 3. Chat history (SQLite)

`chat_history.sqlite3` stores every accepted message with a local timestamp. Each run starts a new session.

| Table | Contents |
|---|---|
| `sessions` | One row per aggregator run (`started_at`, `ended_at`) |
| `participants` | Per session, platform and username: first/last seen and message count |
| `messages` | Session, timestamp, platform, username and message text |

Example:

```sql
SELECT created_at, platform, username, message
FROM messages
ORDER BY id DESC
LIMIT 20;
```

The database uses WAL mode, so `-wal` and `-shm` files appear next to it while the aggregator runs. A database error (disk full, file locked by another program) is printed but never interrupts chat.

### Twitch OAuth

The aggregator treats the Twitch access token as **runtime-only**. It is not required in `chat_config.json`.

Persistent configuration contains:

```json
"twitch": {
    "enabled": true,
    "username": "USERNAME",
    "client_id": "YOUR_TWITCH_CLIENT_ID",
    "client_secret": "YOUR_TWITCH_CLIENT_SECRET",
    "refresh_token": "YOUR_TWITCH_REFRESH_TOKEN",
    "channel": "USERNAME"
}
```

At startup, the aggregator:

1. uses the stored refresh token to obtain an access token;
2. validates it against Twitch — the token's Client ID must match `client_id`, its login must match `username`, and it must include `chat:read`;
3. keeps the access token in memory only and uses it for Twitch IRC;
4. stores a rotated refresh token back to `chat_config.json` (atomic write);
5. falls back to Twitch CLI Device Code Flow when there is no refresh token **or** the refresh fails.

While running, it re-validates the token every hour, refreshes it when 10 minutes or less remain, and reconnects IRC after a refresh so the new token is used. Temporary network or Twitch-server problems are retried and never open the browser flow; only a definite rejection of the refresh token does.

A quiet chat is normal. After 60 seconds of silence the aggregator pings Twitch, and it reconnects only if nothing comes back within 150 seconds (or if Twitch sends `RECONNECT`). Repeated failures back off from 3 s up to 60 s.

The Device Code Flow fallback runs `twitch configure` with your Client ID/Secret, then `twitch token -u --dcf -s "chat:read"`. It needs the `twitch` executable on `PATH`, avoids the localhost redirect flow, and automatically opens the Twitch activation page when the CLI prints the device-flow URL. Token values in the CLI output are redacted from the console. The flow is abandoned after 10 minutes if you never authorize it.

**Never commit `chat_config.json` to GitHub.**

---

## Platform Setup

### Twitch

**Streaming** uses a stream key. The stream key is **not** the chat OAuth token. Conceptually, MediaMTX forwarding looks like:

```yaml
paths:
  live:
    source: publisher
    forward:
      - dest: rtmps://YOUR_TWITCH_INGEST/app#YOUR_TWITCH_STREAM_KEY
```

**Chat** uses Twitch IRC over TLS at `irc.chat.twitch.tv:6697` with the read-only scope `chat:read`. See the [Twitch IRC docs](https://dev.twitch.tv/docs/chat/irc).

**OAuth** — access tokens are obtained at runtime from the stored refresh token; see [Twitch OAuth](#twitch-oauth). Reference docs: [validating](https://dev.twitch.tv/docs/authentication/validate-tokens/) and [refreshing](https://dev.twitch.tv/docs/authentication/refresh-tokens/) tokens.

### YouTube

Chat uses the [YouTube Live Chat API](https://developers.google.com/youtube/v3/live/docs/liveChatMessages). The aggregator authenticates with the API key alone; it does not use an OAuth sign-in for YouTube.

**Discovery**

- resolves the configured `@handle` to a channel ID and uploads playlist ID, then caches both in `chat_config.json` so later starts skip the lookup;
- inspects the channel's **50 most recent uploads** with `playlistItems.list` and `videos.list`;
- treats a video that has an `activeLiveChatId` as the live one;
- with no live stream, retries every **60 seconds** (2 quota units per attempt);
- returns to discovery automatically when the broadcast ends.

Identifier precedence is `uploads_playlist_id`, then `channel_id`, then `handle`.

**Chat transport**

- **`streamList` (default):** a server-streaming gRPC connection to `youtube.googleapis.com:443`, authenticated with the API key. Messages arrive with low latency and no polling. It reconnects after 5 seconds on a temporary error.
- **REST polling (fallback):** `liveChatMessages.list`, following the API's `pollingIntervalMillis` and requesting up to 200 messages per poll. Used automatically when `grpcio` or `protobuf` is missing or too old.

Only text messages are shown. The last 500 message IDs are remembered to drop duplicates.

**Errors**

- `rateLimitExceeded` → wait 15 s; other polling errors → retry after 5 s;
- `quotaExceeded` → wait 15 minutes;
- an unknown handle or invalid API key stops the YouTube worker, since retrying can't fix it; temporary lookup errors are retried.

> [!WARNING]
> **Emergency `search.list` fallback.** If the uploads playlist shows no live stream, the worker calls `search.list` at most once every 20 minutes (`YOUTUBE_SEARCH_FALLBACK_INTERVAL = 1200`) to catch a broadcast that hasn't appeared in the playlist yet. Google documents `search.list` at **100 quota units per call**, so this is up to 72 calls (7,200 units) per day. Together with normal discovery (about 2,880 units per day at one attempt per minute) an aggregator left running 24 hours with no live stream approaches the default 10,000-unit daily quota. Start the aggregator only around your streams, raise the interval, or set `YOUTUBE_EMERGENCY_SEARCH = False`.

### TikTok

TikTok chat is **disabled**. The project does not include an unverified general LIVE-chat connector.

A future connector can feed the same interface without touching the video pipeline:

```text
TikTok -> add_message("TT", username, message) -> panel, native window, history
```

---

## Configuration Reference

The public repository ships `config/chat_config.example.json`. Copy it to `C:\RTMPStreamer\chat_config.json` and fill in real values. **The real file must stay private.**

```json
{
    "twitch": {
        "enabled": true,
        "username": "YOUR_USERNAME",
        "client_id": "YOUR_TWITCH_CLIENT_ID",
        "client_secret": "YOUR_TWITCH_CLIENT_SECRET",
        "refresh_token": "YOUR_TWITCH_REFRESH_TOKEN",
        "channel": "YOUR_USERNAME"
    },
    "youtube": {
        "enabled": true,
        "api_key": "YOUR_YOUTUBE_API_KEY",
        "handle": "@USERNAME",
        "channel_id": "",
        "uploads_playlist_id": ""
    },
    "tiktok": {
        "enabled": false,
        "username": "YOUR_USERNAME"
    }
}
```

| Key | Notes |
|---|---|
| `twitch.client_id` | Twitch application Client ID. |
| `twitch.client_secret` | Twitch application Client Secret. Keep private. Sent with token refreshes when set; may be omitted for public clients. |
| `twitch.refresh_token` | Persistent Twitch OAuth credential; rotated automatically after each refresh. |
| `youtube.api_key` | Requires YouTube Data API v3 enabled on the key's project. |
| `youtube.handle` | Channel handle used for automatic livestream discovery. No `video_id` is needed. |
| `youtube.channel_id` | Optional. Filled in automatically after the first handle lookup; needed for the emergency search fallback. |
| `youtube.uploads_playlist_id` | Optional. Filled in automatically after the first lookup; when set, no channel lookup is made. |
| `tiktok.enabled` | Currently `false`. |

### Tunable constants

**Aggregator** — top of `chat_aggregator_auto_refresh_youtube_auto.py`:

| Constant | Default | Meaning |
|---|---|---|
| `BASE` | script folder | Folder for `chat_config.json`, the overlay text, the history database and the window state |
| `OVERLAY_WINDOW_ENABLED` | `True` | Show the native chat window |
| `OVERLAY_DEFAULT_X` / `_Y` | 20 / 120 | Initial window position |
| `OVERLAY_DEFAULT_WIDTH` / `_HEIGHT` | 430 / 330 | Initial window size |
| `OVERLAY_ALPHA` | 220 | Background opacity (0–255) |
| `OVERLAY_MAX_VISIBLE_LINES` | 18 | Lines shown in the live view |
| `OVERLAY_START_LOCKED` | `False` | Start locked and click-through |
| `OVERLAY_NEW_MESSAGE_DURATION` | 5.0 s | How long a new message stays highlighted |
| `OVERLAY_REGISTER_HOTKEYS` | `True` | Register `Ctrl+Alt+L` / `Ctrl+Alt+H` |
| `MAX_MESSAGES` | 18 | Messages kept in memory for the live views |
| `MAX_LINE_LENGTH` | 42 | Wrap width in characters |
| `STREAM_PANEL_MAX_LINES` | 11 | Lines written to `chat_overlay.txt` |
| `STREAM_PANEL_MAX_LINES_PER_MESSAGE` | 3 | Longest a single message may be in the panel |
| `TOKEN_VALIDATE_INTERVAL` | 3600 s | Twitch token re-validation period |
| `TOKEN_REFRESH_MARGIN` | 600 s | Refresh when this much lifetime or less remains |
| `TWITCH_PING_AFTER` | 60 s | Silence before pinging Twitch |
| `TWITCH_IDLE_TIMEOUT` | 150 s | Silence before the IRC connection is treated as dead |
| `DCF_TIMEOUT` | 600 s | Time allowed for Device Code Flow authorization |
| `YOUTUBE_DISCOVERY_INTERVAL` | 60 s | Delay between livestream discovery attempts |
| `YOUTUBE_DISCOVERY_MAX_VIDEOS` | 50 | Recent uploads inspected per attempt |
| `YOUTUBE_USE_STREAM_LIST` | `True` | Prefer `streamList` over polling |
| `YOUTUBE_POLL_FALLBACK` | `True` | Allow REST polling when gRPC is unavailable |
| `YOUTUBE_STREAM_RECONNECT_DELAY` | 5 s | Delay before reconnecting `streamList` |
| `YOUTUBE_RETRY_INTERVAL` | 5 s | Delay after a YouTube error |
| `YOUTUBE_MAX_RESULTS` | 200 | Messages per REST poll |
| `YOUTUBE_MAX_SEEN_IDS` | 500 | Message IDs remembered for de-duplication |
| `YOUTUBE_QUOTA_BACKOFF` | 900 s | Wait after the API quota is exhausted |
| `YOUTUBE_EMERGENCY_SEARCH` | `True` | Allow the throttled `search.list` fallback |
| `YOUTUBE_SEARCH_FALLBACK_INTERVAL` | 1200 s | Minimum time between fallback searches |

**Stream** — top of `MultistreamApp.py`:

| Constant | Meaning |
|---|---|
| `RTMP_URL` | Local MediaMTX ingest |
| `CHANNEL_NAME`, `STREAM_TITLE`, `SOCIAL_TEXT` | Text shown in the top-left banner |
| `WEBCAM`, `BRIO_MIC` | DirectShow device names |
| `DESKTOP_WIDTH`, `DESKTOP_HEIGHT` | Captured desktop region |
| `OUTPUT_WIDTH`, `OUTPUT_HEIGHT`, `FPS` | Output frame size and rate |
| `WEBCAM_*` | Webcam capture size, overlay size and margin |
| `CHAT_FILE` | Path of `chat_overlay.txt` (defaults to the script's folder, matching the aggregator) |
| `CHAT_X`, `CHAT_Y`, `CHAT_WIDTH`, `CHAT_HEIGHT`, `CHAT_HEADER_HEIGHT` | Chat panel geometry |
| `CHAT_FONT_SIZE`, `CHAT_LINE_SPACING`, `CHAT_RELOAD_FRAMES` | Chat text style and reload rate |
| `LIVE_BADGE_*` | LIVE badge size and position |

> [!NOTE]
> If you change the chat panel height or font, adjust `STREAM_PANEL_MAX_LINES` in the aggregator to match. If the top chat line touches the `LIVE CHAT` header, lower it by one.

---

## Running the System

Start `StreamStart.bat` from the `RTMPStreamer` folder, or use three windows as shown in [Quick Start](#quick-start).

Expected aggregator output (lines from the workers can interleave):

```text
Chat history database: C:\RTMPStreamer\chat_history.sqlite3
Chat history session started: 2026-01-01T20:00:00+00:00
Native chat overlay enabled. Ctrl+Alt+L = lock/unlock, Ctrl+Alt+H = hide/show.
Twitch OAuth: refreshing access token...
Twitch OAuth: access token refreshed and chat_config.json updated.
Twitch chat: connecting as 'USERNAME' to #USERNAME...
Twitch chat: authenticated.
Twitch chat: joined #USERNAME
YouTube chat: automatic livestream discovery enabled (uploads playlist first; throttled Search.List fallback).
YouTube chat: channel ID = UC... | uploads playlist = UU...
YouTube chat: looking for an active livestream...
YouTube discovery: active live chat candidate found: VIDEO_ID — Stream title
YouTube chat: livestream detected: https://www.youtube.com/watch?v=VIDEO_ID
YouTube chat: live chat connected.
TikTok chat disabled.
Unified chat aggregator running.
Overlay file: C:\RTMPStreamer\chat_overlay.txt
Chat database: C:\RTMPStreamer\chat_history.sqlite3
```

If no stream is live yet, YouTube prints `no active livestream found. Retrying in 60s.` and keeps checking.

```powershell
cd C:\RTMPStreamer
.\.venv\Scripts\Activate.ps1
python MultistreamApp.py
```

The stream publishes to `rtmp://127.0.0.1/live`. Stop any window with `Ctrl+C`.

If no stream is live yet, YouTube prints `no active livestream found. Retrying in 60s.` and keeps checking.

---

## Performance and Realtime Targets

Watch the FFmpeg status line for `fps=`, `speed=`, `dup=` and `drop=`. A healthy stream approaches `fps=30`, `speed=1.00x`, `dup=0`, `drop=0`.

### `speed < 1.0x`

A value such as `speed=0.75x` means the pipeline is slower than realtime. Possible causes:

- CPU-heavy filters
- insufficient NVENC availability
- webcam capture pressure
- DirectShow buffering
- excessive webcam resolution
- excessive graphics processing
- other applications consuming GPU/CPU

### BRIO buffer overflow

If FFmpeg reports `real-time buffer ... too full` or `frame dropped`, the capture queue is filling. Current mitigation: capture the BRIO at **640×360 MJPEG** and overlay at **320×180** instead of 1080p/4K.

---

## Troubleshooting

<details>
<summary><code>path 'live' is not configured</code></summary>

MediaMTX started with the wrong configuration. Run it from `C:\MediaMTX` and confirm the log shows:

```text
configuration loaded from C:\MediaMTX\mediamtx.yml
```

</details>

<details>
<summary><code>Cannot open connection tcp://127.0.0.1:1935</code></summary>

MediaMTX is stopped or not listening. Check:

```powershell
Test-NetConnection 127.0.0.1 -Port 1935
```

</details>

<details>
<summary><code>Unrecognized option 'input_format'</code></summary>

Use the parameters supported by the installed FFmpeg build. For BRIO MJPEG capture this project uses:

```python
vcodec="mjpeg"       # correct
input_format="mjpeg" # not supported here
```

</details>

<details>
<summary><code>video_size</code> becomes <code>1x9</code> or <code>1x2</code></summary>

With `ffmpeg-python`, pass tuples, not strings:

```python
video_size=(1920, 1080)    # correct
video_size="1920x1080"     # wrong
```

</details>

<details>
<summary><code>StreamStart.bat</code> says a file was not found</summary>

The launcher looks relative to its own folder. Check that:

- `StreamStart.bat` is inside `RTMPStreamer`, next to `MultistreamApp.py`, the aggregator script, `chat_config.json` and `.venv`;
- `MediaMTX` is a sibling of `RTMPStreamer` and contains `mediamtx.exe`;
- FFmpeg is in a sibling `FFmpeg` folder (`bin\ffmpeg.exe` or `ffmpeg.exe`) or on `PATH`.

The launcher prints the project root, app folder, MediaMTX folder and FFmpeg location it resolved before it starts anything.

</details>

<details>
<summary>The chat panel is empty or shows old messages</summary>

- `chat_overlay.txt` is written by the aggregator. Confirm it is running. Both scripts use the file next to themselves, so they must live in the same folder.
- Text disappearing entirely usually means an older `MultistreamApp.py` without `expansion=none` met a message containing `%`.
- Missing newest messages usually means an older aggregator that wrote more lines than the panel holds. The current one limits the file to `STREAM_PANEL_MAX_LINES`.

</details>

<details>
<summary>FFmpeg exits at startup with "text file ... could not be read"</summary>

`drawtext` needs `chat_overlay.txt` to exist. The current `MultistreamApp.py` creates a placeholder; with an older copy, start the aggregator first or create the file.

</details>

<details>
<summary>Twitch chat authentication failure</summary>

The aggregator prints the exact validation failure. Typical causes:

- `Twitch token belongs to a different Client ID.` — `client_id` doesn't match the app that issued the refresh token;
- `Twitch token belongs to 'x', but config username is 'y'.` — `username` doesn't match the authorized account;
- `Twitch token does not contain chat:read.` — re-authorize with the `chat:read` scope;
- `Twitch token refresh failed (HTTP ...)` — check `client_id`, `client_secret` (if your app is a confidential client) and that the refresh token is still valid;
- `Twitch CLI was not found in PATH.` — the Device Code Flow fallback needs `twitch` to run from PowerShell.

Also make sure the Twitch stream key isn't used anywhere in the chat configuration.

</details>

<details>
<summary>YouTube chat is not found</summary>

Check that:

- YouTube Data API v3 is enabled and the API key is valid;
- the configured `@handle` resolves to the intended channel (`YouTube handle '...' was not found.` means it doesn't);
- the channel has a current live broadcast, and it is among the channel's 50 most recent uploads;
- the broadcast has an active live chat (`active live chat is not available` means it doesn't);
- the quota isn't exhausted (`API quota exhausted` — the worker waits 15 minutes and retries by itself).

The discovery log lists how many recent videos were inspected and the state of the newest one, which shows whether YouTube has published the stream yet.

</details>

<details>
<summary><code>streamList unavailable ... falling back to REST polling</code></summary>

`grpcio` or `protobuf` is missing or too old. Chat still works through REST polling. To use `streamList`:

```powershell
python -m pip install --upgrade grpcio protobuf
```

Run the command with the same Python that starts the aggregator (the `.venv` one).

</details>

<details>
<summary>YouTube quota usage is unexpectedly high</summary>

The likely cause is the emergency `search.list` fallback, which costs 100 units per call and runs at most every 20 minutes while no stream is live. Set `YOUTUBE_EMERGENCY_SEARCH = False`, raise `YOUTUBE_SEARCH_FALLBACK_INTERVAL`, or start the aggregator only when you stream. See the warning under [YouTube](#youtube).

</details>

<details>
<summary><code>Chat history write failed</code></summary>

SQLite could not store a message (disk full, or `chat_history.sqlite3` locked by another program). Chat keeps working; only history is affected. Close other programs that hold the database open.

</details>

<details>
<summary>The native chat window does not appear</summary>

It needs Windows and `OVERLAY_WINDOW_ENABLED = True`. If it was hidden with `Ctrl+Alt+H`, press the same keys again. Its saved position lives in `chat_overlay_window.json`; delete that file to reset it.

</details>

---

## Security

Never commit:

```text
chat_config.json
.env
*.token
*.secret
*.key
chat_history.sqlite3*
```

Treat all of these as credentials:

- Twitch stream key
- Twitch OAuth access token
- Twitch refresh token
- Twitch Client Secret
- YouTube API key

`chat_history.sqlite3` contains viewers' usernames, messages and timestamps. Treat it as private data and don't publish it.

> [!WARNING]
> A MediaMTX `forward` destination embeds your stream key. Keep `mediamtx.yml` out of version control if it contains one.

> [!NOTE]
> The Twitch Device Code Flow fallback passes your Client ID and Secret to the Twitch CLI as command-line arguments, and `twitch configure` stores them in the CLI's own configuration. Use it on a machine you control.

If a credential is exposed, revoke or rotate it immediately.

<details>
<summary>Recommended <code>.gitignore</code></summary>

```gitignore
# Python
.venv/
__pycache__/
*.py[cod]

# Runtime
logs/
chat_overlay.txt
chat_overlay_window.json
*.tmp

# Chat history (private data)
chat_history.sqlite3*

# Secrets
chat_config.json
.env
*.token
*.secret
*.key

# Media
*.mp4
*.mkv
*.flv
*.ts

# IDE / OS
.vscode/
.idea/
.DS_Store
Thumbs.db
```

</details>

---

## Design Principles

The system is split into independent layers.

```text
Video:  capture -> FFmpeg filters -> NVENC -> RTMP -> MediaMTX
Chat:   platform -> aggregator -> panel file / native window / SQLite history
```

This means:

- chat problems don't kill video;
- platform chat credentials never live in the encoder;
- the encoder doesn't need to know how any platform's chat API works;
- a database or overlay failure never interrupts a platform chat connection;
- new platforms plug in through the same `add_message()` interface.

---

## Roadmap

**Completed**

- [x] Windows environment and Python project
- [x] FFmpeg integration
- [x] MediaMTX RTMP ingest
- [x] Full desktop capture
- [x] BRIO webcam and microphone
- [x] Webcam picture-in-picture
- [x] Stream banners
- [x] Chat panel inside the stream
- [x] NVIDIA NVENC H.264
- [x] Twitch stream forwarding
- [x] Twitch chat
- [x] Twitch OAuth: runtime-only access token, refresh-token rotation, Device Code Flow fallback
- [x] YouTube chat with automatic livestream discovery
- [x] YouTube `streamList` with REST fallback
- [x] Native desktop chat window with search and history scrolling
- [x] SQLite chat history
- [x] One-click launcher (`StreamStart.bat`)

**Next**

- [ ] Relay the stream to additional platforms through MediaMTX
- [ ] TikTok chat connector via `add_message()`

---

## References

| Topic | Link |
|---|---|
| MediaMTX | <https://mediamtx.org/> · [Forwarding](https://mediamtx.org/docs/features/forward) |
| FFmpeg | <https://ffmpeg.org/> · [`drawtext`](https://ffmpeg.org/ffmpeg-filters.html#drawtext-1) |
| Python | <https://www.python.org/> |
| Twitch authentication | <https://dev.twitch.tv/docs/authentication/> |
| Twitch IRC chat | <https://dev.twitch.tv/docs/chat/irc> |
| Twitch token validation | <https://dev.twitch.tv/docs/authentication/validate-tokens/> |
| Twitch token refresh | <https://dev.twitch.tv/docs/authentication/refresh-tokens/> |
| YouTube live chat | <https://developers.google.com/youtube/v3/live/docs/liveChatMessages> |
| YouTube low-latency chat | <https://developers.google.com/youtube/v3/live/docs/liveChatMessages/streamList> |

---

## License

This project is licensed under the **MIT License**.

Copyright © 2026 Velea Radu-Valentin.

See the [`LICENSE`](LICENSE) file for the complete license text.

Third-party components such as FFmpeg, MediaMTX, NVIDIA software components, Twitch APIs, and YouTube APIs remain subject to their own licenses and terms.
