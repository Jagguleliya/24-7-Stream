"""
Health Server — Lightweight HTTP endpoint for cloud hosting keep-alive.
Render/Railway/Fly.io free tiers require an HTTP listener to stay awake.
Binds to $PORT when running in the cloud. Ping with UptimeRobot or Cron-Job.org to prevent sleep.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from flask import Flask, jsonify

logger = logging.getLogger("health_server")
app = Flask(__name__)

start_time = time.time()


@app.route("/")
def index():
    return jsonify({
        "service": "GTA VI 24/7 Stream Engine",
        "status": "online",
        "uptime_seconds": int(time.time() - start_time),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "healthy": True}), 200


@app.route("/debug_status")
def debug_status():
    yt_key = os.environ.get("YOUTUBE_STREAM_KEY", "").strip()
    kick_key = os.environ.get("KICK_STREAM_KEY", "").strip()
    return jsonify({
        "youtube_key_configured": bool(yt_key),
        "kick_key_configured": bool(kick_key),
        "youtube_key_length": len(yt_key),
        "kick_key_length": len(kick_key),
        "preview_mode": os.environ.get("PREVIEW_MODE", "false"),
        "force_encoder": os.environ.get("FORCE_ENCODER", ""),
        "uptime_seconds": int(time.time() - start_time),
    })


@app.route("/status")
def status():
    try:
        with open("config.json") as f:
            config = json.load(f)
        return jsonify({
            "status": "running",
            "preview_mode": config.get("preview_mode", True),
            "channels_monitored": len(config.get("priority_channels", [])),
            "encoder_settings": config.get("stream_settings", {}),
            "uptime_seconds": int(time.time() - start_time),
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


def start_health_server(port: int = 8080):
    """Start the health server in a background thread."""
    import threading
    effective_port = int(os.environ.get("PORT", port))
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=effective_port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    logger.info(f"🏥 Health server active on port {effective_port}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with open("config.json") as f:
        config = json.load(f)
    cfg_port = config.get("health_server", {}).get("port", 8080)
    port = int(os.environ.get("PORT", cfg_port))
    print(f"🏥 Health server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
