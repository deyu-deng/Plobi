"""L2 wiring (B3): route the L2 loop through the quota pool.

``ModelConfirmer`` (and any future L2 call site) currently spends against a
single ``PLOBI_L2_CHAT_URL`` endpoint. B3 makes that spend *source-aware*:

- :func:`resolve_l2_endpoint` returns the endpoint of the source the pool picks
  (cheap-first, aigw-fallback, skip-unavailable);
- :class:`QuotaAwareCompleter` is a drop-in completer that wraps a per-source
  ``send`` and does real failover — when a source errors, it is marked failed
  and the next candidate is tried, up to ``max_attempts``.

This is the "source 失效自动切换" acceptance criterion, in code rather than in
a mock. The underlying ``send`` is injectable so tests can drive the failover
without any network.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Callable, Optional

from plobi.quota.pool import QuotaPool, get_quota_pool
from plobi.quota.sources import QuotaSource

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20.0


def resolve_l2_endpoint(pool: Optional[QuotaPool] = None) -> Optional[dict]:
    """Return ``{base_url, api_key, model}`` for the source the pool picks.

    ``None`` when no source is usable (caller degrades — never hard-block).

    Takes a catalog read first (TTL-guarded, never raises) so a desktop app the
    user just connected is spendable without restarting the process.
    """
    pool = pool or get_quota_pool()
    pool.refresh_gateway()
    source = pool.resolve()
    if source is None:
        return None
    return source.endpoint


def _default_send(prompt: str, source: QuotaSource, *, timeout: float = _DEFAULT_TIMEOUT) -> Optional[str]:
    """POST ``prompt`` to ``source`` as an OpenAI-compatible completion.

    Raises on any transport/HTTP error (so the caller can fail over); returns
    ``None`` when the model returned no text (a legitimate empty answer).
    """
    if not source.base_url or not source.api_key:
        raise RuntimeError(f"source {source.name!r} has no endpoint credential")
    body = json.dumps(
        {
            "model": source.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        source.base_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {source.api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    choices = payload.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else None


class QuotaAwareCompleter:
    """A ``ModelConfirmer``-compatible completer that fails over across sources.

    Implements the ``Completer`` protocol ``(prompt, route) -> Optional[str]``.
    Each attempt resolves the current source from the pool, sends through
    ``send``, and on a raised error marks that source failed and retries the
    next candidate. ``route`` is preserved for the caller's ADR-0011 bookkeeping;
    the actual endpoint + model come from the selected source.
    """

    def __init__(
        self,
        pool: Optional[QuotaPool] = None,
        *,
        send: Optional[Callable[[str, QuotaSource], Optional[str]]] = None,
        max_attempts: int = 3,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._pool = pool
        self._send = send
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout

    def __call__(self, prompt: str, route) -> Optional[str]:
        pool = self._pool or get_quota_pool()
        send = self._send or (
            lambda p, s: _default_send(p, s, timeout=self._timeout)
        )
        pool.refresh_gateway()

        for _ in range(self._max_attempts):
            source = pool.resolve()
            if source is None:
                logger.warning("plobi quota: no usable source; leaving unresolved")
                return None
            try:
                reply = send(prompt, source)
            except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError, RuntimeError) as exc:
                logger.warning("plobi quota: source %r failed (%s); failing over", source.name, exc)
                pool.mark_failed(source.name)
                continue
            return reply

        logger.warning("plobi quota: exhausted %d source attempts", self._max_attempts)
        return None
