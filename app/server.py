from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.config import Settings, load_settings
from app.telegram_bot import (
    process_telegram_update,
    setup_telegram_webhook,
    trigger_telegram_comment,
    trigger_telegram_post,
)

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
        elif parsed.path in ("/webhook", "/telegram_webhook"):
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

            if post_data:
                try:
                    update_data = json.loads(post_data.decode("utf-8"))
                    threading.Thread(
                        target=process_telegram_update,
                        args=(update_data, self.settings),
                        daemon=True,
                    ).start()
                except Exception as exc:
                    logger.warning("Error processing webhook update: %s", exc)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Suppress routine health check log spam
        if args and str(args[0]).startswith("GET /health"):
            return
        logger.info("%s - %s", self.address_string(), format % args)


def main() -> None:
    settings = load_settings()
    port = int(os.getenv("PORT", "10000"))
    HealthCheckHandler.settings = settings

    external_url = os.getenv("RENDER_EXTERNAL_URL") or "https://x-automation-bot-lzhh.onrender.com"
    webhook_url = f"{external_url.rstrip('/')}/webhook"
    logger.info("Registering on-demand Telegram Webhook at: %s", webhook_url)
    setup_telegram_webhook(settings, webhook_url)

    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, HealthCheckHandler)
    logger.info("Render Webhook Server listening on 0.0.0.0:%d (Webhook: /webhook)", port)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping Render server...")
        httpd.server_close()
    except KeyboardInterrupt:
        logger.info("Stopping Render server...")
        httpd.server_close()


if __name__ == "__main__":
    main()
