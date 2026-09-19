# Setup

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

## Quick start

Assumes Python, FFmpeg (with `h264_nvenc`) and MediaMTX are already installed. Full setup is below.

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
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe chat_aggregator_auto_refresh_youtube_auto.py
```

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

> **PowerShell blocks activation?** Use the `cmd` activation script instead:
>
> ```powershell
> cmd /k C:\RTMPStreamer\.venv\Scripts\activate.bat
> ```

### 2. Python packages

```powershell
python -m pip install --upgrade pip
python -m pip install ffmpeg-python requests
python -m pip install --upgrade grpcio protobuf
python -m pip freeze > requirements.txt
```

> **Important:** `ffmpeg-python` is only a Python wrapper. It does **not** install `ffmpeg.exe`.
>
> **Note:** Keep `grpcio` and `protobuf` up to date. An old `protobuf` cannot build the `streamList` client; the aggregator then reports the problem and uses REST polling instead.

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

## MediaMTX configuration

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

### Forwarding to a platform

MediaMTX relays the already-encoded stream to platform ingest servers, so no re-encoding happens per platform. For Twitch, forwarding looks like:

```yaml
paths:
  live:
    source: publisher
    forward:
      - dest: rtmps://YOUR_TWITCH_INGEST/app#YOUR_TWITCH_STREAM_KEY
```

> A `forward` destination embeds your stream key. Keep `mediamtx.yml` out of version control if it contains one — see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#security).

## BRIO webcam and microphone

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

## Running the system

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

The stream publishes to `rtmp://127.0.0.1/live`. Stop any window with `Ctrl+C`.

## Performance and realtime targets

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

For further issues, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
