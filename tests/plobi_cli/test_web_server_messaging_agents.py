"""Dashboard Channels (messaging) —— 一份 ``.env``，身份仍然是进程级的。

**世界换过一次**（裁 42／166／178 乙案／192 ＋ 用户 10-25「不保留了」）：
家目录退休之后**不再有「一家一个分身」的家**。凭据只有**一份** ``.env``，
「这个 Agent 是谁」是**进程级**的身份（``-p``），不是一份文件。

这一档钉的就是这个形状：

  ① 带 profile 的读写与不带 profile 的读写落在**同一个** ``.env`` ＋ 同一个
     ``config.yaml``；
  ② 带 profile 的读**不许**回落到进程环境（``os.environ``）——那条回落是
     「上一台机器的根凭据被当成这个 Agent 的」的老 bug；
  ③ 作用域认不出 ⇒ 404；**body 里的 profile 比 query 的管用**（不许被 query 悄悄盖掉）。
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


@pytest.fixture
def shared_home(tmp_path, monkeypatch, _isolate_plobi_home):
    """**一份**共享家（``.env`` / ``config.yaml`` 只有一份）＋ 名册上一行 Agent。"""
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=root-token\n", encoding="utf-8")
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
    # The dashboard process's os.environ may carry root-install credentials;
    # make sure the scoped path never falls back to them.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def _telegram(payload):
    return next(p for p in payload["platforms"] if p["id"] == "telegram")


def _env_field(platform, key):
    return next(f for f in platform["env_vars"] if f["key"] == key)


class TestProfileScopedMessagingReads:
    def test_scoped_read_addresses_the_same_env_file(self, client, shared_home):
        """① 带 profile 与不带 profile 读的是**同一个** ``.env``。"""
        home = shared_home["home"]

        scoped = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )
        assert scoped.status_code == 200
        unscoped = client.get("/api/messaging/platforms")
        assert unscoped.status_code == 200

        assert scoped.json()["env_path"] == str(home / ".env")
        assert unscoped.json()["env_path"] == str(home / ".env")

        token = _env_field(_telegram(scoped.json()), "TELEGRAM_BOT_TOKEN")
        assert token["is_set"] is True
        assert _telegram(scoped.json())["configured"] is True

    def test_scoped_read_never_falls_back_to_process_env(
        self, client, shared_home, monkeypatch
    ):
        """② 带 profile 的读**不许**回落到进程环境。

        那条回落是「根凭据被当成这个 Agent 的」的老 bug：屏上于是显示
        「已配置」，其实这台机器上那个 Agent 根本没有凭据。
        """
        home = shared_home["home"]
        (home / ".env").write_text("", encoding="utf-8")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "process-env-token")

        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )
        assert resp.status_code == 200
        telegram = _telegram(resp.json())
        assert _env_field(telegram, "TELEGRAM_BOT_TOKEN")["is_set"] is False
        assert telegram["configured"] is False

    def test_unscoped_read_shows_dashboard_profile_env(self, client, shared_home):
        resp = client.get("/api/messaging/platforms")
        assert resp.status_code == 200
        telegram = _telegram(resp.json())
        token = _env_field(telegram, "TELEGRAM_BOT_TOKEN")
        assert token["is_set"] is True

    def test_unknown_profile_returns_404(self, client, shared_home):
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "no_such_profile"}
        )
        assert resp.status_code == 404

    def test_scoped_read_returns_profile_path_command_and_startup_failure(
        self, client, shared_home, monkeypatch
    ):
        import plobi_cli.web_server as web_server

        home = shared_home["home"]
        (home / ".env").write_text(
            "TELEGRAM_BOT_TOKEN=worker-token\n", encoding="utf-8"
        )
        (home / "config.yaml").write_text(
            yaml.safe_dump({"platforms": {"telegram": {"enabled": True}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(web_server, "get_running_pid", lambda: None)
        monkeypatch.setattr(
            web_server,
            "read_runtime_status",
            lambda: {
                "gateway_state": "startup_failed",
                "exit_reason": "all configured messaging platforms failed to connect",
                "platforms": {},
            },
        )

        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["env_path"] == str(home / ".env")
        assert payload["gateway_start_command"] == (
            "plobi -p worker_alpha gateway start"
        )
        telegram = _telegram(payload)
        assert telegram["state"] == "startup_failed"
        assert telegram["error_code"] == "startup_failed"
        assert telegram["error_message"] == (
            "all configured messaging platforms failed to connect"
        )


class TestProfileScopedMessagingWrites:
    def test_scoped_write_lands_in_the_one_shared_env(self, client, shared_home):
        """① 写也落在**那一份** ``.env`` ＋ 那一份 ``config.yaml``。"""
        home = shared_home["home"]

        resp = client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={
                "enabled": True,
                "env": {"TELEGRAM_BOT_TOKEN": "worker-token"},
            },
        )
        assert resp.status_code == 200

        env_text = (home / ".env").read_text(encoding="utf-8")
        assert "TELEGRAM_BOT_TOKEN=worker-token" in env_text

        cfg = yaml.safe_load((home / "config.yaml").read_text()) or {}
        assert cfg.get("platforms", {}).get("telegram", {}).get("enabled") is True

    def test_body_profile_beats_query_param(self, client, shared_home):
        """③ body 里的 profile 比 query 的管用——**两个方向都要钉**。

        方向①：body 指向一个不存在的作用域 ⇒ 404（哪怕 query 指向一个好的）。
        方向②：body 指向一个好的作用域 ⇒ 200（哪怕 query 指向一个坏的）。
        """
        resp = client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={"env": {"TELEGRAM_BOT_TOKEN": "body-token"}, "profile": "ghost"},
        )
        assert resp.status_code == 404

        resp = client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "ghost"},
            json={
                "env": {"TELEGRAM_BOT_TOKEN": "body-token"},
                "profile": "worker_alpha",
            },
        )
        assert resp.status_code == 200
        env_text = (shared_home["home"] / ".env").read_text(encoding="utf-8")
        assert "TELEGRAM_BOT_TOKEN=body-token" in env_text

    def test_scoped_read_after_scoped_write_round_trips(self, client, shared_home):
        client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={"enabled": True, "env": {"TELEGRAM_BOT_TOKEN": "worker-token"}},
        )
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )
        telegram = _telegram(resp.json())
        assert telegram["enabled"] is True
        assert _env_field(telegram, "TELEGRAM_BOT_TOKEN")["is_set"] is True
        assert telegram["configured"] is True

    def test_scoped_clear_env_removes_from_the_one_shared_env(
        self, client, shared_home
    ):
        home = shared_home["home"]
        client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={"env": {"TELEGRAM_BOT_TOKEN": "worker-token"}},
        )
        resp = client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={"clear_env": ["TELEGRAM_BOT_TOKEN"]},
        )
        assert resp.status_code == 200
        env_text = (home / ".env").read_text(encoding="utf-8")
        assert "worker-token" not in env_text
