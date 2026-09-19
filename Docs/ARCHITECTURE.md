# Architecture

## Overview

The system captures the desktop, a Logitech BRIO webcam and the BRIO microphone, composes them with a webcam overlay and broadcast graphics, encodes **one** H.264/AAC stream with NVENC, and publishes it over RTMP to a local [MediaMTX](https://mediamtx.org/) server. MediaMTX then relays that already-encoded stream to streaming platforms.

A separate Python process, the **chat aggregator**, reads Twitch and YouTube chat and feeds three consumers: the chat panel inside the stream, an optional native Windows chat window, and a SQLite history database.

> **Core idea:** encode one stream on the PC, then let MediaMTX relay the already-encoded stream to multiple platforms — no re-encoding per platform.

## Video path

```text
desktop (gdigrab 1920x1080) + BRIO webcam (MJPEG 640x360) + BRIO microphone + chat_overlay.txt
    -> FFmpeg filters (scale, overlay, drawbox, drawtext)
    -> NVENC H.264 + AAC, 1280x720 @ 30 FPS
    -> RTMP  rtmp://127.0.0.1/live
    -> MediaMTX
    -> Twitch          (YouTube: experimental, TikTok: disabled)
```

### Pipeline detail

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

`MultistreamApp.py` builds this pipeline with `ffmpeg-python`. The desktop and webcam are captured as separate inputs, scaled independently, overlaid together, run through a stack of `drawbox`/`drawtext` filters for the on-stream graphics and chat panel, and finally encoded once with `h264_nvenc`.

## Chat path

```text
Twitch IRC (TLS) ---------------.
YouTube streamList (gRPC) ------+--> chat aggregator --+--> chat_overlay.txt --> FFmpeg drawtext --> stream
YouTube REST polling (fallback) |                       +--> native chat window (desktop)
TikTok (disabled) --------------'                       +--> chat_history.sqlite3
```

The chat process is deliberately separate from the video encoder: **a chat failure never stops the video stream.** The aggregator (`chat_aggregator_auto_refresh_youtube_auto.py`) owns all platform credentials and connections; `MultistreamApp.py` only ever reads the plain-text file the aggregator writes.

## YouTube discovery

The YouTube worker finds the current livestream from the channel's uploads playlist instead of `search.list`:

```text
1. channels.list        @handle -> channel ID + uploads playlist ID   (cached in chat_config.json)
2. playlistItems.list   recent uploads (up to 50 video IDs)
3. videos.list          the video that has an activeLiveChatId
4. streamList           follow that live chat (REST polling if gRPC is unavailable)
```

Discovery only runs when there is no current chat or when the current livestream ends. A throttled `search.list` call remains as a last-resort fallback — see [CHAT.md](CHAT.md#youtube) for its quota cost.

## Design principles

The system is split into independent layers:

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

## Repository structure

The project lives in one folder. Every script and the launcher find their files relative to their own location, so the folder can be placed anywhere.

```text
PROJECT\
├── README.md
├── LICENSE
├── SECURITY.md
├── .gitignore
├── .gitattributes
├── requirements.txt
├── config/
│   └── chat_config.example.json
├── src/
│   ├── MultistreamApp.py
│   └── chat_aggregator_auto_refresh.py
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
│   ├── StreamStart.bat
│   ├── MultistreamApp.py
│   ├── chat_aggregator_auto_refresh_youtube_auto.py
│   ├── requirements.txt
│   ├── .venv\
│   ├── chat_config.json           # private — never commit
│   ├── chat_overlay.txt           # generated: chat panel text for FFmpeg
│   ├── chat_history.sqlite3       # generated: chat history (plus -wal / -shm files)
│   └── chat_overlay_window.json   # generated: native window position, size, lock, visibility
```

Local Windows runtime:

```text
C:\MediaMTX\
├── mediamtx.exe
└── mediamtx.yml

C:\RTMPStreamer\
├── .venv\
├── MultistreamApp.py
├── chat_aggregator_auto_refresh_youtube_auto.py
├── chat_config.json          # private — never commit
├── chat_overlay.txt          # generated at runtime
└── requirements.txt
```

## Encoding profile

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

## Stream graphics

All graphics are rendered inside the FFmpeg pipeline via `add_graphics()` in `MultistreamApp.py`. OBS is not required.

| Position | Element |
|---|---|
| Top-left | `LIVE STREAM` / `Twitch.tv/<channel>` |
| Top-right | `● LIVE` badge |
| Bottom-left | `LIVE CHAT` header + chat panel |
| Bottom-right | BRIO webcam picture-in-picture |

The title, channel name and social text come from `STREAM_TITLE`, `CHANNEL_NAME` and `SOCIAL_TEXT`. Panel geometry comes from the `CHAT_*` constants. See [Configuration Reference](CHAT.md#configuration-reference) for the full constant list.

## Tested environment

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
| Local RTMP | `rtmp://127.0.0.1/live` |

> This is the tested baseline, not a requirement that every installation use exactly these versions.

## Current status

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

## References

| Topic | Link |
|---|---|
| MediaMTX | <https://mediamtx.org/> · [Forwarding](https://mediamtx.org/docs/features/forward) |
| FFmpeg | <https://ffmpeg.org/> · [`drawtext`](https://ffmpeg.org/ffmpeg-filters.html#drawtext-1) |
| Python | <https://www.python.org/> |
