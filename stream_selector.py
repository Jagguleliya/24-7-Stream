"""
Stream Selector — Priority-based live stream detector.
Checks channels in priority order using yt-dlp:
Priority: Speed > Kai > CarryMinati > GamerFleet > Mythpat > Highest Live Viewers Fallback.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("stream_selector")


@dataclass
class LiveStream:
    name: str
    platform: str
    url: str
    priority: int
    title: str = ""
    viewers: int = 0
    stream_url: str = ""


def check_channel_live(channel: dict) -> Optional[LiveStream]:
    """Check if a single channel is currently live using yt-dlp."""
    url = channel["url"]
    name = channel["name"]
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-warnings",
                "--no-download",
                "--print",
                "%(is_live)s|||%(title)s|||%(concurrent_view_count)s|||%(url)s",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode != 0:
            logger.debug(f"[{name}] yt-dlp returned code {result.returncode}")
            return None

        output = result.stdout.strip()
        if not output:
            return None

        parts = output.split("|||")
        is_live = parts[0].strip().lower() == "true" if len(parts) > 0 else False

        if not is_live:
            logger.info(f"[{name}] Not live")
            return None

        title = parts[1].strip() if len(parts) > 1 else ""
        try:
            viewers = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip() not in ("NA", "None", "") else 0
        except (ValueError, IndexError):
            viewers = 0
        stream_url_raw = parts[3].strip() if len(parts) > 3 else url

        logger.info(f"[{name}] LIVE — {title} ({viewers} viewers)")
        return LiveStream(
            name=name,
            platform=channel.get("platform", "youtube"),
            url=url,
            priority=channel.get("priority", 99),
            title=title,
            viewers=viewers,
            stream_url=stream_url_raw,
        )

    except subprocess.TimeoutExpired:
        logger.warning(f"[{name}] Timeout checking live status")
        return None
    except Exception as e:
        logger.error(f"[{name}] Error: {e}")
        return None


def find_biggest_live_stream() -> Optional[LiveStream]:
    """
    Fallback when no priority channel is live:
    Finds active live GTA VI streams and picks the one with the highest live viewers.
    """
    logger.info("🔍 Searching for top GTA live streams by viewer count...")
    queries = ["ytsearch8:GTA 6 live", "ytsearch8:GTA VI live"]
    candidates = []

    for query in queries:
        try:
            cmd = [
                "yt-dlp",
                "--no-warnings",
                "--no-download",
                "--print",
                "%(is_live)s|||%(uploader)s|||%(title)s|||%(concurrent_view_count)s|||%(webpage_url)s",
                query,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            for line in res.stdout.strip().split("\n"):
                parts = line.strip().split("|||")
                if len(parts) >= 5 and parts[0].strip().lower() == "true":
                    try:
                        viewers = int(parts[3].strip()) if parts[3].strip() not in ("NA", "None", "") else 0
                    except (ValueError, IndexError):
                        viewers = 0
                    candidates.append(LiveStream(
                        name=parts[1].strip() or "Top GTA 6 Streamer",
                        platform="youtube",
                        url=parts[4].strip(),
                        priority=99,
                        title=parts[2].strip(),
                        viewers=viewers,
                        stream_url=parts[4].strip(),
                    ))
        except Exception as e:
            logger.debug(f"Search query error: {e}")

    if candidates:
        best = max(candidates, key=lambda c: c.viewers)
        logger.info(f"🔥 Found highest live watching stream: {best.name} with {best.viewers} viewers!")
        return best

    return None


def get_best_stream_url(channel_url: str, platform: str = "youtube") -> Optional[dict]:
    """
    Get the direct HLS/stream URLs (video + audio) for FFmpeg ingestion.
    Returns dict with 'video' and 'audio' keys, or None on failure.
    Uses single-call fast format fallbacks.
    """
    is_twitch = "twitch.tv" in channel_url.lower() or platform == "twitch"

    if is_twitch:
        # Twitch provides single m3u8 playlist containing both video + audio
        try:
            result = subprocess.run(
                ["yt-dlp", "--no-warnings", "-g", "-f", "720p60/720p/best", channel_url],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip().split("\n")[0]
                logger.info(f"Got Twitch stream URL")
                return {"video": url, "audio": None}
        except Exception as e:
            logger.error(f"Error getting Twitch stream URL: {e}")
    else:
        # YouTube live streams have separate video & audio m3u8 feeds
        fmt = "311+234/231+234/230+234/bestvideo+bestaudio/best"
        try:
            result = subprocess.run(
                ["yt-dlp", "--no-warnings", "-g", "-f", fmt, channel_url],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode == 0 and result.stdout.strip():
                urls = result.stdout.strip().split("\n")
                if len(urls) >= 2:
                    logger.info("Got YouTube video + audio stream URLs")
                    return {"video": urls[0], "audio": urls[1]}
                elif len(urls) == 1:
                    return {"video": urls[0], "audio": None}
        except Exception as e:
            logger.error(f"Error getting YouTube stream URL: {e}")

    # Universal last-resort fallback
    try:
        result = subprocess.run(
            ["yt-dlp", "--no-warnings", "-g", channel_url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            urls = result.stdout.strip().split("\n")
            return {"video": urls[0], "audio": urls[1] if len(urls) > 1 else None}
    except Exception as e:
        logger.error(f"Fallback stream URL error: {e}")

    return None


def select_stream(config: dict) -> Optional[LiveStream]:
    """
    Select the best stream based on priority.
    If dedicated to IShowSpeed, streams Speed when live, otherwise switches to standby.
    """
    channels = sorted(config.get("priority_channels", []), key=lambda c: c.get("priority", 99))

    for channel in channels:
        live = check_channel_live(channel)
        if live:
            logger.info(f"✅ Selected: {live.name} (Priority #{live.priority})")
            return live

    # Optional search fallback (only if enabled in config)
    if config.get("fallback_search", False):
        logger.info("ℹ️ Speed offline. Checking for stream with biggest live viewership...")
        biggest = find_biggest_live_stream()
        if biggest:
            return biggest

    logger.info("❌ Speed is currently offline — staying in Standby mode until next stream")
    return None


def monitor_stream_health(stream: LiveStream) -> bool:
    """Check if the currently selected stream is still live."""
    channel = {
        "name": stream.name,
        "platform": stream.platform,
        "url": stream.url,
        "priority": stream.priority,
    }
    result = check_channel_live(channel)
    return result is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    with open("config.json") as f:
        config = json.load(f)
    stream = select_stream(config)
    if stream:
        print(f"\n🎮 Best stream: {stream.name} — {stream.title}")
        print(f"   Platform: {stream.platform} | Viewers: {stream.viewers}")
        urls = get_best_stream_url(stream.url, stream.platform)
        if urls:
            print(f"   Video URL: {urls['video'][:100]}...")
            if urls.get('audio'):
                print(f"   Audio URL: {urls['audio'][:100]}...")
    else:
        print("\n📺 All streamers offline — standby mode")
