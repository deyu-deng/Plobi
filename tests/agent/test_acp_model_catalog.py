"""The ACP legs' model names must come from the agent, not from a seed list.

Measured 2026-10-28: `opencode models` does not exist in opencode v2.0.32, so the profile
in `plugins/model-providers/opencode-acp/` was falling back to nine hand-written ids.
The session does publish the real catalog — `session/new` answers with `configOptions`,
whose `model` entry lists every id the agent accepts. This pins that reader and the
tool-call accounting that proves a turn really carried tools.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent.copilot_acp_client import acp_model_catalog

FAKE = str(Path(__file__).resolve().parents[2] / 'quota_hub' / 'tests' / 'fake_acp_catalog.py')


def test_a_catalog_is_read_from_the_session_even_after_a_banner():
    ids = acp_model_catalog(sys.executable, [FAKE, '--banner'], cwd=str(Path.cwd()), timeout=30)

    assert ids == ['opencode/beam-free', 'opencode/big-pickle', 'opencode/glm-5.3-flash-free']


def test_an_agent_that_never_answers_yields_no_catalog_instead_of_hanging():
    ids = acp_model_catalog(sys.executable, [FAKE, '--silent'], cwd=str(Path.cwd()), timeout=3)

    assert ids == []
