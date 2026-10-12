"""WP-COST-READOUT-CLI — the one-line spend readout for the CLI's shell exits.

What this file pins down is the honesty rule, not the typography: an absence of
pricing must read as ``unknown``, a subscription-covered turn must read as
covered (and may show 0), and a run that touched nothing prints nothing.
"""

import io

from plobi_cli.cost_readout import (
    cost_line,
    emit_cost_line,
    epilogue_cost_lines,
)


def _row(**over):
    row = {
        "input_tokens": 83345,
        "output_tokens": 2972,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "api_calls": 8,
        "model": "glm-5.3-flash",
        "estimated_cost_usd": 0.01664887,
        "cost_status": "estimated",
        "cost_source": "official_docs_snapshot",
    }
    row.update(over)
    return row


class TestPricedRuns:
    def test_estimated_run_shows_money_status_and_source(self):
        line = cost_line(_row())
        assert line is not None
        assert line.startswith("$0.0166 (estimated · official_docs_snapshot)")
        assert "83.3K in" in line and "2.97K out" in line and "8 calls" in line
        assert "glm-5.3-flash" in line

    def test_actual_status_is_not_softened_into_estimate(self):
        assert "(actual ·" in cost_line(_row(cost_status="actual", cost_source="vendor_invoice"))

    def test_subscription_covered_turn_may_show_zero_without_lying(self):
        # The whole reason the number alone cannot be trusted: 0.0 here means
        # "covered by the plan", which is a different claim from "no price".
        line = cost_line(_row(estimated_cost_usd=0.0, cost_status="included", cost_source=None))
        assert line.startswith("$0.0000 (included in plan)")
        assert "unknown" not in line

    def test_dollars_and_cents_are_both_kept(self):
        assert cost_line(_row(estimated_cost_usd=1234.5)).startswith("$1,234.50 (estimated")


class TestUnpricedRuns:
    def test_unknown_pricing_never_prints_a_number(self):
        line = cost_line(_row(estimated_cost_usd=0.0, cost_status=None, cost_source=None))
        assert "unknown (no price on file)" in line
        assert "$0" not in line
        # usage still visible, so the reader knows tokens were really spent
        assert "83.3K in" in line

    def test_status_the_pricer_does_not_know_is_treated_as_unpriced(self):
        assert "priced partially" in cost_line(_row(cost_status="some_future_status"))

    def test_money_without_tokens_folds_to_partial_not_none(self):
        row = _row(
            input_tokens=0,
            output_tokens=0,
            api_calls=0,
            estimated_cost_usd=0.02,
            cost_status=None,
            cost_source=None,
        )
        assert "priced partially" in cost_line(row)


class TestNothingToSay:
    def test_empty_row_prints_nothing(self):
        assert cost_line({}) is None
        assert cost_line(None) is None

    def test_zero_usage_and_zero_money_prints_nothing(self):
        assert cost_line(_row(input_tokens=0, output_tokens=0, api_calls=0,
                              estimated_cost_usd=0.0, cost_status=None)) is None

    def test_missing_and_none_values_do_not_raise(self):
        assert cost_line({"estimated_cost_usd": None, "cost_status": None}) is None
        assert cost_line({"input_tokens": "many", "api_calls": None,
                          "estimated_cost_usd": 0.01}) .startswith("$0.0100")

    def test_booleans_are_not_token_counts(self):
        line = cost_line(_row(input_tokens=True, api_calls=False,
                              estimated_cost_usd=0.0, cost_status=None))
        assert "unknown" in line
        assert "0 in" in line          # True was ignored, not read as 1
        assert "calls" not in line     # False was ignored, not read as 0 calls


class TestExitSummaryShape:
    def test_cost_line_aligns_with_the_tokens_column(self):
        lines = epilogue_cost_lines(_row())
        assert len(lines) == 1
        assert lines[0].startswith("Cost:")
        assert lines[0].index("$0.0166") == 16

    def test_unpriced_session_still_gets_a_cost_line(self):
        lines = epilogue_cost_lines(_row(cost_status=None, estimated_cost_usd=0.0))
        assert lines and "unknown" in lines[0]

    def test_session_without_usage_gets_no_line(self):
        assert epilogue_cost_lines(_row(input_tokens=0, output_tokens=0, api_calls=0,
                                        estimated_cost_usd=0.0, cost_status=None)) == []


class TestStreamContract:
    def test_emit_writes_exactly_one_line_to_the_given_stream(self):
        err = io.StringIO()
        assert emit_cost_line(_row(), err) is True
        out = err.getvalue()
        assert out.startswith("plobi -z: $0.0166")
        assert out.count("\n") == 1

    def test_emit_is_quiet_when_there_is_nothing_to_say(self):
        err = io.StringIO()
        assert emit_cost_line({}, err) is False
        assert err.getvalue() == ""

    def test_a_broken_stream_changes_nothing(self):
        class Boom:
            def write(self, *_):
                raise OSError("terminal gone")

            def flush(self):
                raise OSError("terminal gone")

        assert emit_cost_line(_row(), Boom()) is False

    def test_prefix_is_overridable_for_other_surfaces(self):
        err = io.StringIO()
        emit_cost_line(_row(), err, prefix="plobi ask:")
        assert err.getvalue().startswith("plobi ask: $0.0166")
