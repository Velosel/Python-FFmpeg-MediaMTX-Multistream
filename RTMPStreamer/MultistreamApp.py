import ffmpeg


# =============================================================
# STREAM CONFIGURATION
# =============================================================

RTMP_URL = "rtmp://127.0.0.1/live"

CHANNEL_NAME = "Ravaelv"
STREAM_TITLE = "LIVE STREAM"
SOCIAL_TEXT = "Twitch.tv/Ravaelv"

WEBCAM = "BRIO 4K Stream Edition"
BRIO_MIC = "Microphone (BRIO 4K Stream Edition)"

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
FPS = 30

# Webcam
WEBCAM_CAPTURE_WIDTH = 640
WEBCAM_CAPTURE_HEIGHT = 360
WEBCAM_WIDTH = 320
WEBCAM_HEIGHT = 180
WEBCAM_MARGIN = 20

# Unified chat
CHAT_FILE = r"C:\RTMPStreamer\chat_overlay.txt"
CHAT_X = 20
CHAT_Y = 100
CHAT_WIDTH = 350
CHAT_HEIGHT = 500
CHAT_RELOAD_FRAMES = 15  # 2x/sec at 30 FPS


# =============================================================
# GRAPHICS
# =============================================================

FONT_FILE = r"C:\Windows\Fonts\segoeui.ttf"
FONT_BOLD_FILE = r"C:\Windows\Fonts\segoeuib.ttf"


def add_graphics(video):
    """
    Clean broadcast overlay.

    Layout:
        Top center: LIVE STREAM + Twitch.tv/Ravaelv
        Top right:  LIVE badge
        Bottom-left: small unified chat panel
        Bottom-right: webcam
    """

    # ---------------------------------------------------------
    # Compact unified chat panel - far left / bottom-left
    # ---------------------------------------------------------
    video = video.filter(
        "drawbox",
        x=12,
        y=505,
        w=300,
        h=195,
        color="050505@0.66",
        t="fill",
    )

    video = video.filter(
        "drawbox",
        x=12,
        y=505,
        w=300,
        h=34,
        color="00D9FF@0.90",
        t="fill",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_BOLD_FILE,
        text="LIVE CHAT",
        x=24,
        y=513,
        fontsize=17,
        fontcolor="000000",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_FILE,
        textfile=CHAT_FILE,
        reload=CHAT_RELOAD_FRAMES,
        x=24,
        y=550,
        fontsize=13,
        line_spacing=-6,
        fontcolor="FFFFFF",
        shadowcolor="000000@0.85",
        shadowx=1,
        shadowy=1,
        box=0,
    )

    # ---------------------------------------------------------
    # TOP STREAM IDENTIFIER
    # "LIVE STREAM — Twitch.tv/Ravaelv"
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
        text="LIVE STREAM",
        x=38,
        y=30,
        fontsize=18,
        fontcolor="FFFFFF",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_FILE,
        text="Twitch.tv/Ravaelv",
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
        x=1135,
        y=20,
        w=125,
        h=42,
        color="FF1744@0.90",
        t="fill",
    )

    video = video.filter(
        "drawtext",
        fontfile=FONT_BOLD_FILE,
        text="● LIVE",
        x=1144,
        y=30,
        fontsize=20,
        fontcolor="FFFFFF",
    )

    return video


# =============================================================
# MAIN STREAM
# =============================================================

def main():
    print("Starting Ravaelv desktop + BRIO + unified chat stream...")
    print(f"RTMP destination: {RTMP_URL}")

    desktop = ffmpeg.input(
        "desktop",
        f="gdigrab",
        video_size=(1920, 1080),
        framerate=FPS,
        draw_mouse=1,
        thread_queue_size=64,
    )

    desktop_video = (
        desktop.video
        .filter("scale", OUTPUT_WIDTH, OUTPUT_HEIGHT)
        .filter("setpts", "N/(30*TB)")
    )

    brio = ffmpeg.input(
        f"video={WEBCAM}:audio={BRIO_MIC}",
        f="dshow",
        vcodec="mjpeg",
        video_size=(WEBCAM_CAPTURE_WIDTH, WEBCAM_CAPTURE_HEIGHT),
        framerate=FPS,
        sample_rate=48000,
        channels=2,
        audio_buffer_size=100,
        rtbufsize="4M",
        thread_queue_size=64,
    )

    webcam_video = (
        brio.video
        .filter("scale", WEBCAM_WIDTH, WEBCAM_HEIGHT)
        .filter("setpts", "N/(30*TB)")
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
            "b:v": "4000k",
            "maxrate": "4000k",
            "bufsize": "8000k",
        },

        r=FPS,
        fps_mode="cfr",

        g=60,
        keyint_min=60,
        bf=0,

        profile="high",
        pix_fmt="yuv420p",

        acodec="aac",
        **{
            "b:a": "128k",
        },
        ar=48000,
        ac=2,

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
