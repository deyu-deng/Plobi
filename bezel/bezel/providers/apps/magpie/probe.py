"""Ask magpie's own gateway whether a published model can actually serve a turn.

The roster is not evidence. magpie lists every id its vendor adapters know about —
including ids the current account may no longer use. Measured 2026-11-24: Devin
listed 24 rows and *every one* answered ``502 "This model is no longer available for
your account"``, while the catalogue looked exactly like the healthy vendors down to
the ``tool_call: true`` flag. A picker built on that catalogue offers models that
cannot answer, which is the one thing it must never do.

So the lamp comes from a turn, not from a listing, and it is *paid for once*: a
permission verdict is a property of the seat, not of the id, so one representative id
per vendor settles the whole vendor in a single request.
"""

from __future__ import annotations

import logging

from .... import model_health

log = logging.getLogger("bezel")

#: Devin needs ~36 s to answer at all (measured 2026-11-24: 502 after 35.8 s), so a
#: short deadline would report "unreachable" for a vendor that is merely slow to refuse.
DEFAULT_PROBE_TIMEOUT = 45.0

#: Cheap, plain, and tool-less: this probe measures *permission to serve*, not quality.
PROBE_TEXT = "ping"

#: Which statuses mean "the seat said no" rather than "that id hiccuped".
_SEAT_STATUSES = (401, 403, 404)


async def _ask(provider, model: str, timeout: float) -> tuple[int, str]:
    """One minimal turn against the gateway. Returns (status, body-text)."""
    base = provider._base_url()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_TEXT}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        res = await provider.http.post(
            f"{base}/v1/chat/completions",
            json=payload,
            headers=provider._headers(),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - an unreachable gateway is not a verdict
        return 0, f"{type(exc).__name__}: {exc}"
    return res.status_code, res.text or ""


async def probe_vendor(
    provider, model_ids: list[str], *, timeout: float = DEFAULT_PROBE_TIMEOUT, max_fails: int = 3
) -> dict:
    """Settle one vendor with as few turns as it takes.

    Returns ``{model_id: (ok, status, detail)}``. A seat-level refusal settles every
    sibling at once; a single success settles the representative only. So does a run of
    refusals: three siblings that all say no are describing the seat, not three
    unfortunate ids, and paying for the other twenty-one would buy nothing.
    """
    verdicts: dict[str, tuple[bool, int, str]] = {}
    if not model_ids:
        return verdicts

    def _settle_rest(
        status: int, message: str, detail: str, why: str, *, unproven: bool = False
    ) -> None:
        for other in model_ids:
            if other in verdicts:
                continue
            if unproven:
                model_health.record_unproven(other, detail or message)
            else:
                model_health.record_refusal(other, status, message)
            verdicts[other] = (False, status, detail)
        log.warning(
            "magpie probe: vendor %s settles %d models as refused (%s, %s) %s",
            model_ids[0].split("/", 1)[0],
            len(model_ids),
            status,
            why,
            detail[:160],
        )

    ordered = sorted(model_ids, key=lambda m: (len(m), m))
    fails = 0
    for model in ordered:
        status, text = await _ask(provider, model, timeout)
        if status == 200:
            verdicts[model] = (True, status, "")
            model_health.record_success(model)
            log.info("magpie probe: %s serves (200)", model)
            break
        detail = provider._upstream_detail(text) or text[:200]
        err = provider._classify_upstream(status, {"model": model}, text)
        message = str(err) if err is not None else detail
        # Judge on the upstream's own words, not on our re-wrapped summary of them:
        # "no longer available for your account" is the sentence that decides, and it is
        # the sentence that has to survive into the dashboard.
        verdict_text = detail or message
        if status and model_health.looks_like_unknown_model(status, verdict_text):
            model_health.record_refusal(model, status, verdict_text)
            why = "refused"
        else:
            # No answer at all, or the gateway's own trouble. That is not a verdict about
            # this model, so it must never start a cooling clock — but it is not proof
            # either, and the lamp stays off until something actually answers.
            model_health.record_unproven(model, detail or f"http {status}")
            why = "unanswered"
        verdicts[model] = (False, status, detail)
        log.info("magpie probe: %s %s (%s) %s", model, why, status, detail[:120])
        fails += 1
        if status in _SEAT_STATUSES or model_health.seat_refusal(status, verdict_text):
            # The account cannot use this vendor at all.
            _settle_rest(status, verdict_text, detail, "seat")
            break
        if fails >= max_fails:
            _settle_rest(status, message, detail, f"{fails} siblings {why}", unproven=(why != "refused"))
            break
    return verdicts


async def probe_roster(provider, *, timeout: float = DEFAULT_PROBE_TIMEOUT) -> dict:
    """Probe every vendor magpie publishes, one representative each."""
    rows = await provider.read_catalogue()
    if not rows:
        return {}
    groups: dict[str, list[str]] = {}
    for mid in rows:
        groups.setdefault(mid.split("/", 1)[0], []).append(mid)
    all_verdicts: dict[str, tuple[bool, int, str]] = {}
    for vendor, ids in sorted(groups.items()):
        try:
            all_verdicts.update(await probe_vendor(provider, ids, timeout=timeout))
        except Exception:  # noqa: BLE001 - one vendor must not stop the sweep
            log.warning("magpie probe: %s failed", vendor, exc_info=True)
    return all_verdicts
