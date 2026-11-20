"""
Avatar Engine — Generates animated corner avatar overlay + TTS voice lines.
Uses edge-tts for free high-quality text-to-speech and Pillow for avatar frames.
Builds a continuous voice loop with configurable interval silence for FFmpeg live mixing.
"""

import asyncio
import json
import logging
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("avatar_engine")

AVATAR_DIR = Path("assets/overlays/avatar_frames")
VOICE_DIR = Path("assets/audio/voice_lines")
VOICE_LOOP_FILE = Path("assets/audio/voice_loop.mp3")


async def generate_voice_line(text: str, output_path: str, voice: str = "en-US-AvaNeural") -> bool:
    """Generate a TTS audio file using edge-tts."""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        logger.info(f"Generated voice: {output_path}")
        return True
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        return False


def generate_all_voice_lines(config: dict):
    """Pre-generate all individual voice line audio files."""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    voice_lines = config.get("avatar", {}).get("voice_lines", [])

    for i, line in enumerate(voice_lines):
        output = VOICE_DIR / f"voice_{i:03d}.mp3"
        if not output.exists():
            asyncio.run(generate_voice_line(line, str(output)))
            logger.info(f"Generated voice line {i}: {line[:50]}...")
        else:
            logger.debug(f"Voice line {i} already exists, skipping")


def generate_voice_loop(interval_seconds: int = 120, initial_delay: int = 2, preview_mode: bool = False) -> Optional[str]:
    """
    Concatenates all voice lines with silence pauses into a single voice loop.
    FFmpeg loops this audio track with -stream_loop -1 and mixes it with the stream audio!
    In preview mode, intervals are shorter (6s) so multiple voice lines are audible in a 30s test clip.
    """
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    voice_files = sorted(list(VOICE_DIR.glob("voice_*.mp3")))

    if not voice_files:
        logger.warning("No voice files found to build loop")
        return None

    actual_interval = 6 if preview_mode else interval_seconds

    # Build FFmpeg command to chain silence and voice files
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filter_inputs = []

    # Initial delay before first speech
    cmd += ["-f", "lavfi", "-t", str(initial_delay), "-i", "anullsrc=r=44100:cl=stereo"]
    filter_inputs.append("[0:a]")
    inp_count = 1

    for vf in voice_files:
        # Add voice line
        cmd += ["-i", str(vf)]
        filter_inputs.append(f"[{inp_count}:a]")
        inp_count += 1

        # Add pause after voice line
        cmd += ["-f", "lavfi", "-t", str(actual_interval), "-i", "anullsrc=r=44100:cl=stereo"]
        filter_inputs.append(f"[{inp_count}:a]")
        inp_count += 1

    filter_complex = f"{''.join(filter_inputs)}concat=n={inp_count}:v=0:a=1[out]"
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(VOICE_LOOP_FILE),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode == 0 and VOICE_LOOP_FILE.exists():
            logger.info(f"✅ Generated voice loop ({inp_count} segments) -> {VOICE_LOOP_FILE}")
            return str(VOICE_LOOP_FILE)
        else:
            logger.error(f"Error building voice loop: {res.stderr}")
    except Exception as e:
        logger.error(f"Voice loop generation error: {e}")

    return None


def create_avatar_frames():
    """
    Create animated avatar frames using Pillow.
    Uses realistic female streamer webcam base (streamer_base.jpg) with rounded corners,
    subtle natural breathing motion, animated pulsing RGB border, and LIVE CAM HUD!
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow not installed, skipping avatar frame generation")
        return

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    base_path = Path("assets/overlays/streamer_base.jpg")
    frames = 30
    w, h = 240, 140
    import math

    if base_path.exists():
        try:
            base = Image.open(base_path).convert("RGBA")
            orig_w, orig_h = base.size
            crop_w = int(orig_h * (w / h))
            left = max(0, (orig_w - crop_w) // 2)
            streamer = base.crop((left, 0, left + crop_w, orig_h)).resize((w, h), Image.Resampling.LANCZOS)

            radius = 10
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (w, h)], radius=radius, fill=255)

            for i in range(frames):
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                shift_y = int(1.5 * math.sin(2 * math.pi * i / frames))
                streamer_shifted = streamer.copy()
                if shift_y != 0:
                    streamer_shifted = Image.new("RGBA", (w, h), (15, 23, 42, 255))
                    streamer_shifted.paste(streamer, (0, shift_y))

                frame.paste(streamer_shifted, (0, 0), mask)
                draw = ImageDraw.Draw(frame)

                # Pulsing RGB border
                hue_phase = i / frames
                r_val = int(128 + 127 * math.sin(2 * math.pi * hue_phase))
                g_val = int(128 + 127 * math.sin(2 * math.pi * hue_phase + 2.094))
                b_val = int(128 + 127 * math.sin(2 * math.pi * hue_phase + 4.188))
                draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, outline=(r_val, g_val, b_val, 220), width=2)

                # Top-left LIVE CAM indicator
                dot_alpha = int(180 + 75 * math.sin(2 * math.pi * i / 15))
                draw.ellipse([(8, 8), (14, 14)], fill=(255, 0, 0, dot_alpha))
                draw.text((18, 5), "LIVE CAM", fill=(255, 255, 255, 230))

                # Bottom tag
                draw.rectangle([(0, h - 20), (w, h)], fill=(0, 0, 0, 140))
                draw.text((10, h - 17), "CO-HOST REACTION", fill=(240, 240, 240, 220))

                frame.save(AVATAR_DIR / f"frame_{i:03d}.png")

            logger.info(f"Generated {frames} realistic female streamer webcam frames in {AVATAR_DIR}")
            return
        except Exception as e:
            logger.warning(f"Error creating realistic webcam frames, using fallback: {e}")

    # Fallback procedural generation if streamer_base.jpg cannot be loaded
    for frame_idx in range(frames):
        img = Image.new("RGBA", (w, h), (15, 23, 42, 220))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=8, outline=(0, 200, 255, 220), width=2)
        draw.text((20, h // 2 - 10), "STREAMER REACTION CAM", fill=(255, 255, 255, 240))
        img.save(AVATAR_DIR / f"frame_{frame_idx:03d}.png")

    logger.info(f"Generated {frames} fallback avatar frames in {AVATAR_DIR}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    with open("config.json") as f:
        config = json.load(f)

    print("🎨 Generating avatar frames...")
    create_avatar_frames()

    print("🎙️ Generating voice lines...")
    generate_all_voice_lines(config)

    print("🔁 Building voice loop track...")
    loop_file = generate_voice_loop(interval_seconds=120, initial_delay=2, preview_mode=True)

    print(f"✅ Avatar assets ready! Loop track: {loop_file}")
