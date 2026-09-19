from pathlib import Path

import ffmpeg


# =============================================================
# STREAM CONFIGURATION
# =============================================================

RTMP_URL = "rtmp://127.0.0.1/live"

CHANNEL_NAME = "Ravaelv"
STREAM_TITLE = "LIVE STREAM"
SOCIAL_TEXT = f"Twitch.tv/{CHANNEL_NAME}"

WEBCAM = "BRIO 4K Stream Edition"
BRIO_MIC = "Microphone (BRIO 4K Stream Edition)"

# Desktop capture region (gdigrab starts at the top-left of the primary monitor)
DESKTOP_WIDTH = 1920
DESKTOP_HEIGHT = 1080

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
FPS = 30

# Encoding
VIDEO_BITRATE = "4000k"
VIDEO_MAXRATE = "4000k"
VIDEO_BUFSIZE = "8000k"
AUDIO_BITRATE = "128k"
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
GOP_SECONDS = 2
GOP_FRAMES = FPS * GOP_SECONDS

# Webcam
WEBCAM_CAPTURE_WIDTH = 640
WEBCAM_CAPTURE_HEIGHT = 360
WEBCAM_WIDTH = 320
WEBCAM_HEIGHT = 180
WEBCAM_MARGIN = 20

# Unified chat
CHAT_FILE = r"C:\RTMPStreamer\chat_overlay.txt"
CHAT_X = 12
CHAT_Y = 505
CHAT_WIDTH = 300
CHAT_HEIGHT = 195
CHAT_HEADER_HEIGHT = 34
CHAT_TEXT_X = CHAT_X + 12
CHAT_TEXT_BOTTOM = CHAT_Y + CHAT_HEIGHT - 8  # newest line sits here
CHAT_RELOAD_FRAMES = 15  # 2x/sec at 30 FPS
CHAT_FONT_SIZE = 13
CHAT_LINE_SPACING = -6

# Top-right live badge
LIVE_BADGE_WIDTH = 125
LIVE_BADGE_HEIGHT = 42
LIVE_BADGE_X = OUTPUT_WIDTH - LIVE_BADGE_WIDTH - 20
LIVE_BADGE_Y = 20


# =============================================================
# GRAPHICS
# =============================================================

FONT_FILE = r"C:\Windows\Fonts\segoeui.ttf"
FONT_BOLD_FILE = r"C:\Windows\Fonts\segoeuib.ttf"


def ensure_chat_file():
    """
    drawtext cannot start if its textfile is missing (FFmpeg exits at
    startup), so create a placeholder if the chat aggregator has not
    written one yet.
    """
    path = Path(CHAT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text("Waiting for messages...", encoding="utf-8")


def add_graphics(video):
    """
    Broadcast overlay.

    Layout:
        Top-left:  LIVE STREAM + Twitch.tv/<channel>
        Top-right: LIVE badge
        Bottom-left: unified chat panel
        Bottom-right: webcam picture-in-picture
    """

    # ---------------------------------------------------------
    # Compact unified chat panel - bottom-left
    # ---------------------------------------------------------
    video = video.filter(
        "drawbox",
        x=CHAT_X,
        y=CHAT_Y,
        w=CHAT_WIDTH,
        h=CHAT_HEIGHT,
        color="050505@0.66",
        t="fill",
    )

    video = video.filter(
        "drawbox",
        x=CHAT_X,
        y=CHAT_Y,
        w=CHAT_WIDTH,
        h=CHAT_HEADER_HEIGHT,
        color="00D9FF@0.90",
        t="fill",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_BOLD_FILE,
        text="LIVE CHAT",
        x=CHAT_X + 12,
        y=CHAT_Y + 8,
        fontsize=17,
        fontcolor="000000",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_FILE,
        textfile=CHAT_FILE,
        reload=CHAT_RELOAD_FRAMES,
        # Chat text is untrusted. With the default expansion, a single "%"
        # in any message makes drawtext skip rendering ALL the chat text,
        # and "%{...}" sequences would be evaluated as functions.
        expansion="none",
        x=CHAT_TEXT_X,
        # Anchor the text block to the bottom of the panel so the newest
        # line is always visible; older lines grow upward. The aggregator
        # limits chat_overlay.txt to STREAM_PANEL_MAX_LINES so it fits.
        y=f"{CHAT_TEXT_BOTTOM}-text_h",
        fontsize=CHAT_FONT_SIZE,
        line_spacing=CHAT_LINE_SPACING,
        fontcolor="FFFFFF",
        shadowcolor="000000@0.85",
        shadowx=1,
        shadowy=1,
        box=0,
    )

    # ---------------------------------------------------------
    # TOP STREAM IDENTIFIER
    # ---------------------------------------------------------
    video = video.filter(
        "drawbox",
        x=20,
        y=20,
        w=430,
        h=48,
        color="050505@0.70",
        t="fill",
    )

    video = video.filter(
        "drawbox",
        x=20,
        y=20,
        w=5,
        h=48,
        color="00D9FF@1.0",
        t="fill",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_BOLD_FILE,
        text=STREAM_TITLE,
        x=38,
        y=30,
        fontsize=18,
        fontcolor="FFFFFF",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_FILE,
        text=SOCIAL_TEXT,
        x=38,
        y=51,
        fontsize=13,
        fontcolor="B8F3FF",
    )

    # ---------------------------------------------------------
    # TOP-RIGHT LIVE BADGE
    # ---------------------------------------------------------
    video = video.filter(
        "drawbox",
        x=LIVE_BADGE_X,
        y=LIVE_BADGE_Y,
        w=LIVE_BADGE_WIDTH,
        h=LIVE_BADGE_HEIGHT,
        color="FF1744@0.90",
        t="fill",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_BOLD_FILE,
        text="● LIVE",
        x=LIVE_BADGE_X + 9,
        y=LIVE_BADGE_Y + 10,
        fontsize=20,
        fontcolor="FFFFFF",
    )

    return video


# =============================================================
# MAIN STREAM
# =============================================================

def main():
    print(f"Starting {CHANNEL_NAME} desktop + BRIO + unified chat stream...")
    print(f"RTMP destination: {RTMP_URL}")

    ensure_chat_file()

    desktop = ffmpeg.input(
        "desktop",
        f="gdigrab",
        video_size=(DESKTOP_WIDTH, DESKTOP_HEIGHT),
        framerate=FPS,
        draw_mouse=1,
        thread_queue_size=64,
    )

    desktop_video = (
        desktop.video
        .filter("scale", OUTPUT_WIDTH, OUTPUT_HEIGHT)
        .filter("setpts", f"N/({FPS}*TB)")
    )

    brio = ffmpeg.input(
        f"video={WEBCAM}:audio={BRIO_MIC}",
        f="dshow",
        vcodec="mjpeg",
        video_size=(WEBCAM_CAPTURE_WIDTH, WEBCAM_CAPTURE_HEIGHT),
        framerate=FPS,
        sample_rate=AUDIO_SAMPLE_RATE,
        channels=AUDIO_CHANNELS,
        audio_buffer_size=100,
        rtbufsize="4M",
        thread_queue_size=64,
    )

    webcam_video = (
        brio.video
        .filter("scale", WEBCAM_WIDTH, WEBCAM_HEIGHT)
        .filter("setpts", f"N/({FPS}*TB)")
    )

    webcam_audio = brio.audio.filter(
        "asetpts",
        "N/SR/TB",
    )

    combined_video = ffmpeg.overlay(
        desktop_video,
        webcam_video,
        x=f"main_w-overlay_w-{WEBCAM_MARGIN}",
        y=f"main_h-overlay_h-{WEBCAM_MARGIN}",
        eof_action="repeat",
    )

    combined_video = add_graphics(combined_video)

    output = ffmpeg.output(
        combined_video,
        webcam_audio,
        RTMP_URL,

        # NVIDIA GTX 1650 NVENC
        vcodec="h264_nvenc",
        preset="p4",
        tune="ll",
        rc="cbr",

        **{
            "b:v": VIDEO_BITRATE,
            "maxrate": VIDEO_MAXRATE,
            "bufsize": VIDEO_BUFSIZE,
        },

        r=FPS,
        fps_mode="cfr",

        g=GOP_FRAMES,
        keyint_min=GOP_FRAMES,
        bf=0,

        profile="high",
        pix_fmt="yuv420p",

        acodec="aac",
        **{
            "b:a": AUDIO_BITRATE,
        },
        ar=AUDIO_SAMPLE_RATE,
        ac=AUDIO_CHANNELS,

        f="flv",
        flush_packets=1,
    )

    print("\nGenerated FFmpeg command:")
    print(" ".join(output.compile()))
    print()

    try:
        ffmpeg.run(
            output,
            overwrite_output=True,
        )

    except ffmpeg.Error as e:
        print("\nFFmpeg error:")

        if e.stderr:
            print(e.stderr.decode("utf-8", errors="replace"))
        else:
            print(e)

    except KeyboardInterrupt:
        print("\nStream stopped.")


if __name__ == "__main__":
    main()
