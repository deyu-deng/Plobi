"""Query-time hyperedges, source tracing, derived-layer guarantee, dispatch."""

from __future__ import annotations

import pytest

from plobi.sag import FAST, MULTI, SagStore, choose_mode, index_tree, recall, recall_events

_CHAIN = [
    ("zones/A.md",
     "---\ntitle: A\ntags: [m]\n---\n\n# A\n\n光储项目的第一条结论。\n"),
    ("zones/B.md",
     "---\ntitle: B\ntags: [m, n]\n---\n\n# B\n\n中间桥梁，连着两端。\n"),
    ("zones/C.md",
     "---\ntitle: C\ntags: [n]\n---\n\n# C\n\n钙钛矿那一段的收尾。\n"),
]


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "concepts").mkdir(parents=True)
    (surface / "notes").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    return mind, surface


def _write(mind: object, name: str, text: str) -> None:
    path = mind / "wiki" / "concepts" / name  # type: ignore[operator]
    path.write_text(text, encoding="utf-8")


def test_hyperedge_expands_one_hop_at_a_time(tmp_path):
    with SagStore(tmp_path / "sag.db") as store:
        index_tree(_CHAIN, store)
        one = recall_events(store, "光储", hops=1)
        two = recall_events(store, "光储", hops=2)

        one_src = [c["source"] for c in one.candidates]
        two_src = [c["source"] for c in two.candidates]
        assert "zones/A.md" in one_src and "zones/B.md" in one_src
        assert "zones/C.md" not in one_src, "one hop must not reach C"
        assert "zones/C.md" in two_src, "the second hop must bring C back"
        assert any(hop["bridge"] == "m" for hop in two.trace["hops"])
        assert two.trace["seeds"] == ["zones/A.md"]
        assert [c["source"] for c in two.candidates] == \
               [c["source"] for c in recall_events(store, "光储", hops=2).candidates]


def test_dead_pages_are_not_indexed(tmp_path):
    from plobi.sag.indexer import dead_sources

    pages = _CHAIN + [
        ("zones/作废.md",
         "---\ntitle: 作废\nstatus: superseded\ntags: [m]\n---\n\n# 作废\n\n光储旧结论。\n"),
        ("zones/被替代.md",
         "---\ntitle: 被替代\ntags: [m]\n---\n\n# 被替代\n\n光储更早的结论。\n"),
        ("zones/新结论.md",
         "---\ntitle: 新结论\nsupersedes: 被替代\n---\n\n# 新结论\n\n光储现行结论。\n"),
    ]
    dead = set(dead_sources(pages))
    assert "zones/作废.md" in dead
    assert "zones/被替代.md" in dead
    assert "zones/新结论.md" not in dead

    with SagStore(tmp_path / "sag.db") as store:
        index_tree(pages, store, dead_sources=dead)
        assert "zones/作废.md" not in store.all_sources()
        assert "zones/被替代.md" not in store.all_sources()


def test_candidates_map_back_to_real_files(brain, tmp_path):
    mind, _surface = brain
    _write(mind, "储能.md",
           "---\ntitle: 储能\ntags: [光储]\n---\n\n# 储能\n\n光储容量与功率。\n")
    _write(mind, "材料.md",
           "---\ntitle: 材料\ntags: [光储]\n---\n\n# 材料\n\n钙钛矿那一页。\n")

    out = recall(mind, "光储 和 关系", mode=MULTI, db_path=tmp_path / "sag.db", hops=2)

    assert out["mode"] == MULTI
    assert out["candidates"], "a relational query must reach the multi tier"
    for candidate in out["candidates"]:
        rel = candidate["source"].removeprefix("Mind/")
        assert (mind / rel).is_file(), candidate["source"]
    assert out["trace"]["hops"], "the bridge chain must be inspectable"


def test_indexing_never_touches_canonical_markdown(brain, tmp_path):
    mind, _surface = brain
    path = mind / "wiki" / "concepts" / "储能.md"
    path.write_text(
        "---\ntitle: 储能\ntags: [光储]\n---\n\n# 储能\n\n光储容量与功率。\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    db = tmp_path / "sag.db"

    first = recall(mind, "光储", mode=MULTI, db_path=db)["candidates"]
    assert path.read_bytes() == before, "the derived index must never write to canon"
    assert first == recall(mind, "光储", mode=MULTI, db_path=db)["candidates"], \
        "a no-op re-index must return byte-identical results"

    db.unlink()
    rebuilt = recall(mind, "光储", mode=MULTI, db_path=db)["candidates"]
    assert rebuilt == first, "delete + rebuild must reproduce the same result"


def test_dispatch_is_deterministic_and_fast_stays_on_bm25(brain, tmp_path):
    mind, _surface = brain
    _write(mind, "储能.md",
           "---\ntitle: 储能\ntags: [光储]\n---\n\n# 储能\n\n光储容量与功率。\n")

    assert choose_mode("光储") == FAST
    assert choose_mode("光储 和 储能 有什么关系") == MULTI
    assert choose_mode("光储", explicit=MULTI) == MULTI

    fast = recall(mind, "光储", mode=FAST, db_path=tmp_path / "unused.db")
    assert fast["mode"] == FAST and fast["trace"] is None
    assert any("### Mind:" in block for block in fast["blocks"])
    assert not (tmp_path / "unused.db").exists(), "fast must not build an index"


def test_mind_adapter_does_not_index_the_human_surface(brain, tmp_path):
    mind, surface = brain
    _write(mind, "储能.md",
           "---\ntitle: 储能\ntags: [光储]\n---\n\n# 储能\n\n光储容量与功率。\n")
    (surface / "notes" / "人写的笔记.md").write_text(
        "---\ntitle: 人写的笔记\n---\n\n# 人写的笔记\n\n光储这一段只在人读面。\n",
        encoding="utf-8",
    )
    out = recall(mind, "光储", mode=MULTI, db_path=tmp_path / "sag.db")
    joined = " ".join(c["source"] for c in out["candidates"])
    assert "人写的笔记" not in joined
