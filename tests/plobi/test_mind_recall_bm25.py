"""BM25 ranker + invalidation semantics for Mind recall (WP-MIND-RECALL-FOUNDATION).

The existing ``test_mind_recall_zones_and_tiers.py`` keeps owning the zone and
tier contracts; this file only pins what this knife adds: deterministic sparse
ranking (tf saturation, length normalisation, IDF) and "a dead page never
enters context". No model, no embedding, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.mind import recall_bm25, retrieval

_HAY = "光储"


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "concepts").mkdir(parents=True)
    (surface / "notes").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    return mind, surface


def _page(path: Path, hits: int, *, extra: str = "", frontmatter: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [f"{_HAY}第 {i} 条：容量 {i} MWh。" for i in range(hits)]
        + ([extra * 40] if extra else [])
    )
    fm = frontmatter or f"title: {path.stem}"
    path.write_text(f"---\n{fm}\n---\n\n# {path.stem}\n\n{body}\n", encoding="utf-8")


def test_bm25_is_deterministic_and_tf_saturates():
    tf = [{"光储": 1}, {"光储": 20}]
    first = recall_bm25.bm25(tf, [100, 100])
    assert first == recall_bm25.bm25(tf, [100, 100]), "same input must be byte-identical"
    assert all(score > 0 for score in first)
    assert first[1] > first[0], "more hits still ranks higher"
    assert first[1] < first[0] * 20, "raw term count would be 20x; BM25 must saturate"


def test_bm25_normalises_for_document_length():
    """The old ranker let a long page win by sheer repetition; length must now cost."""
    tf = [{"光储": 3}, {"光储": 3}]
    short, long = recall_bm25.bm25(tf, [20, 2000])
    assert short > long


def test_rare_term_outweighs_a_term_every_page_has():
    tf = [
        {"光储": 2, "钙钛矿": 0},
        {"光储": 2, "钙钛矿": 0},
        {"光储": 2, "钙钛矿": 2},
    ]
    scores = recall_bm25.bm25(tf, [100, 100, 100])
    assert scores[2] > scores[0] == scores[1], "IDF must downweight the common term"


def test_search_is_byte_identical_across_calls(brain):
    mind, _surface = brain
    for i in range(6):
        _page(mind / "wiki" / "concepts" / f"概念{i}.md", i + 1)
    assert retrieval.search(mind, "光储 容量") == retrieval.search(mind, "光储 容量")


def test_dead_pages_never_enter_context(brain):
    mind, _surface = brain
    _page(mind / "wiki" / "concepts" / "现行.md", 3)
    _page(mind / "wiki" / "concepts" / "已作废.md", 8,
          frontmatter="title: 已作废\nstatus: superseded")
    _page(mind / "wiki" / "concepts" / "被替代.md", 8)
    _page(mind / "wiki" / "concepts" / "新结论.md", 2,
          frontmatter="title: 新结论\nsupersedes: 被替代")

    joined = "\n\n".join(retrieval.search(mind, _HAY, limit=10))

    assert "现行" in joined, "a live page must still be recalled"
    assert "已作废" not in joined, "status: superseded must not reach the prompt"
    assert "被替代" not in joined, "a page named by supersedes must be dropped"


def test_legacy_pages_without_status_stay_recallable(brain):
    """All 53 existing wiki pages have no status field: the new gate must not hide them."""
    mind, _surface = brain
    _page(mind / "wiki" / "concepts" / "老页.md", 3)
    out = retrieval.search(mind, _HAY, limit=5)
    assert any("老页" in block for block in out)


def test_supersedes_accepts_list_and_path_forms():
    assert recall_bm25.superseded_targets(
        "supersedes: [旧甲, Mind/wiki/concepts/旧乙.md]"
    ) == ("旧甲", "Mind/wiki/concepts/旧乙")


def test_page_is_dead_covers_the_closed_vocabulary():
    for status in ("superseded", "rejected", "deprecated", "contested"):
        assert recall_bm25.page_is_dead(f"status: {status}")
    assert recall_bm25.page_is_dead("superseded_by: 新结论")
    assert not recall_bm25.page_is_dead("")
    for status in ("active", "accepted", "provisional", "unknown-value"):
        assert not recall_bm25.page_is_dead(f"status: {status}")


def test_rank_drops_dead_pages_and_orders_by_score():
    candidates = [
        {"tf": {"光储": 1}, "doc_len": 30, "meta": "status: superseded",
         "rel": "Mind/wiki/concepts/死.md"},
        {"tf": {"光储": 4}, "doc_len": 30, "meta": "",
         "rel": "Mind/wiki/concepts/强.md"},
        {"tf": {"光储": 1}, "doc_len": 30, "meta": "",
         "rel": "Mind/wiki/concepts/弱.md"},
    ]
    out = recall_bm25.rank(candidates, [_HAY])
    assert [hit["rel"] for hit in out] == [
        "Mind/wiki/concepts/强.md", "Mind/wiki/concepts/弱.md"
    ]
    assert all(hit["score"] > 0 for hit in out)


def test_injection_budget_and_demotion_still_hold(brain, monkeypatch):
    """The BM25 swap must not regress the §12.7 tier/budget contract."""
    mind, _surface = brain
    for i in range(12):
        _page(mind / "wiki" / "concepts" / f"概念{i}.md", 12 - i, extra="补白")
    monkeypatch.setattr(retrieval, "MAX_TOTAL_CHARS", 900)
    out = retrieval.search(mind, _HAY, limit=12)
    assert out
    assert len("\n\n".join(out)) <= 900
    bodies = [block.split("\n\n", 1)[1] for block in out]
    assert all(len(body) <= retrieval.TIER_CHARS["full"] for body in bodies)
    assert any(len(body) <= retrieval.TIER_CHARS["abstract"] for body in bodies[2:])
