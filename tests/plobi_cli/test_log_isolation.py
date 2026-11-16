"""The suite must never write into the developer's real ``logs/`` directory.

Regression guard for 11-16: code exercised by tests calls
``plobi_logging.setup_logging(plobi_home=<real home>)`` for real, so every
WARNING emitted along a failure path (missing persona slice, broken
``sources.yaml``, unreadable quota config, …) landed in the developer's own
``~/.plobi/logs/errors.log`` — 2225 lines of pytest noise, temp paths and all.
``tests/conftest.py`` pins that directory to a session temp dir; these tests keep
the pin honest.
"""

from __future__ import annotations

import logging
from pathlib import Path

import plobi_logging

_NOISE = "plobi-test-noise-that-must-not-reach-the-real-errors-log"


def test_setup_logging_ignores_the_real_home_under_pytest() -> None:
    real_home = Path.home() / ".plobi"

    log_dir = plobi_logging.setup_logging(plobi_home=real_home, mode="cli", force=True)

    assert log_dir.resolve() != (real_home / "logs").resolve()
    assert log_dir.name == "logs"
    assert "plobi-test-logs-" in str(log_dir)


def test_no_file_handler_points_at_the_real_logs_dir() -> None:
    """Every file handler setup_logging() attaches lives in the temp dir.

    Asserted on the handler's target path rather than on file contents: the
    handlers write through an async queue, so the bytes land a moment later —
    the target path is what decides whether real logs can be polluted.
    """

    log_dir = plobi_logging.setup_logging(mode="cli", force=True).resolve()
    real_logs = (Path.home() / ".plobi" / "logs").resolve()

    targets = [
        Path(handler.baseFilename).resolve()
        for handler in plobi_logging._queued_file_handlers
        if getattr(handler, "baseFilename", None)
    ]

    assert targets, "setup_logging() attached no file handler"
    assert all(target.parent == log_dir for target in targets)
    assert all(target.parent != real_logs for target in targets)
