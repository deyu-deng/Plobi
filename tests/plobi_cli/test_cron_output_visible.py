"""Reading cron's own output files back to the surface (WP-CRON-OUTPUT-VISIBLE).

The bug these pin: a 20:00 scheduled job really ran, really failed to deliver,
and the only place that fact went was a ``.md`` nobody reads.  The desktop cron
page showed a timestamp and called it a run.  So the tests are written as
reverse evidence — a real warning-shaped document must reach the endpoint, and
must not reach it as "success".
"""

from __future__ import annotations

import time

import pytest

WARNING_DOC = (
    "# Cron Job: evening plan\n\n"
    "**Job ID:** 54a589f0ebb7\n"
    "**Run Time:** 2026-10-11T20:00:34+08:00\n"
    "**Mode:** no_agent (script)\n\n"
    '{"warning": "no notifier configured; evening plan not delivered"}\n'
)

FAILED_DOC = (
    "# Cron Job: watchdog\n\n"
    "**Job ID:** j1\n"
    "**Mode:** no_agent (script)\n"
    "**Status:** script failed\n\n"
    "Traceback: boom\n"
)

SILENT_DOC = (
    "# Cron Job: quiet\n\n**Job ID:** j2\n**Mode:** no_agent (script)\n"
    "**Status:** silent (wakeAgent=false)\n"
)

OK_DOC = "# Cron Job: fine\n\n**Job ID:** j3\n**Mode:** no_agent (script)\n\nall good\n"


@pytest.fixture()
def cron_home(tmp_path, monkeypatch):
    """An isolated default profile home, so nothing here touches the real PLOBI_HOME."""
    from plobi_cli import profiles

    home = tmp_path / "plobi-home"
    (home / "cron").mkdir(parents=True)
    (home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "_get_default_plobi_home", lambda: home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    return home


def _write(home, job_id, doc, *, when=None):
    from cron import jobs as cron_jobs

    with cron_jobs.use_cron_store(home):
        cron_jobs.save_job_output(job_id, doc)
    path = home / "cron" / "output" / job_id
    entries = sorted(path.glob("*.md"))
    assert entries, "save_job_output wrote nothing"
    if when is not None:
        import os

        for entry in entries:
            os.utime(entry, (when, when))
    return entries[-1]


def _read(home, job_id, limit=5):
    """Read inside the same store context the production route puts us in."""
    from cron import jobs as cron_jobs

    with cron_jobs.use_cron_store(home):
        return cron_jobs.list_job_outputs(job_id, limit=limit)


def test_the_four_verdicts_come_from_the_markers_the_scheduler_already_writes():
    from cron.jobs import derive_output_status

    assert derive_output_status(FAILED_DOC) == "failed"
    assert derive_output_status(SILENT_DOC) == "silent"
    assert derive_output_status(WARNING_DOC) == "warning", "the 20:00 case must not read as success"
    assert derive_output_status(OK_DOC) == "success"


def test_the_reader_returns_newest_first_with_the_file_time_and_a_status(cron_home):
    older = _write(cron_home, "j1", OK_DOC)
    time.sleep(1.1)  # the file name is second-resolution: two writes in one second collide
    newer = _write(cron_home, "j1", WARNING_DOC)

    entries = _read(cron_home, "j1", limit=5)

    assert [e["name"] for e in entries] == [newer.name, older.name]
    assert entries[0]["status"] == "warning"
    assert "no notifier configured" in entries[0]["body"]
    assert entries[0]["mtime"] >= entries[1]["mtime"]
    assert entries[0]["truncated"] is False and entries[0]["max_chars"] > 0


def test_a_long_run_is_cut_and_says_so_instead_of_looking_complete(cron_home):
    from cron.jobs import OUTPUT_SNIPPET_MAX_CHARS

    _write(cron_home, "j2", OK_DOC + "\n" + ("x" * (OUTPUT_SNIPPET_MAX_CHARS * 2)))

    entry = _read(cron_home, "j2", limit=1)[0]

    assert entry["truncated"] is True
    assert len(entry["body"]) == OUTPUT_SNIPPET_MAX_CHARS
    assert entry["chars_total"] > OUTPUT_SNIPPET_MAX_CHARS, "the ceiling must be reported, not applied in silence"


def test_known_credential_shapes_are_masked_and_the_web_url_carve_out_is_pinned(cron_home):
    """The read path uses the shared boundary, and pins what it actually guarantees.

    ``redact_sensitive_text`` deliberately does NOT mask web-URL query params or
    ``user:pass@`` userinfo (see the NOTE in agent/redact.py: magic links, OAuth
    callbacks and pre-signed URLs are legitimate round-trip tokens, and masking
    them by name breaks skills mid-flow).  Known credential shapes — ``sk-``,
    ``ghp_``, JWTs — and the bare-token userinfo form ARE still masked.  This test
    pins both halves: if the shared module ever changes its mind, we find out here
    instead of silently shipping a surface that disagrees with the logs.
    """
    token = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAbogus"
    leaky = WARNING_DOC + chr(10) + (
        "DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=abcdef0123456789"
        + chr(10)
        + f"key: {token}"
        + chr(10)
    )
    _write(cron_home, "j3", leaky)

    body = _read(cron_home, "j3", limit=1)[0]["body"]

    assert token not in body, "a vendor-prefixed token must never reach the surface"
    assert "ghp_AA...ogus" in body, f"the mask should be visible, not a silent deletion: {body[-90:]!r}"
    # The deliberate carve-out, asserted so nobody "fixes" it here by mistake:
    assert "access_token=abcdef0123456789" in body, "web-URL query params pass through by design (agent/redact.py NOTE)"


def test_the_endpoint_carries_the_warning_shape_end_to_end(cron_home, monkeypatch):
    """Route level: the surface must be able to see the 20:00 failure, not just its timestamp."""
    from plobi_cli import web_server

    _write(cron_home, "job-evening", WARNING_DOC)
    monkeypatch.setattr(web_server, "_find_cron_job_profile", lambda job_id: "default")

    payload = web_server._list_cron_job_outputs_sync("job-evening", None, 3)

    assert payload, "the endpoint returned nothing for a job that definitely ran"
    assert payload[0]["status"] == "warning"
    assert "no notifier configured" in payload[0]["body"]


def test_a_job_with_no_output_files_reports_nothing_rather_than_inventing_success(cron_home, monkeypatch):
    from plobi_cli import web_server

    monkeypatch.setattr(web_server, "_find_cron_job_profile", lambda job_id: "default")

    assert web_server._list_cron_job_outputs_sync("never-ran", None, 3) == []


def test_a_crafted_job_id_cannot_escape_the_output_directory(cron_home):
    for bad in ("..", "../..", "a/b", "C:\\Windows", ""):
        assert _read(cron_home, bad, limit=3) == [], f"{bad!r} must be rejected, not resolved"
