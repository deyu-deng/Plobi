"""Derived event-entity store: incremental, rebuildable, deterministic."""

from __future__ import annotations

from plobi.sag import SagStore, index_tree


def _page(text: str) -> str:
    return text


def _chain() -> list[tuple[str, str]]:
    """A --(entity m)--> B --(entity n)--> C."""
    return [
        ("zones/A.md", _page(
            "---\ntitle: A\ntags: [m]\n---\n\n# A\n\n光储项目的第一条结论。\n"
        )),
        ("zones/B.md", _page(
            "---\ntitle: B\ntags: [m, n]\n---\n\n# B\n\n中间桥梁，连着两端。\n"
        )),
        ("zones/C.md", _page(
            "---\ntitle: C\ntags: [n]\n---\n\n# C\n\n钙钛矿那一段的收尾。\n"
        )),
    ]


def test_upsert_only_writes_changed_pages(tmp_path):
    with SagStore(tmp_path / "sag.db") as store:
        first = index_tree(_chain(), store)
        assert first == {"pages": 3, "changed": 3, "removed": 0}

        second = index_tree(_chain(), store)
        assert second["changed"] == 0, "unchanged pages must not be rewritten"

        grown = _chain() + [(
            "zones/D.md",
            "---\ntitle: D\ntags: [n]\n---\n\n# D\n\n新增一页。\n",
        )]
        third = index_tree(grown, store)
        assert third == {"pages": 4, "changed": 1, "removed": 0}

        shrunk = index_tree(_chain()[:2], store)
        assert shrunk["removed"] == 2, "vanished pages must leave the derived index"


def test_rebuild_produces_the_same_store(tmp_path):
    pages = _chain()
    with SagStore(tmp_path / "a.db") as a, SagStore(tmp_path / "b.db") as b:
        index_tree(pages, a)
        index_tree(pages, b)
        assert a.all_sources() == b.all_sources()
        assert a.seed_sources(["光储"]) == b.seed_sources(["光储"])
        assert a.expand(a.seed_sources(["光储"]), hops=2) == \
               b.expand(b.seed_sources(["光储"]), hops=2)


def test_extract_page_uses_wikilinks_and_tags_without_a_model():
    from plobi.sag import extract_page

    page = extract_page(
        "---\ntitle: 储能\ntags: [光储, 电池]\n---\n\n# 储能\n\n"
        "第一段正文。见 [[容量衰减]] 与 [[循环寿命]]。\n"
    )
    assert page["title"] == "储能"
    assert page["abstract"] == "第一段正文。见 [[容量衰减]] 与 [[循环寿命]]。"
    assert {"容量衰减", "循环寿命", "光储", "电池", "储能"} <= set(page["entities"])
    assert len(page["content_hash"]) == 64


def test_extra_entities_is_the_model_topup_seam():
    from plobi.sag import extract_page

    plain = extract_page("---\ntitle: A\n---\n\n# A\n\n正文。\n")
    topped = extract_page(
        "---\ntitle: A\n---\n\n# A\n\n正文。\n", extra_entities=["模型补的实体"]
    )
    assert "模型补的实体" not in plain["entities"]
    assert "模型补的实体" in topped["entities"]
