"""
run.py — Main entry point for the 24/7 GTA VI Stream Automation.
Starts all components: health server, chat bot, stream orchestrator.
Usage:
    python run.py              # Default mode (uses config.json setting)
    python run.py --preview    # Preview mode (encodes 30s local mp4 with all overlays & avatar speech)
    python run.py --live       # Live mode (broadcasts 24/7 to YouTube + Kick)
"""

import json
import logging
import os
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("stream.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


def load_env():
    """Load .env file if it exists."""
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def check_qr_asset():
    """Restores private QR code from Render Secret Files (/etc/secrets/) or environment variables."""
    import base64
    from pathlib import Path
    target = Path("assets/overlays/upi_qr.png")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Check Render Secret File locations
    secret_candidates = [
        Path("/etc/secrets/upi_qr.png"),
        Path("/etc/secrets/upi_qr.b64"),
        Path("upi_qr.png"),
        Path("upi_qr.b64"),
    ]
    for candidate in secret_candidates:
        if candidate.exists():
            try:
                data = candidate.read_bytes()
                if data.startswith(b"\x89PNG"):
                    target.write_bytes(data)
                    logger.info(f"✅ Loaded QR code from {candidate}")
                    return
                else:
                    decoded = base64.b64decode(data.strip())
                    if decoded.startswith(b"\x89PNG"):
                        target.write_bytes(decoded)
                        logger.info(f"✅ Decoded base64 QR code from {candidate}")
                        return
            except Exception as e:
                logger.warning(f"Error reading secret QR from {candidate}: {e}")

    # Fallback: check environment variable
    qr_b64 = os.environ.get("UPI_QR_BASE64", "").strip()
    if qr_b64:
        try:
            target.write_bytes(base64.b64decode(qr_b64))
            logger.info("✅ Restored QR code from UPI_QR_BASE64 env var")
        except Exception as e:
            logger.warning(f"Failed to decode UPI_QR_BASE64: {e}")


def main():
    load_env()
    check_qr_asset()

    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)

    # CLI flag & ENV overrides
    if "--preview" in sys.argv:
        config["preview_mode"] = True
    elif "--live" in sys.argv:
        config["preview_mode"] = False
    elif os.environ.get("PREVIEW_MODE", "").lower() in ("false", "0", "no"):
        config["preview_mode"] = False
    elif os.environ.get("PREVIEW_MODE", "").lower() in ("true", "1", "yes"):
        config["preview_mode"] = True

    preview_mode = config.get("preview_mode", True)

    logger.info("=" * 60)
    logger.info("🎮 GTA VI 24/7 Stream Automation Engine")
    logger.info(f"   Mode: {'PREVIEW (saving to preview_output.mp4)' if preview_mode else 'LIVE (YouTube + Kick 24/7)'}")
    logger.info("=" * 60)

    # 1. Start HTTP health server (keeps Render/Oracle Cloud/Railway instances awake)
    if not preview_mode:
        from health_server import start_health_server
        port = int(os.environ.get("PORT", config.get("health_server", {}).get("port", 8080)))
        start_health_server(port)
        logger.info(f"✅ Health server active on port {port}")

        def keep_alive_worker():
            render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://two4-7-stream-uul0.onrender.com")
            import urllib.request
            while True:
                time.sleep(240)
                try:
                    with urllib.request.urlopen(f"{render_url}/health", timeout=10) as r:
                        logger.debug(f"Self keep-alive ping: {r.status}")
                except Exception as e:
                    logger.debug(f"Self keep-alive error: {e}")

        threading.Thread(target=keep_alive_worker, daemon=True).start()

    # 2. Start Chat Monetization Bot
    from chat_bot import start_chat_bot
    bot = start_chat_bot(config)
    logger.info("✅ Chat monetization bot active")

    # 3. Start Stream Orchestrator
    from stream_orchestrator import main_loop
    logger.info("✅ Starting stream orchestrator loop...")
    main_loop(override_preview=preview_mode)


if __name__ == "__main__":
    main()
