"""三表分离：``vault.db`` 的 schema、CRUD、迁移与降级腿（WP-VAULT-TAKEOVER）。

被测的是 ``plobi.mind.vault`` —— 人读面那三份数据（笔记／档案／日记）
在库里各有一张表。**DB 只做读侧加速与元数据索引，不接管写入**：
真值仍在盘上的 md 文件里（判据六）。

迁移的口径（判据一、二、五）：
* 扫描根走 ``real_target()``，**不手拼字面路径**；
* 分类名照盘上真实的写（``butler``，不是 ``butlers``）；
* **只增不改**：原文件一个字节都不动（判据六）。
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from plobi.mind import vault

# ``migrate_vault_db`` 住在 ``scripts/`` 下，不在包路径上 ⇒ 显式把它那一层加进 sys.path
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """把库指到一个临时目录——**绝不碰盘上那两个真库**。

    落点形状照盘上真实的来：``<家>/plobi/vault.db``。
    """
    path = tmp_path / "Plobi-Dev" / "plobi" / "vault.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_VAULT_DB", str(path))
    # 换库 ⇒ 初始化标记要跟着失效，否则 schema 不会在新库上建
    vault._INITIALIZED.clear()
    yield path
    vault._INITIALIZED.clear()


def _physical(logical: str) -> Path:
    """逻辑前缀 → 这一档测试用的物理相对路径（``Notes/…`` ⇒ ``notes/…``）。

    旧写法 ``Surface/notes/…`` 也认（**仍解析**）——那一档是兼容期的兜底。
    """
    parts = Path(logical).as_posix().split("/")
    head, rest = parts[0], parts[1:]
    if head in ("Notes", "Diary", "Dossiers", "Meta", "Inbox"):
        return Path(head.lower()).joinpath(*rest)
    if head == "Surface":
        return Path(*rest)
    return Path(*parts)


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# --- A 组：schema 与 CRUD ------------------------------------------------


def test_three_tables_exist(db):
    """三张表都建出来。"""
    conn = vault.connect()
    try:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"vault_notes", "agent_dossiers", "user_diary"} <= names


def test_note_roundtrip(db):
    """笔记：写进去读出来是同一条。"""
    note = vault.VaultNote(
        path="Notes/数学/微积分.md",
        title="微积分",
        topic="数学",
        tags=["math"],
        belongs=["Aura"],
        relates=[],
        updated="2026-11-01",
        chars=1200,
        excerpt="极限与连续。",
    )
    vault.upsert_note(note)

    got = vault.get_note("Notes/数学/微积分.md")
    assert got is not None
    assert got.title == "微积分"
    assert got.topic == "数学"
    assert got.tags == ["math"]
    assert got.belongs == ["Aura"]
    assert got.chars == 1200


def test_upsert_is_idempotent(db):
    """同一条写两次不会变成两行（迁移脚本会重复跑）。"""
    note = vault.VaultNote(path="Notes/a.md", title="A", topic="")
    vault.upsert_note(note)
    vault.upsert_note(vault.VaultNote(path="Notes/a.md", title="A2", topic=""))
    assert len(vault.list_notes()) == 1
    assert vault.get_note("Notes/a.md").title == "A2"


def test_the_old_logical_prefix_no_longer_finds_the_note(db):
    """旧写法 ``Surface/notes/…`` **查不到**了（2026-11-13 归一已删）。

    库里只认 ``Notes/…`` 这种逻辑路径；``Surface/…`` 那层结构连同它的翻译一起停用，
    留着半条兜底路就会让下一次重构再长出一次旧布局。
    """
    vault.upsert_note(
        vault.VaultNote(path="Notes/数学/微积分.md", title="微积分", topic="数学")
    )
    assert vault.get_note("Surface/notes/数学/微积分.md") is None
    # 新写法照旧查得到，回给调用方的也是它自己。
    found = vault.get_note("Notes/数学/微积分.md")
    assert found is not None and found.title == "微积分"
    assert found.path == "Notes/数学/微积分.md"


def test_dossier_roundtrip(db):
    """档案：里程碑与正文都存得住。"""
    dossier = vault.AgentDossier(
        agent_id="aura",
        plan_content="# 计划\n",
        progress_content="- [x] 第一步\n- [ ] 第二步\n",
        milestones_total=2,
        milestones_done=1,
        work_path=r"D:\Data\AppData\Plobi-Dev\dossiers\projects\Aura",
        updated="2026-11-08T10:00:00",
    )
    vault.upsert_dossier(dossier)

    got = vault.get_dossier("aura")
    assert got is not None
    assert got.milestones_total == 2
    assert got.milestones_done == 1
    assert "第一步" in got.progress_content
    assert got.updated == "2026-11-08T10:00:00"


def test_diary_roundtrip(db):
    """日记：按日期存取。"""
    vault.upsert_diary(
        vault.UserDiaryEntry(date="2026-11-08", content="今天开始接手。", updated="2026-11-08")
    )
    got = vault.get_diary("2026-11-08")
    assert got is not None
    assert got.content == "今天开始接手。"
    assert len(vault.list_diaries()) == 1


def test_search_notes(db):
    """搜索按标题与摘要命中。"""
    vault.upsert_note(
        vault.VaultNote(path="Notes/x.md", title="微积分导数", topic="数学", excerpt="链式法则")
    )
    hits = vault.search_notes("微积分")
    assert any(h.title == "微积分导数" for h in hits)


# --- B 组：迁移（口径与「只增不改」） ------------------------------------


def _make_surface(tmp_path: Path) -> Path:
    """造一份最小的人读面：两篇笔记、一个档案、一篇日记。

    **形状照盘上真实的来**：人读面就是**家的本体**，``notes/``／``dossiers/``／
    ``diary/`` 直接在它底下（真盘是 ``D:\\Data\\AppData\\Plobi-Dev\\notes`` 这样），
    不是 ``<家>/surface/notes``。
    """
    home = tmp_path / "Plobi-Dev"
    surface = home
    (surface / "notes" / "数学").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (surface / "diary").mkdir(parents=True)
    (surface / "plobi").mkdir(parents=True, exist_ok=True)

    (surface / "notes" / "数学" / "微积分.md").write_text(
        textwrap.dedent(
            """\
            ---
            title: 微积分
            tags: [math]
            belongs: [Aura]
            ---

            # 微积分
            """ + "极限与连续。" * 60
        ),
        encoding="utf-8",
    )
    (surface / "dossiers" / "projects" / "Aura" / "plan.md").write_text(
        "# 计划\n\n第一版。\n", encoding="utf-8"
    )
    (surface / "dossiers" / "projects" / "Aura" / "progress.md").write_text(
        "- [x] 已做\n- [ ] 未做\n", encoding="utf-8"
    )
    (surface / "diary" / "2026-11-08-Sunday.md").write_text(
        "今天开始接手。\n", encoding="utf-8"
    )
    return surface


def _point_at_home(monkeypatch, home: Path):
    """把 ``real_target`` 与 ``vault_db_path`` 一起指到同一个家。

    两个必须同源——迁移脚本那道门就是查这一条：库与数据根不在同一家就拒绝跑。
    ``HUMAN_ROOT`` 也得钉到同一个家：``migrate_vault_db.get_roots()`` 走的是
    解析器那一梯（``Notes/…`` 落在人读面家里），不钉就会落到 conftest 的临时
    ``PLOBI_HOME`` 上，于是扫到 0 条。
    """
    from plobi.mind import paths as paths_mod

    monkeypatch.setenv("HUMAN_ROOT", str(home))
    monkeypatch.setattr(
        paths_mod,
        "real_target",
        lambda logical, root=None: home / _physical(logical),
    )
    monkeypatch.setattr(vault, "vault_db_path", lambda: home / "plobi" / "vault.db")


def test_migrate_reads_real_layout_and_never_writes_back(db, tmp_path, monkeypatch):
    """迁移：扫得到、落得对，且**原文件一个字节没动**（判据一、二、六）。

    这条钉的是「只增不改」这条硬边界——用户 11-08 14:3x 原话：
    「我们数据目录变了很多。你不要让一些东西回归了。」
    """
    surface = _make_surface(tmp_path)

    # 把 real_target 与库一起指到这份临时人读面上
    _point_at_home(monkeypatch, surface)

    migrate_mod = importlib.import_module("migrate_vault_db")

    roots = migrate_mod.get_roots()
    assert roots["notes"] == surface / "notes"
    assert roots["dossiers"] == surface / "dossiers"
    assert roots["diary"] == surface / "diary"

    # 迁移前记下原文件的字节
    before = {
        p: p.read_bytes()
        for p in list(surface.rglob("*.md"))
    }

    n_notes = migrate_mod.migrate_notes(roots)
    n_dossiers = migrate_mod.migrate_dossiers(roots)
    n_diary = migrate_mod.migrate_diary(roots)

    assert n_notes == 1
    assert n_dossiers == 1
    assert n_diary == 1

    # 三张表各一行
    assert _count(db, "vault_notes") == 1
    assert _count(db, "agent_dossiers") == 1
    assert _count(db, "user_diary") == 1

    # 内容对得上
    note = vault.get_note("Notes/数学/微积分.md")
    assert note is not None
    assert note.title == "微积分"
    assert note.belongs == ["Aura"]

    dossier = vault.get_dossier("Aura")
    assert dossier is not None
    assert dossier.milestones_total == 2
    assert dossier.milestones_done == 1
    assert "第一版" in dossier.plan_content

    diary = vault.get_diary("2026-11-08")
    assert diary is not None
    assert "今天开始接手" in diary.content

    # **原文件一个字节都没动**
    for path, content in before.items():
        assert path.read_bytes() == content


def test_migrate_timestamps_are_iso_not_mtime(db, tmp_path, monkeypatch):
    """落库的时间戳是 ISO 字符串，不是 mtime 浮点（浮点上屏会变成一串数字）。"""
    surface = _make_surface(tmp_path)
    _point_at_home(monkeypatch, surface)
    migrate_mod = importlib.import_module("migrate_vault_db")
    migrate_mod.migrate_dossiers(migrate_mod.get_roots())

    dossier = vault.get_dossier("Aura")
    assert dossier.updated
    assert not dossier.updated.replace(".", "").isdigit()  # 不是 1786… 那种浮点
    assert "T" in dossier.updated  # ISO


def test_dossier_category_is_butler_not_butlers(db, tmp_path, monkeypatch):
    """分类名照盘上真实的写：`butler`（旧代码写的是 `butlers`，扫不到）。"""
    home = tmp_path / "Plobi-Dev"
    (home / "dossiers" / "butler" / "Planner").mkdir(parents=True)
    (home / "plobi").mkdir(parents=True, exist_ok=True)
    (home / "dossiers" / "butler" / "Planner" / "plan.md").write_text(
        "# 计划\n", encoding="utf-8"
    )
    _point_at_home(monkeypatch, home)
    migrate_mod = importlib.import_module("migrate_vault_db")
    assert migrate_mod.migrate_dossiers(migrate_mod.get_roots()) == 1
    assert _count(db, "agent_dossiers") == 1


# --- C 组：降级腿 --------------------------------------------------------


def test_missing_db_falls_back_to_filesystem(monkeypatch, tmp_path):
    """库指向一个坏路径 ⇒ API 端点仍返回数据（走文件系统那条腿）。

    判据六：DB 空／坏／异常都要能降级，**屏上不许白屏**。
    """
    from plobi.mind import paths as paths_mod
    from plobi.mind import surface_api

    surface = _make_surface(tmp_path)
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setattr(
        paths_mod, "real_target", lambda logical, root=None: surface / _physical(logical)
    )
    # 指到一个**建不出来的**路径 ⇒ connect() 抛错
    monkeypatch.setenv("PLOBI_VAULT_DB", str(tmp_path / "no" / "such" / "dir" / "x.db"))
    monkeypatch.setattr(vault, "vault_db_path", lambda: tmp_path / "no" / "such" / "dir" / "x.db")
    vault._INITIALIZED.clear()
    import asyncio

    # 端点里那些 `from .vault import ...` 是局部导入 ⇒ 直接调函数即可
    result = asyncio.run(surface_api.list_surface_notes())
    assert result["ok"] is True
    # 降级腿给的是文件系统腿的形状（notes 来自 build_library）
    assert isinstance(result["data"]["notes"], list)


def test_registry_has_get_not_get_agent():
    """钉住那条硬伤：`AgentRegistry` **没有** `get_agent`，只有 `get`。

    旧代码调 `registry.get_agent(...)` ⇒ AttributeError ⇒ 每次都落回文件系统，
    DB 白建。这条测试是为了下次有人再写错时当场红。
    """
    from plobi.agents.registry import AgentRegistry

    assert hasattr(AgentRegistry, "get")
    assert not hasattr(AgentRegistry, "get_agent")


# --- D 组：跨家边界（用户 11-08 14:5x 拍定） ------------------------------


def test_migration_refuses_to_cross_homes(tmp_path, monkeypatch):
    """**库与数据根不在同一个家 ⇒ 当场拒绝迁移。**

    用户 11-08 14:5x 原话：「我们其实不需要把开发版的数据迁移过来。
    就让安装版保持什么都没有的全新状态。」

    开发态（``Plobi-Dev``）与装机态（``Plobi``）是**两个身份**，各自的数据互不相干。
    这一条就是那道门：把它钉住，下次谁想「顺手把开发版的数据搬过去」时当场红。
    """
    from plobi.mind import paths as paths_mod

    migrate_mod = importlib.import_module("migrate_vault_db")

    # 人读面指到「开发态」那个家
    dev_surface = tmp_path / "Plobi-Dev"
    (dev_surface / "notes").mkdir(parents=True)
    monkeypatch.setattr(
        paths_mod,
        "real_target",
        lambda logical, root=None: dev_surface / _physical(logical),
    )

    # 但库指到「装机态」那个家 ⇒ 两个身份 ⇒ 必须拒绝
    other_home = tmp_path / "Plobi"
    (other_home / "plobi").mkdir(parents=True)
    monkeypatch.setattr(vault, "vault_db_path", lambda: other_home / "plobi" / "vault.db")

    with pytest.raises(SystemExit) as exc:
        migrate_mod.get_roots()
    assert "不在同一个家" in str(exc.value)


def test_migration_allows_same_home(tmp_path, monkeypatch):
    """同一个家里 ⇒ 放行。"""
    from plobi.mind import paths as paths_mod

    migrate_mod = importlib.import_module("migrate_vault_db")

    home = tmp_path / "Plobi-Dev"
    (home / "notes").mkdir(parents=True)
    (home / "plobi").mkdir(parents=True)
    monkeypatch.setattr(
        paths_mod,
        "real_target",
        lambda logical, root=None: home / _physical(logical),
    )
    monkeypatch.setattr(vault, "vault_db_path", lambda: home / "plobi" / "vault.db")

    roots = migrate_mod.get_roots()
    assert roots["notes"] == home / "notes"
