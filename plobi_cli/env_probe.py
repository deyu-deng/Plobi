"""Live probes for every credential the settings pages can test.

One registry, two doors. The Tools sub-page holds credentials for *tool
backends* — a translation engine, a self-hosted endpoint — whose call shape is
nothing like a models read: DeepL wants a POSTed form plus
``Authorization: DeepL-Auth-Key``, LibreTranslate a GET with the text in ``q``.
The Providers page holds model API keys, which are checked with an
OpenAI-shaped ``/v1/models`` read. Both shapes live here, keyed by env var, so
adding a credential means editing one table instead of remembering which of two
probe implementations to copy.

The two doors differ only in URL and bookkeeping:

* ``POST /api/env/probe`` — generic, used by Tools & Keys; returns
  ``supported`` so the SPA knows whether a row earns a "Test" button.
* ``POST /api/providers/validate`` — a thin alias kept for the provider wizard;
  same registry, same result contract.

Result contract (identical on both doors, so the client reads them the same way):

* ``ok=True`` — the service accepted the credential.
* ``ok=False, reachable=True`` — the credential is wrong; ``message`` says why.
* ``reachable=False`` — the probe itself could not run (offline, endpoint
  down, no scheme). Never present this as "your key is bad".

Nothing here ever logs the credential value: a probe failure logs the exception
class only, and the caller passes the value straight through.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

DEEPL_KEY_ENV = "PLOBI_TRANSLATE_DEEPL_KEY"
LOCAL_URL_ENV = "PLOBI_TRANSLATE_LOCAL_URL"

# One short Chinese phrase: every engine translates it, and the reply makes it
# obvious whether a real translation happened or we got an error page back.
_PROBE_TEXT = "你好"

# 10s: DeepL is usually sub-second; a self-hosted endpoint on a cold start can
# take a few seconds. Beyond that the "Test" button stops feeling like a test.
_TIMEOUT = 10.0

_UA = "Plobi/1.0 (+settings)"


def _result(
    *, ok: bool, reachable: bool, message: str = "", sample: str = ""
) -> Dict[str, Any]:
    return {"ok": ok, "reachable": reachable, "message": message, "sample": sample}


def is_probeable(key: str) -> bool:
    """True when this env var has a live check the SPA can offer.

    Drives the ``probe`` flag on ``GET /api/env`` rows — the client shows a Test
    button only for these, so a row can never advertise a check that does not
    exist.
    """
    return key in _PROBES


def probe_env_value(key: str, value: str, api_key: str = "") -> Dict[str, Any]:
    """Run the probe registered for ``key`` against an explicit ``value``.

    The value is passed in rather than read from ``os.environ`` on purpose: the
    user must be able to test a key they typed but have not saved yet.

    ``api_key`` is only read by probes that need a second secret to reach the
    service (a custom ``OPENAI_BASE_URL`` behind auth); the rest ignore it.
    """
    probe = _PROBES.get(key)

    if probe is None:
        return _result(ok=False, reachable=False)

    return probe(value, api_key=api_key)


# --------------------------------------------------------------------------- #
# DeepL
# --------------------------------------------------------------------------- #
def _deepl_sample(resp: Any) -> str:
    try:
        items = resp.json().get("translations") or []
        text = items[0].get("text") if items and isinstance(items[0], dict) else ""
    except Exception:
        return ""

    return text.strip() if isinstance(text, str) else ""


def _probe_deepl(value: str, api_key: str = "") -> Dict[str, Any]:
    import httpx

    # Same host-picking rule the real translation uses (free keys end in ":fx",
    # only api-free accepts them). Probing a different host than the one naming
    # will call is how you get "test passed, every name came back empty".
    from plobi.agents.naming import deepl_endpoint

    url = deepl_endpoint(value)

    try:
        with httpx.Client(timeout=httpx.Timeout(_TIMEOUT)) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"DeepL-Auth-Key {value}",
                    "User-Agent": _UA,
                },
                data={
                    "text": _PROBE_TEXT,
                    "source_lang": "ZH",
                    "target_lang": "EN-US",
                },
            )
    except Exception as exc:
        logger.info("env probe: DeepL unreachable (%s)", type(exc).__name__)

        return _result(
            ok=False,
            reachable=False,
            message="Could not reach DeepL. Check the network or proxy and try again.",
        )

    # DeepL answers 403 (not 401) for a key it won't accept.
    if resp.status_code in (401, 403):
        return _result(
            ok=False,
            reachable=True,
            message=(
                'DeepL rejected this key. Check for a typo — a free key ends in ":fx".'
            ),
        )
    # 456 = quota exhausted: the key is fine, the allowance is not.
    if resp.status_code == 456:
        return _result(
            ok=False,
            reachable=True,
            message=(
                "This key is valid, but its character quota is used up for this "
                "period."
            ),
        )
    if resp.status_code == 429:
        return _result(
            ok=True,
            reachable=True,
            message="DeepL rate-limited the test, but the key is accepted.",
        )
    if not resp.is_success:
        return _result(
            ok=False,
            reachable=True,
            message=f"DeepL returned HTTP {resp.status_code} for this key.",
        )

    sample = _deepl_sample(resp)

    if not sample:
        return _result(
            ok=False,
            reachable=True,
            message="DeepL accepted the key but sent back no translation.",
        )

    return _result(ok=True, reachable=True, sample=sample)


# --------------------------------------------------------------------------- #
# Self-hosted translation endpoint
# --------------------------------------------------------------------------- #
def _local_failure_message(exc: Exception, base: str) -> str:
    """Turn the httpx exception into something a user can act on."""
    name = type(exc).__name__

    if "Connect" in name:
        return (
            "Nothing is listening at that address — start the translation "
            "service and try again."
        )
    if "Timeout" in name:
        return (
            "The endpoint timed out. It may still be starting up, or the "
            "address may be wrong."
        )

    return f"Could not reach {base}. Check the address and that the service is running."


def _probe_local(value: str, api_key: str = "") -> Dict[str, Any]:
    import httpx

    from plobi.agents.naming import local_body_text

    base = value.strip().rstrip("/")

    # A host:port with no scheme is the single most common typo here, and httpx
    # reports it as an unsupported protocol — not something the user can map
    # back to "I forgot http://".
    if not re.match(r"^https?://", base, re.I):
        return _result(
            ok=False,
            reachable=False,
            message="The URL needs a scheme — start it with http:// or https://.",
        )

    # Both parameter spellings, exactly like naming.translate_local: a self-hosted
    # LibreTranslate reads source/target, Google-shaped endpoints read sl/tl.
    # Sending both keeps the test honest about what the real call will do.
    sep = "&" if "?" in base else "?"
    url = base + sep + urllib.parse.urlencode(
        {"q": _PROBE_TEXT, "sl": "auto", "tl": "en", "source": "auto", "target": "en"}
    )

    try:
        with httpx.Client(timeout=httpx.Timeout(_TIMEOUT)) as client:
            resp = client.get(url, headers={"User-Agent": _UA})
    except Exception as exc:
        logger.info("env probe: local endpoint unreachable (%s)", type(exc).__name__)

        return _result(
            ok=False,
            reachable=False,
            message=_local_failure_message(exc, base),
        )

    if not resp.is_success:
        return _result(
            ok=False,
            reachable=True,
            message=(
                f"The endpoint answered HTTP {resp.status_code} — check that the "
                "path (e.g. /translate) is right."
            ),
        )

    sample = local_body_text(resp.text)

    if not sample:
        return _result(
            ok=False,
            reachable=True,
            message="The endpoint answered, but its reply held no translation.",
        )

    return _result(ok=True, reachable=True, sample=sample)


_PROBES: Dict[str, Callable[..., Dict[str, Any]]] = {
    DEEPL_KEY_ENV: _probe_deepl,
    LOCAL_URL_ENV: _probe_local,
}


# --------------------------------------------------------------------------- #
# Model-provider credentials
# --------------------------------------------------------------------------- #
# Deliberately the same table as the tool credentials above: one registry, so
# adding a testable credential is one edit here instead of a second probe
# implementation living in web_server. Each entry is (url, auth) where auth is
# "bearer" (Authorization header) or "query" (?key=) — a cheap read-only call
# that 401s on a bad token.
BASE_URL_ENV = "OPENAI_BASE_URL"

_PROVIDER_PROBES: Dict[str, tuple] = {
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1/key", "bearer"),
    "OPENAI_API_KEY": ("https://api.openai.com/v1/models", "bearer"),
    "XAI_API_KEY": ("https://api.x.ai/v1/models", "bearer"),
    "GEMINI_API_KEY": (
        "https://generativelanguage.googleapis.com/v1beta/models",
        "query",
    ),
}


def _parse_model_ids(resp: Any) -> List[str]:
    """Model ids from an OpenAI-compatible ``/models`` reply; [] for anything else."""
    try:
        if not resp.is_success: return []
        payload = resp.json()
    except Exception: return []
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list): return []
    ids: List[str] = []
    for item in data:
        mid = str((item.get("id") if isinstance(item, dict) else item) or "").strip()
        if mid: ids.append(mid)
    return ids


def _probe_models_endpoint(url: str, auth: str) -> Callable[..., Dict[str, Any]]:
    """Build the probe for one provider key.

    401/403 is the only honest "bad key" answer; 429 means the key is real but
    throttled, so it must not block the user from saving it.
    """

    def probe(value: str, api_key: str = "") -> Dict[str, Any]:
        import httpx
        headers = {"Accept": "application/json"}
        params: Dict[str, str] = {}
        if auth == "bearer": headers["Authorization"] = f"Bearer {value}"
        else: params["key"] = value
        try:
            with httpx.Client(timeout=httpx.Timeout(_TIMEOUT)) as client:
                resp = client.get(url, headers=headers, params=params)
        except Exception as exc:
            logger.info("env probe: provider unreachable (%s)", type(exc).__name__)
            return _result(ok=False, reachable=False, message="Could not reach the provider to verify the key.")
        if resp.status_code in (401, 403):
            return _result(ok=False, reachable=True, message="That API key was rejected. Double-check it and try again.")
        if resp.status_code == 429 or resp.is_success:
            return _result(ok=True, reachable=True)
        return _result(ok=False, reachable=True, message=f"Provider returned HTTP {resp.status_code} for this key.")

    return probe


def _probe_base_url(value: str, api_key: str = "") -> Dict[str, Any]:
    """Custom/local endpoint: any HTTP answer (even 401) proves it is up, not that auth works.

    ``api_key`` is forwarded because many self-hosted servers gate ``/models``
    behind auth; with it the reply also tells us which models it serves, which
    the provider wizard uses to pick a default.
    """
    import httpx
    url = value.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    try:
        with httpx.Client(timeout=httpx.Timeout(_TIMEOUT)) as client:
            resp = client.get(url, headers=headers)
    except Exception as exc:
        logger.info("env probe: custom endpoint unreachable (%s)", type(exc).__name__)
        return _result(ok=False, reachable=False, message=f"Could not reach {url}.")
    result = _result(ok=True, reachable=True)
    result["models"] = _parse_model_ids(resp)
    return result


_PROBES.update({key: _probe_models_endpoint(url, auth) for key, (url, auth) in _PROVIDER_PROBES.items()})
_PROBES[BASE_URL_ENV] = _probe_base_url
