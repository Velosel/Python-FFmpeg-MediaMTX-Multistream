import json
import socket
import ssl
import threading
import time
import textwrap
from collections import deque
from pathlib import Path

import requests


BASE = Path(r"C:\RTMPStreamer")
CONFIG_FILE = BASE / "chat_config.json"
OUTPUT_FILE = BASE / "chat_overlay.txt"

MAX_MESSAGES = 18
MAX_LINE_LENGTH = 42

# Validate/refresh Twitch at startup and periodically.
TOKEN_VALIDATE_INTERVAL = 3600
TOKEN_REFRESH_MARGIN = 600  # refresh if <= 10 minutes remain

messages = deque(maxlen=MAX_MESSAGES)
lock = threading.Lock()
config_lock = threading.Lock()


# =============================================================
# Configuration
# =============================================================

def load_config():
    with config_lock:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)


def save_config(config):
    # Atomic replacement. This is important because the refresh
    # token can rotate and the old one may immediately become
    # invalid after a successful refresh.
    temp = CONFIG_FILE.with_suffix(".tmp")

    with config_lock:
        temp.write_text(
            json.dumps(config, indent=4),
            encoding="utf-8",
        )
        temp.replace(CONFIG_FILE)


# =============================================================
# Overlay writer
# =============================================================

def write_overlay():
    with lock:
        lines = []

        for platform, username, message in messages:
            prefix = {
                "T": "[T]",
                "Y": "[YT]",
                "TT": "[TT]",
            }.get(platform, "[?]")

            message = " ".join(str(message).split())

            if not message:
                continue

            full = f"{prefix} {username}: {message}"

            wrapped = textwrap.wrap(
                full,
                width=MAX_LINE_LENGTH,
                break_long_words=False,
                break_on_hyphens=False,
            )

            lines.extend(wrapped)

        content = "\n".join(lines)

    temp = OUTPUT_FILE.with_suffix(".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(OUTPUT_FILE)


def add_message(platform, username, message):
    username = " ".join(str(username).split())
    message = " ".join(str(message).split())

    if not username or not message:
        return

    with lock:
        messages.append((platform, username, message))

    write_overlay()


# =============================================================
# Twitch OAuth
# =============================================================

def validate_twitch_token(twitch):
    token = twitch.get("oauth_token", "").strip()
    client_id = twitch.get("client_id", "").strip()

    if not token:
        raise RuntimeError("Twitch oauth_token is empty.")

    if not client_id:
        raise RuntimeError("Twitch client_id is empty.")

    response = requests.get(
        "https://id.twitch.tv/oauth2/validate",
        headers={
            "Authorization": f"OAuth {token}",
        },
        timeout=15,
    )

    if response.status_code == 401:
        raise RuntimeError("Twitch access token is invalid or revoked.")

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
    twitch = config.get("twitch", {})

    client_id = twitch.get("client_id", "").strip()
    client_secret = twitch.get("client_secret", "").strip()
    refresh_token = twitch.get("refresh_token", "").strip()

    if not client_id:
        raise RuntimeError("Missing Twitch client_id.")

    if not client_secret:
        raise RuntimeError("Missing Twitch client_secret.")

    if not refresh_token:
        raise RuntimeError(
            "Missing Twitch refresh_token. "
            "This token can only be refreshed automatically "
            "if Twitch issued it from a refreshable OAuth flow."
        )

    print("Twitch OAuth: refreshing access token...")

    response = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )

    if not response.ok:
        try:
            details = response.json()
        except Exception:
            details = response.text

        raise RuntimeError(
            f"Twitch token refresh failed "
            f"(HTTP {response.status_code}): {details}"
        )

    data = response.json()

    new_access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")

    if not new_access_token:
        raise RuntimeError(
            "Twitch refresh response did not contain access_token."
        )

    twitch["oauth_token"] = new_access_token

    # Twitch can rotate refresh tokens. Always store the newest
    # refresh token if one is returned.
    if new_refresh_token:
        twitch["refresh_token"] = new_refresh_token

    config["twitch"] = twitch
    save_config(config)

    print(
        "Twitch OAuth: access token refreshed and "
        "chat_config.json updated."
    )

    return data


def ensure_twitch_token(config):
    twitch = config.get("twitch", {})

    # First validate the token.
    try:
        data = validate_twitch_token(twitch)

        expires_in = int(data.get("expires_in", 0))

        print(
            f"Twitch OAuth: valid for approximately "
            f"{expires_in // 60} minutes."
        )

        if expires_in <= TOKEN_REFRESH_MARGIN:
            print(
                "Twitch OAuth: token is near expiry; "
                "attempting refresh."
            )
            refresh_twitch_token(config)
            return validate_twitch_token(
                config["twitch"]
            )

        return data

    except Exception as validation_error:
        print(
            f"Twitch OAuth validation failed: "
            f"{validation_error}"
        )

        # Attempt automatic refresh.
        try:
            refresh_twitch_token(config)
            return validate_twitch_token(
                config["twitch"]
            )
        except Exception as refresh_error:
            raise RuntimeError(
                "Twitch token could not be validated or refreshed. "
                f"Validation: {validation_error}; "
                f"Refresh: {refresh_error}"
            ) from refresh_error


# =============================================================
# Twitch IRC parser
# =============================================================

def parse_twitch_privmsg(line):
    line = line.strip("\r\n")

    if not line:
        return None

    # Remove IRC metadata tags.
    if line.startswith("@"):
        try:
            _, line = line.split(" ", 1)
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

    message = " ".join(message.split())

    if not username or not message:
        return None

    return username, message


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

    # Validate/refresh before opening IRC.
    try:
        ensure_twitch_token(config)
    except Exception as e:
        print(f"Twitch OAuth startup error: {e}")
        return

    last_validation = time.time()

    print(
        f"Twitch chat: connecting as '{username}' "
        f"to #{channel}..."
    )

    while True:
        sock = None

        try:
            # Periodic Twitch token validation.
            if time.time() - last_validation >= TOKEN_VALIDATE_INTERVAL:
                try:
                    ensure_twitch_token(config)
                    last_validation = time.time()
                except Exception as e:
                    print(
                        f"Twitch OAuth periodic validation "
                        f"failed: {e}"
                    )
                    time.sleep(10)
                    continue

            # Reload in case the refresh operation rotated
            # access_token / refresh_token.
            current_config = load_config()
            twitch = current_config["twitch"]
            oauth_token = twitch["oauth_token"].strip()

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

            buffer = ""

            while True:
                # Re-validate approximately every hour.
                if time.time() - last_validation >= TOKEN_VALIDATE_INTERVAL:
                    try:
                        current_config = load_config()
                        ensure_twitch_token(current_config)
                        last_validation = time.time()
                    except Exception as e:
                        print(
                            f"Twitch OAuth validation failed "
                            f"while connected: {e}"
                        )
                        raise ConnectionError(
                            "Restarting Twitch IRC after OAuth validation failure."
                        ) from e

                data = sock.recv(8192)

                if not data:
                    raise ConnectionError(
                        "Twitch connection closed."
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

                    parsed = parse_twitch_privmsg(line)

                    if parsed:
                        msg_username, message = parsed
                        add_message(
                            "T",
                            msg_username,
                            message,
                        )

        except Exception as e:
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

        time.sleep(3)


# =============================================================
# YouTube
# =============================================================

def find_youtube_live_chat_id(api_key, video_id):
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "liveStreamingDetails",
            "id": video_id,
            "key": api_key,
        },
        timeout=15,
    )

    response.raise_for_status()

    items = response.json().get("items", [])

    if not items:
        raise RuntimeError(
            "YouTube video ID was not found."
        )

    details = items[0].get(
        "liveStreamingDetails",
        {},
    )

    chat_id = details.get(
        "activeLiveChatId"
    )

    if not chat_id:
        raise RuntimeError(
            "YouTube live chat is not active."
        )

    return chat_id


def youtube_worker(config):
    youtube = config.get("youtube", {})

    if not youtube.get("enabled", False):
        print("YouTube chat disabled.")
        return

    api_key = youtube.get("api_key", "").strip()
    video_id = youtube.get("video_id", "").strip()

    if not api_key or not video_id:
        print(
            "YouTube chat: API key/video ID not configured."
        )
        return

    page_token = None

    print("YouTube chat: connecting...")

    while True:
        try:
            chat_id = find_youtube_live_chat_id(
                api_key,
                video_id,
            )

            print("YouTube chat: connected.")

            while True:
                params = {
                    "liveChatId": chat_id,
                    "part": "snippet,authorDetails",
                    "maxResults": 200,
                    "key": api_key,
                }

                if page_token:
                    params["pageToken"] = page_token

                response = requests.get(
                    "https://www.googleapis.com/youtube/v3/liveChat/messages",
                    params=params,
                    timeout=30,
                )

                response.raise_for_status()

                data = response.json()

                for item in data.get("items", []):
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
                    )

                page_token = data.get(
                    "nextPageToken"
                )

                delay = data.get(
                    "pollingIntervalMillis",
                    500,
                )

                time.sleep(
                    max(delay, 100) / 1000
                )

        except Exception as e:
            print(f"YouTube chat error: {e}")
            time.sleep(5)


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
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not OUTPUT_FILE.exists():
        OUTPUT_FILE.write_text(
            "LIVE CHAT\nWaiting for messages...",
            encoding="utf-8",
        )

    config = load_config()

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

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nChat aggregator stopped.")


if __name__ == "__main__":
    main()
