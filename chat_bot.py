"""
Chat Monetization Bot — Sends periodic donation/superchat/UPI reminders
to YouTube Live Chat and Kick Chat.
(Standalone daemon — runs alongside the stream orchestrator)
"""

import json
import logging
import random
import time
import threading

logger = logging.getLogger("chat_bot")


class ChatBot:
    """Sends periodic monetization reminders to live chat."""

    def __init__(self, config: dict):
        self.config = config
        self.chat_config = config.get("chat_bot", {})
        self.enabled = self.chat_config.get("enabled", True)
        self.interval = self.chat_config.get("reminder_interval_seconds", 300)
        self.messages = self.chat_config.get("messages", [])
        self.running = False

    def get_random_message(self) -> str:
        """Get a random monetization reminder message."""
        upi_id = os.environ.get("UPI_ID", "").strip() or self.config.get("upi_id", "").strip()
        if not self.messages:
            upi_text = f" UPI: {upi_id}" if upi_id else ""
            return f"💰 Support the stream!{upi_text} Scan the QR code or send a Superchat!"
        msg = random.choice(self.messages)
        if upi_id:
            msg = msg.replace("{upi_id}", upi_id)
        return msg

    def send_to_youtube_chat(self, message: str):
        """
        Send message to YouTube Live Chat.
        NOTE: Requires YouTube Data API v3 OAuth credentials.
        For now, logs the message — full API integration needs OAuth setup.
        """
        logger.info(f"[YouTube Chat] {message}")
        # TODO: Integrate YouTube Data API v3 liveChatMessages.insert
        # Requires: OAuth2 credentials + live broadcast ID
        # Will be enabled once YouTube stream key is configured

    def send_to_kick_chat(self, message: str):
        """
        Send message to Kick Chat.
        NOTE: Kick API is still evolving. 
        For now, logs the message.
        """
        logger.info(f"[Kick Chat] {message}")
        # TODO: Integrate Kick chat API when available
        # Alternative: Use a browser automation approach

    def broadcast_message(self, message: str):
        """Send a message to all configured platforms."""
        self.send_to_youtube_chat(message)
        self.send_to_kick_chat(message)

    def run(self):
        """Main chat bot loop — sends periodic reminders."""
        if not self.enabled:
            logger.info("Chat bot disabled in config")
            return

        self.running = True
        logger.info(f"💬 Chat bot started (interval: {self.interval}s)")
        logger.info(f"   Loaded {len(self.messages)} reminder messages")

        # Initial delay before first message
        time.sleep(60)

        while self.running:
            try:
                message = self.get_random_message()
                self.broadcast_message(message)
                time.sleep(self.interval)
            except Exception as e:
                logger.error(f"Chat bot error: {e}")
                time.sleep(30)

    def stop(self):
        self.running = False


def start_chat_bot(config: dict) -> ChatBot:
    """Start the chat bot in a background thread."""
    bot = ChatBot(config)
    thread = threading.Thread(target=bot.run, daemon=True)
    thread.start()
    return bot


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )
    with open("config.json") as f:
        config = json.load(f)

    bot = ChatBot(config)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
        print("\n👋 Chat bot stopped")
