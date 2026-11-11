"""主模型写向**名册那一行**，不是那唯一一份共享配置。

家目录退休之后（裁 42／166／178 乙案／192）配置只有一份 ⇒「给某个 Agent 单独选
一个模型」的落点只剩**名册那一行的覆盖**（读时叠：名册行 → 顶层 → 出厂默认）。
以前桌面 Models 屏那一条路写的是那唯一一份 `config.yaml` ⇒ **切了 Agent 再换主模型，
所有 Agent 一起换**。

这一档钉的是分流的判据：

  ① 作用域指向名册上的一个 L2 Agent ⇒ 主模型写**名册那一行**，共享那份**一个字不动**；
  ② 没有作用域 ⇒ 写共享那一份（那才是"这台机器的主模型"）；
  ③ 作用域认不出 ⇒ 404，且不落盘；
  ④ 自定义端点（带 base_url / api_key）是**这台机器**的 ⇒ 名册行放不下，整笔仍走共享；
  ⑤ 辅助槽（auxiliary）没有 per-agent 的位置 ⇒ 仍是共享那份。
"""
import pytest
import yaml


def _seed_agent(home, name, role="l2_project"):
    """名册上落一行 Agent —— 身份＝名册那一行（裁 192），不是家目录。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry = AgentRegistry(home / "plobi" / "projects.yaml")
    registry.upsert(AgentEntry(name=name, role=role))
    registry.save()
    return registry


@pytest.fixture
def shared_home(tmp_path, monkeypatch, _isolate_plobi_home):
    """**一份**共享家（配置只有一份）＋ 名册上一行 Agent。"""
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    home.mkdir(parents=True, exist_ok=True)
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


def _cfg(home):
    return yaml.safe_load((home / "config.yaml").read_text()) or {}


def _row(home, name="worker_alpha"):
    from plobi.agents.registry import AgentRegistry

    return AgentRegistry.load(home / "plobi" / "projects.yaml").get(name)


def _post_main(client, **body):
    body.setdefault("scope", "main")
    body.setdefault("confirm_expensive_model", True)
    return client.post("/api/model/set", json=body)


class TestPerAgentMainModel:
    def test_agent_profile_writes_the_roster_row_only(self, client, shared_home):
        """① 作用域指向名册上的 Agent ⇒ 写名册那一行，共享那份一个字不动。"""
        home = shared_home["home"]

        resp = _post_main(
            client,
            provider="openrouter",
            model="test/model-1",
            profile="worker_alpha",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["provider"] == "openrouter"

        row = _row(home)
        assert row.provider == "openrouter"
        assert row.model == "test/model-1"

        # 共享那份**没有**被写
        assert not _cfg(home).get("model")

    def test_without_profile_writes_the_shared_config(self, client, shared_home):
        """② 没有作用域 ⇒ 写共享那一份（那才是"这台机器的主模型"）。"""
        home = shared_home["home"]

        resp = _post_main(client, provider="openrouter", model="test/model-1")
        assert resp.status_code == 200

        model_cfg = _cfg(home).get("model", {})
        assert model_cfg.get("provider") == "openrouter"

        # 名册那一行没被碰
        row = _row(home)
        assert row.provider == ""
        assert row.model == ""

    def test_unknown_profile_is_404_and_writes_nothing(self, client, shared_home):
        """③ 作用域认不出 ⇒ 404，且不落盘。"""
        home = shared_home["home"]
        before = (home / "config.yaml").read_text(encoding="utf-8")

        resp = _post_main(
            client, provider="openrouter", model="test/model-1", profile="ghost"
        )
        assert resp.status_code == 404
        assert (home / "config.yaml").read_text(encoding="utf-8") == before
        assert _row(home).provider == ""

    def test_custom_endpoint_stays_machine_level(self, client, shared_home):
        """④ 自定义端点是**这台机器**的 ⇒ 名册行放不下，整笔仍走共享那一份。

        名册行只放得下 provider + model；base_url / api_key 没有 per-agent 的位置。
        把它们拆成两处写会更糟（共享那份的 provider 与 base_url 会来自两处）。
        """
        home = shared_home["home"]

        resp = _post_main(
            client,
            provider="custom",
            model="llama-3.1-8b",
            base_url="http://127.0.0.1:8000/v1",
            api_key="local-key",
            profile="worker_alpha",
        )
        assert resp.status_code == 200

        model_cfg = _cfg(home).get("model", {})
        assert model_cfg.get("base_url") == "http://127.0.0.1:8000/v1"
        assert _row(home).provider == ""

    def test_auxiliary_scope_is_not_per_agent(self, client, shared_home):
        """⑤ 辅助槽没有 per-agent 的位置 ⇒ 仍是共享那份。"""
        home = shared_home["home"]

        resp = client.post(
            "/api/model/set",
            json={
                "scope": "auxiliary",
                "task": "vision",
                "provider": "openrouter",
                "model": "test/vision-1",
                "confirm_expensive_model": True,
                "profile": "worker_alpha",
            },
        )
        assert resp.status_code == 200

        aux = _cfg(home).get("auxiliary", {})
        assert aux.get("vision", {}).get("model") == "test/vision-1"
        assert _row(home).provider == ""


class TestL1IsNotPerAgent:
    def test_default_scope_stays_shared(self, client, shared_home):
        """`default` 是 L1／全局那一格 ⇒ 它的主模型就是这台机器的主模型。"""
        home = shared_home["home"]

        resp = _post_main(
            client, provider="openrouter", model="test/model-1", profile="default"
        )
        assert resp.status_code == 200

        model_cfg = _cfg(home).get("model", {})
        assert model_cfg.get("provider") == "openrouter"
