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


async def generate_voice_line(text: str, output_path: str, voice: str = "en-US-GuyNeural") -> bool:
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
    Generates a bouncing circle face with blinking eyes and talking mouth expressions.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow not installed, skipping avatar frame generation")
        return

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    size = 150
    frames = 30  # 30 frames for a smooth bounce loop

    import math

    for frame_idx in range(frames):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Bounce offset (sine wave)
        bounce_y = int(8 * math.sin(2 * math.pi * frame_idx / frames))
        center_y = size // 2 + bounce_y

        # Head circle
        head_r = 45
        draw.ellipse(
            [size // 2 - head_r, center_y - head_r, size // 2 + head_r, center_y + head_r],
            fill=(255, 220, 180, 255),
            outline=(200, 160, 120, 255),
            width=2,
        )

        # Hair / cap
        draw.chord(
            [size // 2 - head_r - 2, center_y - head_r - 4, size // 2 + head_r + 2, center_y],
            180, 360,
            fill=(60, 40, 30, 255),
            outline=(40, 25, 20, 255),
            width=2,
        )

        # Eyes (blinking every 15 frames)
        eye_y = center_y - 10
        if frame_idx % 15 < 13:  # open eyes
            draw.ellipse([size // 2 - 18, eye_y - 5, size // 2 - 10, eye_y + 5], fill=(40, 40, 40, 255))
            draw.ellipse([size // 2 + 10, eye_y - 5, size // 2 + 18, eye_y + 5], fill=(40, 40, 40, 255))
        else:  # blink
            draw.line([size // 2 - 18, eye_y, size // 2 - 10, eye_y], fill=(40, 40, 40, 255), width=2)
            draw.line([size // 2 + 10, eye_y, size // 2 + 18, eye_y], fill=(40, 40, 40, 255), width=2)

        # Mouth (animated open/closed for talking effect)
        mouth_y = center_y + 12
        if frame_idx % 6 < 3:
            draw.ellipse(
                [size // 2 - 10, mouth_y - 4, size // 2 + 10, mouth_y + 8],
                fill=(200, 80, 80, 255),
            )
        else:
            draw.arc(
                [size // 2 - 12, mouth_y - 2, size // 2 + 12, mouth_y + 6],
                0, 180,
                fill=(200, 80, 80, 255),
                width=2,
            )

        img.save(AVATAR_DIR / f"frame_{frame_idx:03d}.png")

    logger.info(f"Generated {frames} avatar frames in {AVATAR_DIR}")


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
