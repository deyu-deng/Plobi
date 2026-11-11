"""笔记链接图（反向链接面板的数据源）。

钉住四件事：

① 出链按**文件名**或 **frontmatter 的 title** 解析成逻辑路径；
② 解析不上的目标**原样保留**（屏上显示成「未创建」），但**不进反向表**——
   指向不存在的东西没有「被谁引用」的落点；
③ 代码围栏里的 ``[[…]]`` 不算链接，嵌图（``![[图.png]]``）也不算；
④ 同文件锚点（``[[#ch1]]``）不是出链。
"""

from __future__ import annotations

import pytest

from plobi.mind import notes_links


@pytest.fixture()
def vault(monkeypatch, tmp_path):
    surface = tmp_path / "surface"
    (surface / "notes" / "数学").mkdir(parents=True)
    (surface / "notes" / "物理").mkdir(parents=True)

    (surface / "notes" / "数学" / "微积分.md").write_text(
        "---\ntitle: 微积分\n---\n\n极限见 [[大学物理]]，也见 [[没写的那篇]]。\n\n"
        "同篇锚点 [[#ch1]] 不算出链。\n\n```md\n[[围栏里的不算]]\n```\n",
        encoding="utf-8",
    )
    (surface / "notes" / "物理" / "大学物理.md").write_text(
        "---\ntitle: 大学物理笔记\n---\n\n回到 [[微积分]]。\n\n![[插图.png]]\n",
        encoding="utf-8",
    )

    mind = tmp_path / "mind"
    (mind / "wiki").mkdir(parents=True)

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("SURFACE_ROOT", str(surface))

    return surface


def test_outgoing_links_resolve_by_file_name_and_title(vault):
    graph = notes_links.build_link_graph()

    # 「大学物理」按**文件名**解析；「微积分」按**文件名**解析。
    assert graph["links"]["Notes/数学/微积分.md"] == [
        "Notes/物理/大学物理.md",
        "没写的那篇",
    ]
    assert graph["links"]["Notes/物理/大学物理.md"] == ["Notes/数学/微积分.md"]


def test_backlinks_are_the_inverse_of_outgoing(vault):
    graph = notes_links.build_link_graph()

    assert graph["backlinks"]["Notes/物理/大学物理.md"] == ["Notes/数学/微积分.md"]
    # 「没写的那篇」不是一篇笔记 ⇒ 没有落点 ⇒ 不进反向表。
    assert "没写的那篇" not in graph["backlinks"]


def test_fences_embeds_and_anchors_are_not_links(vault):
    graph = notes_links.build_link_graph()
    outgoing = graph["links"]["Notes/数学/微积分.md"]

    assert "围栏里的不算" not in outgoing
    assert "#ch1" not in outgoing
    assert graph["links"]["Notes/物理/大学物理.md"] == ["Notes/数学/微积分.md"]


def test_titles_and_totals_come_from_frontmatter(vault):
    graph = notes_links.build_link_graph()

    assert graph["titles"]["Notes/物理/大学物理.md"] == "大学物理笔记"
    assert graph["totals"]["notes"] == 2
    assert graph["totals"]["links"] == 3


def test_missing_notes_root_raises_instead_of_reporting_an_empty_graph(monkeypatch, tmp_path):
    """根不在盘上 ⇒ **报错**，不返回空表。

    空表会被屏上读成「一篇笔记都没有」——那不是答案，是「找不到笔记在哪儿」。
    """

    mind = tmp_path / "mind"
    (mind / "wiki").mkdir(parents=True)

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("SURFACE_ROOT", str(tmp_path / "surface"))

    with pytest.raises(FileNotFoundError):
        notes_links.build_link_graph()


def test_route_returns_the_graph_in_an_envelope(vault):
    """路由的信封：``{"ok": true, "data": …}``——前端按这一层解。"""

    import asyncio
    import json

    from plobi.mind import surface_api

    response = asyncio.run(surface_api.get_surface_note_links())
    body = response if isinstance(response, dict) else json.loads(response.body)

    assert body["ok"] is True
    assert body["data"]["totals"]["notes"] == 2
    assert body["data"]["backlinks"]["Notes/物理/大学物理.md"] == ["Notes/数学/微积分.md"]


def test_route_reports_unavailable_instead_of_an_empty_graph(monkeypatch, tmp_path):
    """根读不到 ⇒ 回 503，**不回空表**（屏上分不出「没有笔记」和「找不到笔记」）。"""

    import asyncio
    import json

    from plobi.mind import surface_api

    mind = tmp_path / "mind"
    (mind / "wiki").mkdir(parents=True)

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("SURFACE_ROOT", str(tmp_path / "surface"))

    response = asyncio.run(surface_api.get_surface_note_links())
    body = response if isinstance(response, dict) else json.loads(response.body)

    assert body["ok"] is False
    assert response.status_code == 503
