# Troubleshooting

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

The likely cause is the emergency `search.list` fallback, which costs 100 units per call and runs at most every 20 minutes while no stream is live. Set `YOUTUBE_EMERGENCY_SEARCH = False`, raise `YOUTUBE_SEARCH_FALLBACK_INTERVAL`, or start the aggregator only when you stream. See [CHAT.md — YouTube](CHAT.md#youtube).

</details>

<details>
<summary><code>Chat history write failed</code></summary>

SQLite could not store a message (disk full, or `chat_history.sqlite3` locked by another program). Chat keeps working; only history is affected. Close other programs that hold the database open.

</details>

<details>
<summary>The native chat window does not appear</summary>

It needs Windows and `OVERLAY_WINDOW_ENABLED = True`. If it was hidden with `Ctrl+Alt+H`, press the same keys again. Its saved position lives in `chat_overlay_window.json`; delete that file to reset it.

</details>

## Performance

Watch the FFmpeg status line for `fps=`, `speed=`, `dup=` and `drop=`. A healthy stream approaches `fps=30`, `speed=1.00x`, `dup=0`, `drop=0`. See [SETUP.md — Performance and realtime targets](SETUP.md#performance-and-realtime-targets) for causes and mitigations of slow encoding and BRIO buffer overflow.

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

> **Warning:** A MediaMTX `forward` destination embeds your stream key. Keep `mediamtx.yml` out of version control if it contains one.

> **Note:** The Twitch Device Code Flow fallback passes your Client ID and Secret to the Twitch CLI as command-line arguments, and `twitch configure` stores them in the CLI's own configuration. Use it on a machine you control.

If a credential is exposed, revoke or rotate it immediately.

### Recommended `.gitignore`

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
