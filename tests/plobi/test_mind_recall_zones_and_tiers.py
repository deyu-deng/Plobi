"""召回的两条边界：只读脑里的记忆区；预算不够时降级成摘要，而不是把一句半截话塞进去。

形状是抄来的（``Docs/specs/memory-adapter.md`` §12.6／§12.7 第 3 条）：openviking 先过取
``limit*4`` → 打分 → 按 abstract 去重 → 三级深度（abstract/overview/full）→ 每次追加前先算
``projected_chars``。我们原来只有「截断」，没有「退到摘要」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.mind import retrieval
from plobi.mind.paths import MEMORY_ZONES

_HAY = "光储"
_VAULT_SENTINEL = "这一段只在人读面上"


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "concepts").mkdir(parents=True)
    (surface / "notes").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    return mind, surface


def _page(path: Path, hits: int, *, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [f"{_HAY}第 {i} 条：容量 {i} MWh，功率 {i + 1} MW。" for i in range(hits)]
        + [extra * 40]
    )
    path.write_text(f"---\ntitle: {path.stem}\n---\n\n# {path.stem}\n\n{body}\n", encoding="utf-8")


def test_recall_only_reads_the_brain_never_the_human_surface(brain):
    """人读面里再多的关键词命中也不进上下文（MEMORY_ZONES 只含脑那一半）；目录页也不挤占预算。"""
    mind, surface = brain
    _page(surface / "notes" / "人写的笔记.md", 40, extra=_VAULT_SENTINEL)     # strongest, wrong repo
    _page(mind / "wiki" / "concepts" / "储能.md", 3)                        # weaker, but it is canon
    _page(mind / "wiki" / "concepts" / "INDEX.md", 30, extra="目录：什么都提一句")  # 泛指页，最会抢分

    out = retrieval.search(mind, _HAY, limit=5)
    joined = "\n\n".join(out)

    assert out, "脑里有命中就该给东西"
    assert _VAULT_SENTINEL not in joined, "人读面那一页一个字节都不许进上下文"
    assert "人写的笔记" not in joined
    assert "INDEX" not in joined, "目录页短而泛指，挤掉真页的预算＝负例"
    for block in out:
        rel = block.splitlines()[0][len("### Mind: "):]
        assert any(rel == zone or rel.startswith(zone + "/") for zone in MEMORY_ZONES), rel


def test_a_tight_budget_demotes_a_page_instead_of_truncating_it(brain, monkeypatch):
    """塞不下整页时退到摘要级；总注入量硬卡在上限内，且不出现半截句子。"""
    mind, _vault = brain
    for i in range(12):
        _page(mind / "wiki" / "concepts" / f"概念{i}.md", 12 - i, extra="补白")

    monkeypatch.setattr(retrieval, "MAX_TOTAL_CHARS", 900)
    out = retrieval.search(mind, _HAY, limit=12)

    assert out, "预算小不等于该一条不给"
    assert len("\n\n".join(out)) <= 900, f"注入量超了硬上限：{len(chr(10).join(out))}"
    bodies = [b.split("\n\n", 1)[1] for b in out]
    assert all(len(body) <= retrieval.TIER_CHARS["full"] for body in bodies)
    # 深的那几页之外，必须已经退到摘要级（而不是 overview 被切一半）
    assert any(len(body) <= retrieval.TIER_CHARS["abstract"] for body in bodies[2:]), bodies
    for body in bodies:
        assert not body.endswith("，功率") and not body.endswith("：容量"), "半截句子=截断没降级"
