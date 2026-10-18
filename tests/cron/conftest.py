"""Cron-test fixtures.

Provides a default ``PLOBI_MODEL`` for cron run_job tests so each one
doesn't have to spell out a model. The global conftest blanks
PLOBI_MODEL hermetically; without this autouse fixture every cron test
that exercises ``run_job`` would hit the fail-fast guard added in
``cron/scheduler.py`` (see issue #23979) and have to be rewritten.

Tests that specifically need ``PLOBI_MODEL`` unset — model-resolution
edge cases — call ``monkeypatch.delenv("PLOBI_MODEL", raising=False)``
inside the test, which overrides this fixture's value for that scope.

``CRON_TEST_AGENT`` does the same job for the second fail-closed guard
in ``run_job`` — the recipient gate (裁定 177): an agent round with no
owner on its record must not run. The gate itself stays live in these
tests; only the world it reads is pinned, by pointing the roster parser
(``PLOBI_PROJECTS_CONFIG``) at a real one-row roster written with the
product's own registry writer.
"""

import pytest

CRON_TEST_AGENT = "cron-test-agent"


@pytest.fixture(autouse=True)
def _default_cron_test_model(monkeypatch):
    """Pin a default PLOBI_MODEL so cron run_job tests have a resolvable model."""
    monkeypatch.setenv("PLOBI_MODEL", "test-cron-default-model")
    yield


@pytest.fixture(autouse=True)
def _default_cron_test_roster(tmp_path, monkeypatch):
    """一份真名册（一行），让收件人闸门在 cron 测试里解析得出 ``CRON_TEST_AGENT``。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    path = tmp_path / "cron-test-roster.yaml"
    entry = AgentEntry(
        name=CRON_TEST_AGENT, role="l2_test", profile=CRON_TEST_AGENT,
        display_name="测试分身",
    )
    AgentRegistry(path=path, agents={entry.name: entry}).save()
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(path))
    yield
