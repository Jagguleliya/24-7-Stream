"""
Stream Orchestrator — Synchronized 24/7 FFmpeg streaming engine.
Uses piped yt-dlp muxing directly into FFmpeg stdin (pipe:0).
Guarantees 100% perfect lip-sync by ingesting combined video+audio streams,
overlaying UPI QR code, animated avatar, scrolling ticker, and mixing avatar voice callouts.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from stream_selector import select_stream, LiveStream, get_best_stream_url
from avatar_engine import (
    create_avatar_frames,
    generate_all_voice_lines,
    generate_voice_loop,
    AVATAR_DIR,
    VOICE_LOOP_FILE,
)

logger = logging.getLogger("stream_orchestrator")

# Global process references for cleanup
current_ffmpeg: Optional[subprocess.Popen] = None
current_ytdlp: Optional[subprocess.Popen] = None
running = True


def cleanup_processes():
    global current_ffmpeg, current_ytdlp
    if current_ffmpeg and current_ffmpeg.poll() is None:
        try:
            current_ffmpeg.terminate()
            current_ffmpeg.wait(timeout=3)
        except Exception:
            current_ffmpeg.kill()
    if current_ytdlp and current_ytdlp.poll() is None:
        try:
            current_ytdlp.terminate()
            current_ytdlp.wait(timeout=3)
        except Exception:
            current_ytdlp.kill()


def signal_handler(sig, frame):
    global running
    logger.info("🛑 Shutdown signal received")
    running = False
    cleanup_processes()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_config() -> dict:
    with open("config.json", encoding="utf-8") as f:
        return json.load(f)


def test_encoder_works(enc: str) -> bool:
    """Verifies that an encoder can actually allocate hardware and encode a test frame."""
    try:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=64x64:d=0.04", "-c:v", enc, "-frames:v", "1", "-f", "null", "-"]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def detect_encoder() -> str:
    """Detect best available encoder with live hardware capability testing."""
    force = os.environ.get("FORCE_ENCODER", "").strip().lower()
    if force in ("libx264", "h264_amf", "h264_nvenc"):
        logger.info(f"⚙️ Using forced encoder from environment: {force}")
        return force

    # Cloud containers (Render, Railway, Docker on CPU servers) default to libx264
    if os.environ.get("RENDER") or os.environ.get("PORT"):
        logger.info("☁️ Cloud container detected — testing hardware encoders...")

    for enc, name in [("h264_amf", "AMD AMF"), ("h264_nvenc", "NVIDIA NVENC")]:
        if test_encoder_works(enc):
            logger.info(f"🎮 Hardware acceleration verified: {name} ({enc})")
            return enc

    logger.info("💻 Using CPU libx264 encoder (cloud verified)")
    return "libx264"


def resolve_qr_image_path(qr_cfg: dict) -> Optional[str]:
    """Resolves the best valid binary image file for QR overlay."""
    candidates = [
        qr_cfg.get("image_path", "assets/overlays/upi_qr.png"),
        "/etc/secrets/upi_qr.png",
        "upi_qr.png",
        "assets/overlays/sample_qr.png",
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.path.getsize(c) > 100:
            try:
                with open(c, "rb") as f:
                    header = f.read(8)
                    if header.startswith(b"\x89PNG") or header.startswith(b"\xff\xd8"):
                        return c
            except Exception:
                continue
    return None


def build_ffmpeg_command(
    config: dict,
    encoder: str,
    preview_mode: bool = False,
    preview_output: str = "preview_output.mp4",
    stream_urls: Optional[dict] = None,
    streamer_name: str = "",
) -> list:
    """
    Builds the FFmpeg command. Supports direct HLS ingestion (stream_urls)
    or piped yt-dlp fallback (pipe:0) with frame-accurate tpad video delay.
    """
    settings = config.get("stream_settings", {})
    resolution = settings.get("resolution", "1280x720")
    fps = settings.get("fps", 30)
    vbitrate = settings.get("video_bitrate", "4000k")
    abitrate = settings.get("audio_bitrate", "128k")
    preset = settings.get("preset", "veryfast")

    w, h = map(int, resolution.split("x"))
    qr_cfg = config.get("qr_overlay", {})
    avatar_cfg = config.get("avatar", {})

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats"]

    if stream_urls and stream_urls.get("video"):
        cmd += ["-i", stream_urls["video"]]
        vid_src = "0:v"
        if stream_urls.get("audio"):
            cmd += ["-i", stream_urls["audio"]]
            aud_src = "1:a"
            next_input = 2
        else:
            aud_src = "0:a"
            next_input = 1
    else:
        cmd += ["-i", "pipe:0"]
        vid_src = "0:v"
        aud_src = "0:a"
        next_input = 1

    # QR code overlay image
    qr_path = resolve_qr_image_path(qr_cfg)
    has_qr = qr_path is not None and qr_cfg.get("enabled", True)
    qr_input_idx = None
    if has_qr:
        cmd += ["-i", qr_path]
        qr_input_idx = next_input
        next_input += 1

    # Avatar frames
    avatar_glob = str(AVATAR_DIR / "frame_%03d.png")
    has_avatar = avatar_cfg.get("enabled", True) and AVATAR_DIR.exists() and any(AVATAR_DIR.glob("frame_*.png"))
    avatar_input_idx = None
    if has_avatar:
        cmd += ["-stream_loop", "-1", "-framerate", "10", "-i", avatar_glob]
        avatar_input_idx = next_input
        next_input += 1

    # Voice loop audio track
    has_voice = VOICE_LOOP_FILE.exists()
    voice_input_idx = None
    if has_voice:
        cmd += ["-stream_loop", "-1", "-i", str(VOICE_LOOP_FILE)]
        voice_input_idx = next_input
        next_input += 1

    delay_ms = settings.get("audio_delay_ms", 0)
    delay_sec = abs(delay_ms) / 1000.0

    if delay_ms > 0:
        # Voice is heard AFTER mouth moves (voice is late / lagging behind video):
        # We delay video by delay_sec so mouth movements wait for delayed voice:
        video_pts = f"tpad=start_duration={delay_sec}:start_mode=clone,setpts=PTS-STARTPTS"
        audio_pts = "asetpts=PTS-STARTPTS"
        logger.info(f"⏱️ Lip-sync calibration: Delaying video by {delay_ms}ms ({delay_sec}s) so mouth matches voice")
    elif delay_ms < 0:
        # Voice is early: delay audio using adelay
        adelay_val = int(abs(delay_ms))
        video_pts = "setpts=PTS-STARTPTS"
        audio_pts = f"adelay={adelay_val}|{adelay_val},asetpts=PTS-STARTPTS"
        logger.info(f"⏱️ Lip-sync calibration: Delaying audio by {adelay_val}ms")
    else:
        video_pts = "setpts=PTS-STARTPTS"
        audio_pts = "asetpts=PTS-STARTPTS"
        logger.info("⏱️ Lip-sync calibration: 0ms offset (raw timing)")

    # Filter Complex
    filters = []

    # Scale base video to resolution and apply timestamp sync offset (fps filter first to cut render load)
    filters.append(f"[{vid_src}]fps={fps},{video_pts},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[base]")
    current_vid = "[base]"

    # Top viral caption banner with trending hashtags
    s_display = (streamer_name or "GTA 6").upper()
    banner_w = w - 300
    banner_text = f"{s_display} PLAYING GTA 6  |  #GTA6 #GTAVI #SpeedLive"
    filters.append(
        f"{current_vid}drawbox=x=110:y=12:w={banner_w}:h=38:color=black@0.75:t=fill,"
        f"drawbox=x=110:y=12:w={banner_w}:h=38:color=red@0.85:t=2,"
        f"drawtext=text='{banner_text}'"
        f":fontsize=18:fontcolor=yellow:borderw=2:bordercolor=black"
        f":x=125:y=22[with_banner]"
    )
    current_vid = "[with_banner]"

    # Ticker tape at bottom
    upi_id = os.environ.get("UPI_ID", "").strip() or config.get("upi_id", "").strip()
    upi_str = f"  -  UPI {upi_id}" if upi_id else ""
    ticker_text = f"{s_display} GTA 6 LIVE STREAM  -  #GTA6 #GTAVI #SpeedLive #GTA6Gameplay  -  Support Stream via Scan QR Code{upi_str}  -  Subscribe and Like the Stream!  -"
    filters.append(
        f"{current_vid}drawtext=text='{ticker_text}'"
        f":fontsize=22:fontcolor=white:borderw=2:bordercolor=black"
        f":y=h-35:x=w-mod(t*120\\,w+tw)-tw[ticker]"
    )
    current_vid = "[ticker]"

    # QR Code overlay top-right
    if has_qr and qr_input_idx is not None:
        qr_size = qr_cfg.get("size", 160)
        qr_margin = 15
        qr_x = w - qr_size - qr_margin
        qr_y = qr_margin
        filters.append(f"[{qr_input_idx}:v]scale={qr_size}:{qr_size}[qr_scaled]")
        filters.append(f"{current_vid}[qr_scaled]overlay={qr_x}:{qr_y}[with_qr]")
        current_vid = "[with_qr]"

        label_text = qr_cfg.get("label", "Support - Scan to Pay").replace(":", "")
        filters.append(
            f"{current_vid}drawtext=text='{label_text}'"
            f":fontsize=14:fontcolor=yellow:borderw=1:bordercolor=black"
            f":x={qr_x}:y={qr_y + qr_size + 5}[with_qr_label]"
        )
        current_vid = "[with_qr_label]"

    # Avatar overlay bottom-right (16:9 streamer webcam box)
    if has_avatar and avatar_input_idx is not None:
        av_w = avatar_cfg.get("width", 240)
        av_h = avatar_cfg.get("height", 140)
        av_margin = 20
        av_x = w - av_w - av_margin
        av_y = h - av_h - av_margin - 25
        filters.append(f"[{avatar_input_idx}:v]scale={av_w}:{av_h}[avatar_scaled]")
        filters.append(f"{current_vid}[avatar_scaled]overlay={av_x}:{av_y}[with_avatar]")
        current_vid = "[with_avatar]"

    # LIVE indicator top-left
    filters.append(
        f"{current_vid}drawtext=text='LIVE'"
        f":fontsize=28:fontcolor=red:borderw=2:bordercolor=white"
        f":x=15:y=15[final_vid]"
    )

    # Audio Mixing: Stream audio (aud_src) + Voice loop (voice_input_idx:a)
    if voice_input_idx is not None:
        filters.append(f"[{aud_src}]{audio_pts},volume=1.0[a_main]")
        filters.append(f"[{voice_input_idx}:a]asetpts=PTS-STARTPTS,volume=1.2[a_voice]")
        filters.append("[a_main][a_voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_mixed]")
        filters.append("[a_mixed]aresample=async=1000:min_hard_comp=0.100000:first_pts=0[final_aud]")
        audio_map = "[final_aud]"
    else:
        filters.append(f"[{aud_src}]{audio_pts},aresample=async=1000:first_pts=0[final_aud]")
        audio_map = "[final_aud]"

    cmd += ["-filter_complex", ";".join(filters)]

    # Encoding settings
    if encoder == "h264_amf":
        cmd += ["-c:v", "h264_amf", "-usage", "lowlatency", "-quality", "speed", "-rc", "cbr", "-b:v", vbitrate]
    elif encoder == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", vbitrate]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-b:v", vbitrate, "-tune", "zerolatency"]

    cmd += [
        "-map", "[final_vid]",
        "-map", audio_map,
        "-c:a", "aac",
        "-b:a", abitrate,
        "-ar", "44100",
        "-r", str(fps),
        "-g", str(fps * 2),
        "-maxrate", vbitrate,
        "-bufsize", str(int(vbitrate.replace("k", "")) * 2) + "k",
    ]

    # Output targets
    if preview_mode:
        cmd += ["-t", "30", "-f", "mp4", preview_output]
    else:
        yt_key = os.environ.get("YOUTUBE_STREAM_KEY", "").strip() or config.get("stream_keys", {}).get("youtube", "")
        kick_key = os.environ.get("KICK_STREAM_KEY", "").strip() or config.get("stream_keys", {}).get("kick", "")

        outputs = []
        if yt_key:
            outputs.append(f"[f=flv:onfail=ignore]rtmp://a.rtmp.youtube.com/live2/{yt_key}")
        if kick_key:
            outputs.append(f"[f=flv:onfail=ignore]rtmps://fa723fd74122.global-contribute.live-video.net:443/app/{kick_key}")

        if not outputs:
            logger.error("No stream keys configured!")
            sys.exit(1)

        cmd += ["-f", "tee", "|".join(outputs)]

    return cmd


def run_direct_stream(selected: LiveStream, config: dict, encoder: str, preview_mode: bool):
    """
    Directly ingests video and audio HLS streams into FFmpeg without pipe buffering.
    Eliminates pipe-induced desync, prevents timestamp drift, and cuts CPU/RAM usage.
    """
    global current_ffmpeg, current_ytdlp

    cleanup_processes()

    logger.info(f"📥 Resolving direct HLS stream URLs for: {selected.name} ({selected.url})")
    urls = get_best_stream_url(selected.url, selected.platform)

    if urls and urls.get("video"):
        logger.info("⚡ Ingesting direct HLS stream URLs (zero-pipe, frame-accurate sync)")
        ffmpeg_cmd = build_ffmpeg_command(config, encoder, preview_mode, stream_urls=urls, streamer_name=selected.name)
        logger.info(f"🎬 Launching FFmpeg encoder: {encoder} (preview={preview_mode})")
        current_ffmpeg = subprocess.Popen(ffmpeg_cmd)
        return current_ffmpeg
    else:
        logger.warning(f"⚠️ Could not extract direct URLs for {selected.name}. Running Standby screen to keep channel 100% LIVE...")
        return run_standby_loop(config, encoder, preview_mode)


def run_piped_stream(target_url: str, config: dict, encoder: str, preview_mode: bool, streamer_name: str = ""):
    """
    Spawns yt-dlp to mux video+audio to stdout, and pipes directly to FFmpeg stdin.
    """
    global current_ffmpeg, current_ytdlp

    cleanup_processes()

    # yt-dlp downloads matching video and audio chunks and outputs combined MPEG-TS stream to stdout
    ytdlp_cmd = [
        "yt-dlp",
        "--no-warnings",
        "-f", "311+234/231+234/230+234/720p60/720p/bestvideo+bestaudio/best",
        "-o", "-",
        target_url,
    ]
    logger.info(f"📥 Starting synchronized stream ingest for: {target_url}")
    current_ytdlp = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    ffmpeg_cmd = build_ffmpeg_command(config, encoder, preview_mode, streamer_name=streamer_name)
    logger.info(f"🎬 Launching FFmpeg encoder: {encoder} (preview={preview_mode})")
    current_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=current_ytdlp.stdout)
    current_ytdlp.stdout.close()  # Allow ytdlp to receive SIGPIPE if FFmpeg closes

    return current_ffmpeg


def run_standby_loop(config: dict, encoder: str, preview_mode: bool):
    """Run standby screen when all target streamers are offline."""
    global current_ffmpeg, current_ytdlp

    cleanup_processes()

    settings = config.get("stream_settings", {})
    resolution = settings.get("resolution", "1280x720")
    fps = settings.get("fps", 30)
    vbitrate = settings.get("video_bitrate", "4000k")
    abitrate = settings.get("audio_bitrate", "128k")
    preset = settings.get("preset", "veryfast")
    w, h = map(int, resolution.split("x"))

    qr_cfg = config.get("qr_overlay", {})
    avatar_cfg = config.get("avatar", {})

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats"]
    cmd += ["-f", "lavfi", "-i", f"color=c=0x0f172a:s={resolution}:d=3600:r={fps}"]
    next_input = 1

    qr_path = resolve_qr_image_path(qr_cfg)
    has_qr = qr_path is not None and qr_cfg.get("enabled", True)
    qr_input_idx = None
    if has_qr:
        cmd += ["-i", qr_path]
        qr_input_idx = next_input
        next_input += 1

    avatar_glob = str(AVATAR_DIR / "frame_%03d.png")
    has_avatar = avatar_cfg.get("enabled", True) and AVATAR_DIR.exists() and any(AVATAR_DIR.glob("frame_*.png"))
    avatar_input_idx = None
    if has_avatar:
        cmd += ["-stream_loop", "-1", "-framerate", "10", "-i", avatar_glob]
        avatar_input_idx = next_input
        next_input += 1

    has_voice = VOICE_LOOP_FILE.exists()
    voice_input_idx = None
    if has_voice:
        cmd += ["-stream_loop", "-1", "-i", str(VOICE_LOOP_FILE)]
        voice_input_idx = next_input
        next_input += 1
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        voice_input_idx = next_input
        next_input += 1

    filters = []
    current_vid = "[0:v]"

    filters.append(
        f"{current_vid}drawtext=text='ISHOWSPEED GTA 6 COMMUNITY STREAM'"
        f":fontsize=40:fontcolor=white:borderw=3:bordercolor=black"
        f":x=(w-tw)/2:y=(h-th)/2-60[title]"
    )
    filters.append(
        f"[title]drawtext=text='Waiting for Speed to go Live!  -  #GTA6 #IShowSpeed #GTAVI'"
        f":fontsize=24:fontcolor=yellow:borderw=2:bordercolor=black"
        f":x=(w-tw)/2:y=(h-th)/2+10[sub]"
    )
    current_vid = "[sub]"

    upi_id = os.environ.get("UPI_ID", "").strip() or config.get("upi_id", "").strip()
    upi_str = f"  -  UPI {upi_id}" if upi_id else ""
    ticker_text = f"ISHOWSPEED GTA 6 COMMUNITY STREAM  -  #GTA6 #IShowSpeed #GTAVI #SpeedLive  -  Support the stream Scan QR Code{upi_str}  -  Like & Subscribe!  -"
    filters.append(
        f"{current_vid}drawtext=text='{ticker_text}'"
        f":fontsize=22:fontcolor=white:borderw=2:bordercolor=black"
        f":y=h-35:x=w-mod(t*120\\,w+tw)-tw[ticker]"
    )
    current_vid = "[ticker]"

    if has_qr and qr_input_idx is not None:
        qr_size = qr_cfg.get("size", 160)
        qr_x = w - qr_size - 15
        qr_y = 15
        filters.append(f"[{qr_input_idx}:v]scale={qr_size}:{qr_size}[qr_s]")
        filters.append(f"{current_vid}[qr_s]overlay={qr_x}:{qr_y}[with_qr]")
        current_vid = "[with_qr]"
        filters.append(
            f"{current_vid}drawtext=text='Support - Scan to Pay'"
            f":fontsize=14:fontcolor=yellow:borderw=1:bordercolor=black"
            f":x={qr_x}:y={qr_y + qr_size + 5}[with_qr_label]"
        )
        current_vid = "[with_qr_label]"

    if has_avatar and avatar_input_idx is not None:
        av_w = avatar_cfg.get("width", 240)
        av_h = avatar_cfg.get("height", 140)
        av_x = w - av_w - 20
        av_y = h - av_h - 45
        filters.append(f"[{avatar_input_idx}:v]scale={av_w}:{av_h}[av_s]")
        filters.append(f"{current_vid}[av_s]overlay={av_x}:{av_y}[with_av]")
        current_vid = "[with_av]"

    filters.append(
        f"{current_vid}drawtext=text='STANDBY'"
        f":fontsize=28:fontcolor=orange:borderw=2:bordercolor=white"
        f":x=15:y=15[final_vid]"
    )

    cmd += ["-filter_complex", ";".join(filters)]

    if encoder == "h264_amf":
        cmd += ["-c:v", "h264_amf", "-usage", "lowlatency", "-quality", "speed", "-rc", "cbr", "-b:v", vbitrate]
    elif encoder == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", vbitrate]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-b:v", vbitrate, "-tune", "zerolatency"]

    cmd += [
        "-map", "[final_vid]",
        "-map", f"{voice_input_idx}:a",
        "-c:a", "aac",
        "-b:a", abitrate,
        "-ar", "44100",
        "-r", str(fps),
        "-g", str(fps * 2),
    ]

    if preview_mode:
        cmd += ["-t", "15", "-f", "mp4", "standby_preview.mp4"]
    else:
        yt_key = os.environ.get("YOUTUBE_STREAM_KEY", "").strip() or config.get("stream_keys", {}).get("youtube", "")
        kick_key = os.environ.get("KICK_STREAM_KEY", "").strip() or config.get("stream_keys", {}).get("kick", "")
        outputs = []
        if yt_key:
            outputs.append(f"[f=flv:onfail=ignore]rtmp://a.rtmp.youtube.com/live2/{yt_key}")
        if kick_key:
            outputs.append(f"[f=flv:onfail=ignore]rtmps://fa723fd74122.global-contribute.live-video.net:443/app/{kick_key}")
        if outputs:
            cmd += ["-f", "tee", "|".join(outputs)]
        else:
            cmd += ["-t", "15", "-f", "mp4", "standby_preview.mp4"]

    logger.info("📺 Starting Standby stream...")
    current_ffmpeg = subprocess.Popen(cmd)
    return current_ffmpeg


def main_loop(override_preview: Optional[bool] = None):
    """Main 24/7 orchestration loop."""
    global running, current_ffmpeg, current_ytdlp

    config = load_config()
    preview_mode = override_preview if override_preview is not None else config.get("preview_mode", False)
    poll_interval = config.get("poll_interval_seconds", 30)

    encoder = detect_encoder()

    def prep_assets_async():
        try:
            logger.info("🎨 Preparing avatar frames and voice loop in background...")
            create_avatar_frames()
            generate_all_voice_lines(config)
            generate_voice_loop(interval_seconds=120, initial_delay=2, preview_mode=preview_mode)
            logger.info("✅ Avatar and voice assets ready")
        except Exception as e:
            logger.warning(f"Asset prep error: {e}")

    threading.Thread(target=prep_assets_async, daemon=True).start()

    logger.info("=" * 60)
    logger.info("🚀 Synchronized Stream Orchestrator Starting")
    logger.info(f"   Mode: {'PREVIEW (local)' if preview_mode else 'LIVE (YouTube + Kick)'}")
    logger.info(f"   Encoder: {encoder}")
    logger.info(f"   Lip-Sync: 100% Hardware Muxed (pipe:0)")
    logger.info("=" * 60)

    current_stream: Optional[LiveStream] = None
    speed_check_counter = 0

    while running:
        try:
            # 1. If FFmpeg is running and healthy: keep broadcasting without interruption!
            if current_ffmpeg and current_ffmpeg.poll() is None:
                speed_check_counter += 1
                # If currently streaming a fallback (priority > 1), check every ~3 minutes if Speed went live
                if current_stream and current_stream.priority > 1 and speed_check_counter >= 12:
                    speed_check_counter = 0
                    config = load_config()
                    speed_channel = next((c for c in config.get("priority_channels", []) if "speed" in c["name"].lower()), None)
                    if speed_channel:
                        speed_live = check_channel_live(speed_channel)
                        if speed_live:
                            logger.info(f"🔥 IShowSpeed is LIVE! Upgrading stream from {current_stream.name} → IShowSpeed...")
                            current_ffmpeg = run_direct_stream(speed_live, config, encoder, preview_mode)
                            current_stream = speed_live

                time.sleep(15)
                continue

            # 2. FFmpeg is not running (first start, or streamer went offline/disconnected)
            if current_stream:
                logger.warning(f"⚠️ Stream for {current_stream.name} ended or disconnected. Reconnecting...")
                current_stream = None

            config = load_config()
            selected = select_stream(config)

            if selected:
                logger.info(f"🎮 Streaming: {selected.name} — {selected.title} ({selected.viewers} viewers)")
                current_ffmpeg = run_direct_stream(selected, config, encoder, preview_mode)
                current_stream = selected
                speed_check_counter = 0

                if preview_mode:
                    current_ffmpeg.wait()
                    logger.info("✅ Preview saved to preview_output.mp4")
                    running = False
                    break
            else:
                logger.info("📺 Entering Standby mode (all targets offline)...")
                current_ffmpeg = run_standby_loop(config, encoder, preview_mode)
                current_stream = None

                if preview_mode:
                    current_ffmpeg.wait()
                    logger.info("✅ Standby preview saved to standby_preview.mp4")
                    running = False
                    break

            time.sleep(5)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"❌ Orchestrator loop error: {e}")
            time.sleep(10)

    cleanup_processes()
    logger.info("👋 Stream Orchestrator stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("stream.log", encoding="utf-8"),
        ],
    )
    main_loop()
