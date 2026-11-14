from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.config import Settings, load_settings
from app.telegram_bot import run_telegram_listener, trigger_telegram_comment, trigger_telegram_post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("render-server")


class HealthCheckHandler(BaseHTTPRequestHandler):
    settings: Settings = None  # Populated when server starts

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/health", "/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {
                "status": "healthy",
                "service": "x-automation-telegram-bot",
                "author": self.settings.author_handle if self.settings else "@aman179102",
                "time_utc": datetime.now(timezone.utc).isoformat(),
            }
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/trigger":
            # Webhook to trigger new draft
            query = parse_qs(parsed.query)
            fmt = query.get("format", [None])[0]
            logger.info("Triggering Telegram post via HTTP request (format=%s)...", fmt)
            ok = trigger_telegram_post(self.settings, preferred_kind=fmt, force=True)
            self.send_response(200 if ok else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"status": "dispatched" if ok else "failed", "format": fmt}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif parsed.path in ("/trigger_comment", "/trigger-comment"):
            logger.info("Triggering Telegram comment recommendation via HTTP request...")
            ok = trigger_telegram_comment(self.settings)
            self.send_response(200 if ok else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"status": "dispatched" if ok else "failed", "type": "comment"}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif parsed.path in ("/trigger_hiring", "/trigger-hiring", "/job"):
            query = parse_qs(parsed.query)
            category = query.get("category", ["any_all"])[0]
            logger.info("Triggering Telegram hiring lead via HTTP request (category=%s)...", category)
            from app.hiring_radar import trigger_telegram_hiring
            ok = trigger_telegram_hiring(self.settings, category=category)
            self.send_response(200 if ok else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"status": "dispatched" if ok else "failed", "type": "hiring", "category": category}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Suppress routine health check log spam
        if args and str(args[0]).startswith("GET /health"):
            return
        logger.info("%s - %s", self.address_string(), format % args)


def keepalive_worker() -> None:
    """
    Periodically sends an external HTTP request to Render's public URL every 8 minutes
    to keep the free-tier service awake 24/7 without spinning down.
    """
    import time
    import httpx

    # Wait 60s for server to bind and DNS to settle
    time.sleep(60)
    external_url = os.getenv("RENDER_EXTERNAL_URL") or "https://x-automation-bot-lzhh.onrender.com"
    health_url = f"{external_url.rstrip('/')}/health"
    logger.info("Keepalive self-ping worker active for: %s", health_url)
    while True:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(health_url)
                logger.info("Self-keepalive ping sent to %s (Status: %d)", health_url, resp.status_code)
        except Exception as exc:
            logger.warning("Self-keepalive ping warning: %s", exc)
        time.sleep(480)  # Ping every 8 minutes (Render sleeps after 15 minutes of inactivity)


def main() -> None:
    settings = load_settings()
    port = int(os.getenv("PORT", "10000"))
    HealthCheckHandler.settings = settings

    logger.info("Starting Telegram Bot listener thread...")
    bot_thread = threading.Thread(
        target=run_telegram_listener,
        args=(settings,),
        name="TelegramBotThread",
        daemon=True,
    )
    bot_thread.start()

    logger.info("Starting Render keepalive thread...")
    keepalive_thread = threading.Thread(
        target=keepalive_worker,
        name="RenderKeepAliveThread",
        daemon=True,
    )
    keepalive_thread.start()

    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, HealthCheckHandler)
    logger.info("Render Web Service listening on 0.0.0.0:%d (Healthcheck: /health)", port)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping Render server...")
        httpd.server_close()


if __name__ == "__main__":
    main()
