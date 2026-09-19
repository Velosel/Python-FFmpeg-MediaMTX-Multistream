# Unified Chat

The chat aggregator (`chat_aggregator_auto_refresh_youtube_auto.py`) merges platform messages into one local feed:

```text
[T]  username: message
[YT] username: message
[TT] username: message
```

Every accepted message goes through `add_message(platform, username, message, source_message_id=None)` and is fanned out to three places.

## 1. Stream panel (`chat_overlay.txt`)

- shows the newest messages that fit in **11 lines** (`STREAM_PANEL_MAX_LINES`), oldest first;
- wraps lines at **42 characters** (`MAX_LINE_LENGTH`);
- keeps whole messages, cutting any single message to **3 lines** with an ellipsis (`STREAM_PANEL_MAX_LINES_PER_MESSAGE`);
- writes atomically (temp file, then replace) under one lock, retrying briefly if Windows has the file locked, so FFmpeg never reads a half-written file;
- resets to `Waiting for messages...` when the aggregator starts, so a previous session's chat never appears on stream.

`MultistreamApp.py` reads this file with `drawtext`, reloading twice per second (`CHAT_RELOAD_FRAMES`). Two details keep that reliable:

- **`expansion=none`** — chat text is untrusted. With `drawtext`'s default expansion, a single `%` in any message makes FFmpeg skip rendering *all* chat text, and `%{...}` would be evaluated as a function.
- **Bottom-anchored text** — the newest line sits at the bottom of the panel and older lines grow upward, so new messages are never pushed out of view.

`MultistreamApp.py` also creates a placeholder `chat_overlay.txt` if none exists, because `drawtext` cannot start without its text file.

## 2. Native chat window

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

> The native window is an ordinary window on your desktop, so `gdigrab` normally captures it like any other window. If you don't want it in the stream, move it outside the captured area (for example to a second monitor), hide it with `Ctrl+Alt+H`, or set `OVERLAY_WINDOW_ENABLED = False`.

## 3. Chat history (SQLite)

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

## Twitch OAuth

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

## Platform setup

### Twitch

**Streaming** uses a stream key. The stream key is **not** the chat OAuth token. See [SETUP.md](SETUP.md#forwarding-to-a-platform) for the MediaMTX forwarding configuration.

**Chat** uses Twitch IRC over TLS at `irc.chat.twitch.tv:6697` with the read-only scope `chat:read`. See the [Twitch IRC docs](https://dev.twitch.tv/docs/chat/irc).

**OAuth** — access tokens are obtained at runtime from the stored refresh token; see [Twitch OAuth](#twitch-oauth) above. Reference docs: [validating](https://dev.twitch.tv/docs/authentication/validate-tokens/) and [refreshing](https://dev.twitch.tv/docs/authentication/refresh-tokens/) tokens.

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

> **Emergency `search.list` fallback.** If the uploads playlist shows no live stream, the worker calls `search.list` at most once every 20 minutes (`YOUTUBE_SEARCH_FALLBACK_INTERVAL = 1200`) to catch a broadcast that hasn't appeared in the playlist yet. Google documents `search.list` at **100 quota units per call**, so this is up to 72 calls (7,200 units) per day. Together with normal discovery (about 2,880 units per day at one attempt per minute) an aggregator left running 24 hours with no live stream approaches the default 10,000-unit daily quota. Start the aggregator only around your streams, raise the interval, or set `YOUTUBE_EMERGENCY_SEARCH = False`.

### TikTok

TikTok chat is **disabled**. The project does not include an unverified general LIVE-chat connector.

A future connector can feed the same interface without touching the video pipeline:

```text
TikTok -> add_message("TT", username, message) -> panel, native window, history
```

## Configuration reference

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

### Tunable constants — aggregator

Top of `chat_aggregator_auto_refresh_youtube_auto.py`:

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

### Tunable constants — stream

Top of `MultistreamApp.py`:

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

> If you change the chat panel height or font, adjust `STREAM_PANEL_MAX_LINES` in the aggregator to match. If the top chat line touches the `LIVE CHAT` header, lower it by one.
