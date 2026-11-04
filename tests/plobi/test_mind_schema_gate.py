"""词表闸（§12.7 第 1 条）：正典页的 frontmatter 值必须落在脑里那一份真源里。

钉的是「写了就好」不够——claude-obsidian 的教训是一个 type 值在四处声明、并集十二个交集两个，
而 lint 只查字段在不在（memory-adapter.md §12.6「两条别抄」第 2 条）。
所以判据是**值**：不合法就拒；新页缺必填键也拒；append 到存量页不拿旧账挡人。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plobi.mind import schema as mind_schema
from plobi.mind.writer import MindWriter

_SCHEMA = {
    "checked_dirs": ["wiki/concepts", "wiki/sources", "wiki/memory"],
    "skip_names": ["INDEX.md"],
    "page_types": ["concept", "entity", "source", "synthesis"],
    "source_statuses": ["unreviewed", "active", "superseded", "rejected"],
    "claim_assessments": ["accepted", "provisional"],
    "evidence_relations": ["supports", "contradicts", "context"],
    "required_by_dir": {"wiki/concepts": ["type"], "wiki/sources": ["type", "status"],
                        "wiki/memory": []},
}


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    mind = tmp_path / "Mind"
    surface = tmp_path / "Surface"
    (mind / "wiki" / "concepts").mkdir(parents=True)
    (mind / "wiki" / "meta").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Plobi").mkdir(parents=True)
    (surface / "dossiers" / "courses" / "电路分析").mkdir(parents=True)
    (mind / "wiki" / "meta" / "schema.json").write_text(
        json.dumps(_SCHEMA, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("SURFACE_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    return mind, surface


def _write(mind, rel, content):
    return MindWriter(mind, require_approval=False, commit=False,
                      run_verifier=False).write_one(rel, content)


def test_illegal_values_and_an_unpointable_belongs_are_refused_before_they_land(brain):
    mind, _vault = brain

    refused = _write(mind, "Mind/wiki/concepts/bad.md",
                     "---\ntype: bogus\nstatus: reviewed-by-alien\nbelongs: [NoSuchProject]\n---\n\n# bad\n")

    assert refused.ok is False
    detail = refused.detail or ""
    assert "词表闸" in detail, detail
    assert "type「bogus」" in detail
    assert "status「reviewed-by-alien」" in detail
    assert "belongs「NoSuchProject」" in detail
    assert not (mind / "wiki" / "concepts" / "bad.md").exists(), "被拒的一字节都不该落盘"

    # 同一条 belongs 换成真源里现取得到的名字就通过：闸挡的是指不出去，不是不认识路径
    ok = _write(mind, "Mind/wiki/concepts/good.md",
                "---\ntype: concept\nbelongs: [Plobi]\n---\n\n# good\n")
    assert ok.ok, ok.detail
    assert (mind / "wiki" / "concepts" / "good.md").is_file()


def test_a_new_canon_page_missing_a_required_key_is_refused_while_appends_still_pass(brain):
    mind, _vault = brain

    refused = _write(mind, "Mind/wiki/sources/nobody-said.md", "# 一份来源资料，没有 frontmatter\n")
    assert refused.ok is False
    detail = refused.detail or ""
    assert "缺必填键" in detail or "没有 frontmatter" in detail, detail
    assert "type" in detail and "status" in detail, detail
    assert not (mind / "wiki" / "sources" / "nobody-said.md").exists()

    # 先按词表建起来，再 append 一段话：拿历史页旧账挡当前这次追加＝噪音，不是牙
    assert _write(mind, "Mind/wiki/sources/src.md",
                  "---\ntype: source\nstatus: unreviewed\n---\n\n# src\n").ok
    appended = MindWriter(mind, require_approval=False, commit=False, run_verifier=False).write_one(
        "Mind/wiki/sources/src.md", "\n补一段\n", mode="append")
    assert appended.ok, appended.detail

    # 词表读不到时不装死也不停腿：退到内置副本，记忆这条腿照常写得动
    (mind / "wiki" / "meta" / "schema.json").unlink()
    assert mind_schema.load_schema(mind)["page_types"] == mind_schema.FALLBACK["page_types"]
    assert _write(mind, "Mind/wiki/memory/note.md", "# 镜像条目\n").ok
