from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import string
import time
from urllib.parse import parse_qsl, quote, urlsplit

import httpx


def percent_encode(val: str | bytes) -> str:
    """RFC 5849 percent encoding."""
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    return quote(str(val), safe="~-._")


def generate_nonce(length: int = 32) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


class OAuth1Auth(httpx.Auth):
    """
    RFC 5849 OAuth 1.0a User Context authentication for httpx.
    Signs requests with HMAC-SHA1. Works with JSON bodies, query params, and multipart uploads.
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        access_token: str,
        access_token_secret: str,
    ) -> None:
        self.consumer_key = consumer_key.strip()
        self.consumer_secret = consumer_secret.strip()
        self.access_token = access_token.strip()
        self.access_token_secret = access_token_secret.strip()

    def auth_flow(self, request: httpx.Request):
        auth_header = self._build_header(request)
        request.headers["Authorization"] = auth_header
        yield request

    def _build_header(self, request: httpx.Request) -> str:
        nonce = generate_nonce()
        timestamp = str(int(time.time()))

        oauth_params: dict[str, str] = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": timestamp,
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }

        # Collect params for signature
        split_url = urlsplit(str(request.url))
        base_url = f"{split_url.scheme}://{split_url.netloc}{split_url.path}"

        sig_params: list[tuple[str, str]] = []
        for k, v in oauth_params.items():
            sig_params.append((percent_encode(k), percent_encode(v)))

        # Add query parameters
        if split_url.query:
            for k, v in parse_qsl(split_url.query, keep_blank_values=True):
                sig_params.append((percent_encode(k), percent_encode(v)))

        # Form urlencoded body parameters (if applicable)
        content_type = request.headers.get("Content-Type", "")
        if "application/x-www-form-urlencoded" in content_type:
            try:
                body_str = request.content.decode("utf-8")
                for k, v in parse_qsl(body_str, keep_blank_values=True):
                    sig_params.append((percent_encode(k), percent_encode(v)))
            except Exception:
                pass

        # Sort parameters lexicographically by key, then value
        sig_params.sort(key=lambda item: (item[0], item[1]))
        normalized_params = "&".join(f"{k}={v}" for k, v in sig_params)

        method = request.method.upper()
        base_string = f"{percent_encode(method)}&{percent_encode(base_url)}&{percent_encode(normalized_params)}"

        signing_key = f"{percent_encode(self.consumer_secret)}&{percent_encode(self.access_token_secret)}"
        hashed = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        )
        signature = base64.b64encode(hashed.digest()).decode("utf-8")
        oauth_params["oauth_signature"] = signature

        # Format Authorization header string
        header_parts = [
            f'{percent_encode(k)}="{percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        ]
        return "OAuth " + ", ".join(header_parts)
