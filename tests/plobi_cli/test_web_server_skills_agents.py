"""Dashboard skills / toolsets —— 一张全局名单 ＋ 每 Agent 一份黑名单。

**这一族的世界换过一次**（裁 42／166／178 乙案／192 ＋ 用户 10-25「不保留了」）：
家目录退休之后**不再有「一家一个分身」的家**。技能资产只有**一张全局名单**
（顶层家那一份 ``skills/``），「这个 Agent 关掉了哪些」落在**名册那一行的黑名单**
（``AgentEntry.disabled_skills`` / ``disabled_toolsets``）——全局表一个字不动，
行黑名单只做减法（2026-10-19 起的形状；见 ``web_server._agent_tool_row``）。

这一档钉的就是这个形状：

  ① 名单是**全局**的：带 profile 的读与不带 profile 的读看到**同一批技能**；
  ② 开关是**每 Agent** 的：带 profile 的写落**名册那一行**，全局表不动；
  ③ 作用域认不出 ⇒ 404，且**不落盘**；
  ④ 作用域用完，模块全局变量必须还原。

（改这一档之前，它钉的是老世界：每个 profile 一个分身家、技能清单各看各的、
开关只改分身那一份——那套形状 10-24 搬走、10-27 删净，早就不存在了。）
"""
import pytest
import yaml


def _seed_agent(home, name):
    """名册上落一行 Agent —— 身份＝名册那一行（裁 192），不是家目录。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry = AgentRegistry(home / "plobi" / "projects.yaml")
    registry.upsert(AgentEntry(name=name, role="l2_project"))
    registry.save()
    return registry


def _roster_entry(home, name):
    """名册上那一行（重新读盘，不拿缓存）。"""
    from plobi.agents.registry import AgentRegistry

    return AgentRegistry.load(home / "plobi" / "projects.yaml").get(name)


def _write_skill(skills_dir, name, description="test skill"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


@pytest.fixture
def shared_home(tmp_path, monkeypatch, _isolate_plobi_home):
    """**一份**共享家（配置 / 技能只有一份）＋ 名册上一行 Agent。"""
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    (home / "skills").mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    _seed_agent(home, "worker_alpha")
    return {"home": home}


@pytest.fixture
def client(monkeypatch, shared_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import plobi_state
    from plobi_constants import get_plobi_home
    from plobi_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", get_plobi_home() / "state.db")
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def _load_cfg(home):
    return yaml.safe_load((home / "config.yaml").read_text()) or {}


def _skills_by_name(payload):
    return {s["name"]: s for s in payload}


class TestSkillsRead:
    def test_skills_list_with_profile_is_the_same_global_list(
        self, client, shared_home
    ):
        """① 名单是全局的：带 profile 不换名单。"""
        home = shared_home["home"]
        _write_skill(home / "skills", "dashboard-skill")
        _write_skill(home / "skills", "worker-skill")

        scoped = client.get("/api/skills", params={"profile": "worker_alpha"})
        assert scoped.status_code == 200
        unscoped = client.get("/api/skills")
        assert unscoped.status_code == 200

        assert set(_skills_by_name(scoped.json())) == set(
            _skills_by_name(unscoped.json())
        )
        assert "worker-skill" in _skills_by_name(scoped.json())

    def test_skills_list_without_profile_uses_dashboard_home(
        self, client, shared_home
    ):
        home = shared_home["home"]
        _write_skill(home / "skills", "dashboard-skill")

        resp = client.get("/api/skills")
        assert resp.status_code == 200
        names = {s["name"] for s in resp.json()}
        assert "dashboard-skill" in names


class TestSkillsToggle:
    def test_toggle_writes_the_agent_roster_row_only(
        self, client, shared_home
    ):
        """② 开关是每 Agent 的：落名册那一行，**全局表一个字不动**。"""
        home = shared_home["home"]
        _write_skill(home / "skills", "worker-skill")

        resp = client.put(
            "/api/skills/toggle",
            json={"name": "worker-skill", "enabled": False, "profile": "worker_alpha"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "name": "worker-skill", "enabled": False}

        # 名册那一行记着这一项
        entry = _roster_entry(home, "worker_alpha")
        assert "worker-skill" in [s.casefold() for s in entry.disabled_skills]

        # 全局表没被碰
        global_cfg = _load_cfg(home)
        assert "worker-skill" not in (global_cfg.get("skills", {}) or {}).get(
            "disabled", []
        )

        # 读面：带这个 Agent 时它是关的，不带时它照旧开着
        scoped = _skills_by_name(
            client.get("/api/skills", params={"profile": "worker_alpha"}).json()
        )
        assert scoped["worker-skill"]["enabled"] is False
        unscoped = _skills_by_name(client.get("/api/skills").json())
        assert unscoped["worker-skill"]["enabled"] is True

    def test_toggle_reenable_round_trip(self, client, shared_home):
        home = shared_home["home"]
        _write_skill(home / "skills", "worker-skill")

        for enabled in (False, True):
            resp = client.put(
                "/api/skills/toggle",
                json={
                    "name": "worker-skill",
                    "enabled": enabled,
                    "profile": "worker_alpha",
                },
            )
            assert resp.status_code == 200

        entry = _roster_entry(home, "worker_alpha")
        assert "worker-skill" not in [s.casefold() for s in entry.disabled_skills]

    def test_unknown_profile_returns_404_and_writes_nothing(
        self, client, shared_home
    ):
        """③ 认不出的作用域 ⇒ 404，**且一个字都不落盘**。"""
        home = shared_home["home"]
        _write_skill(home / "skills", "worker-skill")
        before = _load_cfg(home)

        resp = client.put(
            "/api/skills/toggle",
            json={"name": "worker-skill", "enabled": False, "profile": "ghost"},
        )
        assert resp.status_code == 404
        assert _load_cfg(home) == before

    def test_invalid_profile_name_returns_400(self, client, shared_home):
        resp = client.put(
            "/api/skills/toggle",
            json={"name": "worker-skill", "enabled": False, "profile": "Bad Name!"},
        )
        assert resp.status_code == 400

    def test_scope_restores_module_globals(self, client, shared_home):
        """④ 作用域用完，模块全局变量必须还原。"""
        import tools.skills_tool as skills_tool

        before = skills_tool.SKILLS_DIR
        client.get("/api/skills", params={"profile": "worker_alpha"})
        assert skills_tool.SKILLS_DIR == before


class TestToolsetToggle:
    def test_toolset_toggle_writes_the_agent_roster_row_only(
        self, client, shared_home
    ):
        """② 的工具集那一面：全局白名单不动，翻的是这个 Agent 的黑名单。"""
        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "platform_toolsets:\n  cli:\n    - x_search\n", encoding="utf-8"
        )

        resp = client.put(
            "/api/tools/toolsets/x_search",
            json={"enabled": False, "profile": "worker_alpha"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "name": "x_search", "enabled": False}

        entry = _roster_entry(home, "worker_alpha")
        assert "x_search" in [t.casefold() for t in entry.disabled_toolsets]

        # 全局白名单没被碰
        global_cfg = _load_cfg(home)
        assert "x_search" in (global_cfg.get("platform_toolsets", {}) or {}).get(
            "cli", []
        )

        scoped = {
            t["name"]: t
            for t in client.get(
                "/api/tools/toolsets", params={"profile": "worker_alpha"}
            ).json()
        }
        assert scoped["x_search"]["enabled"] is False
        unscoped = {
            t["name"]: t for t in client.get("/api/tools/toolsets").json()
        }
        assert unscoped["x_search"]["enabled"] is True

    def test_toolset_toggle_unknown_profile_404(self, client, shared_home):
        resp = client.put(
            "/api/tools/toolsets/x_search",
            json={"enabled": True, "profile": "ghost"},
        )
        assert resp.status_code == 404


class TestProfileScopedHubActions:
    def test_hub_install_spawns_with_profile_flag(
        self, client, shared_home, monkeypatch
    ):
        """Hub 装技能走子进程——身份是**进程级**的（``-p``），不受家退休影响。"""
        import plobi_cli.web_server as web_server

        calls = []

        class _FakeProc:
            pid = 4242

        def _fake_spawn(subcommand, name):
            calls.append((list(subcommand), name))
            return _FakeProc()

        monkeypatch.setattr(web_server, "_spawn_plobi_action", _fake_spawn)
        resp = client.post(
            "/api/skills/hub/install",
            json={"identifier": "official/demo", "profile": "worker_alpha"},
        )
        assert resp.status_code == 200
        assert calls == [
            (
                ["-p", "worker_alpha", "skills", "install", "official/demo", "--yes"],
                web_server._hub_action_name("install", "official/demo"),
            )
        ]

    def test_hub_install_without_profile_keeps_legacy_argv(
        self, client, shared_home, monkeypatch
    ):
        import plobi_cli.web_server as web_server

        calls = []

        class _FakeProc:
            pid = 4242

        monkeypatch.setattr(
            web_server,
            "_spawn_plobi_action",
            lambda subcommand, name: calls.append(list(subcommand)) or _FakeProc(),
        )
        resp = client.post(
            "/api/skills/hub/install", json={"identifier": "official/demo"}
        )
        assert resp.status_code == 200
        assert calls == [["skills", "install", "official/demo", "--yes"]]

    def test_hub_install_unknown_profile_404(self, client, shared_home):
        resp = client.post(
            "/api/skills/hub/install",
            json={"identifier": "official/demo", "profile": "ghost"},
        )
        assert resp.status_code == 404
