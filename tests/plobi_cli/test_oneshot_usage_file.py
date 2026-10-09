"""Tests for plobi -z --usage-file (per-run JSON usage report)."""

import json

from plobi_cli.oneshot import _write_usage_file


def _result(**overrides):
    base = {
        "estimated_cost_usd": 0.1234,
        "cost_status": "estimated",
        "cost_source": "pricing-table",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 800,
        "cache_write_tokens": 0,
        "reasoning_tokens": 50,
        "total_tokens": 1250,
        "api_calls": 3,
        "model": "openai/gpt-5.5",
        "provider": "openrouter",
        "session_id": "abc123",
        "completed": True,
        "failed": False,
    }
    base.update(overrides)
    return base


class TestWriteUsageFile:
    def test_writes_report_with_cost_and_tokens(self, tmp_path):
        path = tmp_path / "usage.json"
        _write_usage_file(str(path), _result())
        report = json.loads(path.read_text())
        assert report["estimated_cost_usd"] == 0.1234
        assert report["input_tokens"] == 1000
        assert report["output_tokens"] == 200
        assert report["model"] == "openai/gpt-5.5"
        assert report["api_calls"] == 3
        assert report["failed"] is False
        assert "failure" not in report

    def test_none_path_is_noop(self, tmp_path):
        # Must not raise and must not create a report file.
        _write_usage_file(None, _result())
        assert not (tmp_path / "usage.json").exists()

    def test_failure_marks_failed_and_records_message(self, tmp_path):
        path = tmp_path / "usage.json"
        _write_usage_file(str(path), {}, failure="boom")
        report = json.loads(path.read_text())
        assert report["failed"] is True
        assert report["failure"] == "boom"
        # Missing result fields serialize as null, not KeyError.
        assert report["estimated_cost_usd"] is None

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "usage.json"
        _write_usage_file(str(path), _result())
        assert json.loads(path.read_text())["total_tokens"] == 1250

    def test_unwritable_path_never_raises(self, tmp_path):
        # The parent chain must be genuinely impossible on BOTH platforms. The old
        # literal here was "/proc/definitely/not/writable/usage.json", which on
        # Windows is not a POSIX path at all — Python resolved it drive-relative
        # against the cwd's volume and *created* D:\proc\...\usage.json (410 B of
        # real billing JSON, found on 2026-10-08). So the test was asserting that a
        # write it actually performed had "failed" — 裁定 79.
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        doomed = str(blocker / "nested" / "usage.json")

        _write_usage_file(doomed, _result())  # must swallow, not raise

        assert not (blocker / "nested").exists(), "nothing may be created on the way down"

    def test_result_failed_flag_carries_through(self, tmp_path):
        path = tmp_path / "usage.json"
        _write_usage_file(str(path), _result(failed=True))
        assert json.loads(path.read_text())["failed"] is True
