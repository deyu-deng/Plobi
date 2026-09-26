"""DingTalk custom-robot webhook sender.

Outbound only. Inbound confirmations arrive through the gateway's DingTalk
Stream adapter and are intercepted by the ``plobi-agenda`` plugin, so this
module stays a one-way pipe.

Signing: robots configured with 加签 reject unsigned posts, which is the most
common "it silently 310000s" failure. Set ``DINGTALK_WEBHOOK_SECRET`` and the
timestamp/sign pair is added automatically.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .base import SendOutcome

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


def _sign(secret: str, timestamp_ms: int) -> str:
    payload = f"{timestamp_ms}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest))


class DingTalkNotifier:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        secret: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.webhook_url = (webhook_url or os.environ.get("DINGTALK_WEBHOOK_URL", "")).strip()
        self.secret = (secret or os.environ.get("DINGTALK_WEBHOOK_SECRET", "")).strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    def _signed_url(self) -> str:
        if not self.secret:
            return self.webhook_url
        timestamp = int(time.time() * 1000)
        joiner = "&" if "?" in self.webhook_url else "?"
        return f"{self.webhook_url}{joiner}timestamp={timestamp}&sign={_sign(self.secret, timestamp)}"

    # Seam for tests: all network traffic goes through here.
    def _post(self, url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"errcode": -1, "errmsg": f"non-JSON response: {body[:120]}"}

    def send(self, text: str) -> SendOutcome:
        if not self.configured:
            return SendOutcome(ok=False, detail="DINGTALK_WEBHOOK_URL is not set")

        try:
            data = self._post(self._signed_url(), {"msgtype": "text", "text": {"content": text}})
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.warning("DingTalk send failed: %s", exc)
            return SendOutcome(ok=False, detail=str(exc))

        code = data.get("errcode", 0)
        if code != 0:
            message = data.get("errmsg", "unknown")
            logger.warning("DingTalk rejected the message: %s (%s)", message, code)
            return SendOutcome(ok=False, detail=f"errcode={code} {message}")

        return SendOutcome(ok=True)
