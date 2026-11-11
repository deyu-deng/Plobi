"""The machine dashboard's multi-profile unification —— **一份家**，身份是进程级的。

**世界换过一次**（裁 42／166／178 乙案／192 ＋ 用户 10-25「不保留了」）：家目录退休
之后**不再有「一家一个分身」的家**。配置 / 凭据 / MCP 表 / 技能都只有**一份**，
「这个 Agent 是谁」是**名册那一行**（身份）＋ **进程级**的 ``-p``（落点），
不是一份文件。

这一档钉的就是这个形状：

  ① 配置 / 凭据 / MCP 表：带 profile 与不带 profile 落在**同一个**文件；
  ② 读与写**对称**——带 profile 的读看见的就是带 profile 的写写下去的那份；
  ③ 作用域认不出 ⇒ 404，**且一个字都不落盘**；
  ④ 进程级身份还在：spawn 类动作照旧带 ``-p <profile>``；PTY 子进程**不**把家
     钉到分身（裁定 180：一个后端一个库）。

（改这一档之前，它钉的是老世界：每个 profile 一个分身家、写只落分身那份、
读只看见分身那份——那套形状 10-24 搬走、10-27 删净，早就不存在了。）
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
    """**一份**共享家（配置 / .env / MCP 表只有一份）＋ 名册上一行 Agent。"""
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    (home / ".env").write_text("", encoding="utf-8")
    _seed_agent(home, "worker_beta")
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


class TestProfileScopedConfig:
    def test_config_put_with_profile_lands_in_the_one_shared_config(
        self, client, shared_home
    ):
        """① 带 profile 的写落**那一份** config.yaml。"""
        home = shared_home["home"]
        resp = client.put(
            "/api/config",
            json={"config": {"timezone": "Mars/Olympus"}, "profile": "worker_beta"},
        )
        assert resp.status_code == 200
        assert _cfg(home).get("timezone") == "Mars/Olympus"

        # 不带 profile 的读看见的也是它——同一份文件
        unscoped = client.get("/api/config")
        assert unscoped.json().get("timezone") == "Mars/Olympus"

    def test_config_get_with_profile_reads_the_one_shared_config(
        self, client, shared_home
    ):
        """② 读与写对称：带 profile 的读看见的就是那一份。"""
        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "timezone: Venus/Cloud\n", encoding="utf-8"
        )
        resp = client.get("/api/config", params={"profile": "worker_beta"})
        assert resp.status_code == 200
        assert resp.json().get("timezone") == "Venus/Cloud"

        unscoped = client.get("/api/config")
        assert unscoped.json().get("timezone") == "Venus/Cloud"

    def test_config_query_param_equivalent_to_body(self, client, shared_home):
        """The SPA's fetchJSON injects ?profile= — must scope like body.profile."""
        home = shared_home["home"]
        resp = client.put(
            "/api/config?profile=worker_beta",
            json={"config": {"timezone": "Pluto/Far"}},
        )
        assert resp.status_code == 200
        assert _cfg(home).get("timezone") == "Pluto/Far"

    def test_config_raw_round_trip_scoped(self, client, shared_home):
        resp = client.put(
            "/api/config/raw",
            json={"yaml_text": "timezone: Io/Volcano\n", "profile": "worker_beta"},
        )
        assert resp.status_code == 200
        resp = client.get("/api/config/raw", params={"profile": "worker_beta"})
        assert "Io/Volcano" in resp.json()["yaml"]

    def test_config_raw_path_points_at_the_one_shared_config(
        self, client, shared_home
    ):
        """The Config page header shows /api/config/raw's ``path`` — it must
        point at the ONE config.yaml, whichever profile is selected (家退休
        之后只有一份；那个「指向另一个分身的路径」的老 bug 已经没有对应现实）。"""
        home = shared_home["home"]
        resp = client.get("/api/config/raw", params={"profile": "worker_beta"})
        assert resp.status_code == 200
        assert resp.json()["path"] == str(home / "config.yaml")
        resp = client.get("/api/config/raw")
        assert resp.json()["path"] == str(home / "config.yaml")

    def test_unknown_profile_404_and_writes_nothing(self, client, shared_home):
        """③ 认不出的作用域 ⇒ 404，**且一个字都不落盘**。"""
        home = shared_home["home"]
        before = (home / "config.yaml").read_text(encoding="utf-8")

        resp = client.put(
            "/api/config",
            json={"config": {"timezone": "Mars/Olympus"}, "profile": "ghost"},
        )
        assert resp.status_code == 404
        assert (home / "config.yaml").read_text(encoding="utf-8") == before


class TestProfileScopedEnv:
    def test_env_set_lands_in_the_one_shared_env(self, client, shared_home):
        home = shared_home["home"]
        resp = client.put(
            "/api/env",
            json={"key": "FAL_KEY", "value": "test-fal-123", "profile": "worker_beta"},
        )
        assert resp.status_code == 200
        assert "test-fal-123" in (home / ".env").read_text()

    def test_env_list_reads_the_one_shared_env(self, client, shared_home):
        home = shared_home["home"]
        (home / ".env").write_text("FAL_KEY=worker-only-value\n", encoding="utf-8")

        scoped = client.get("/api/env", params={"profile": "worker_beta"})
        assert scoped.status_code == 200
        assert scoped.json()["FAL_KEY"]["is_set"] is True

        unscoped = client.get("/api/env")
        assert unscoped.json()["FAL_KEY"]["is_set"] is True

    def test_env_delete_scoped(self, client, shared_home):
        home = shared_home["home"]
        (home / ".env").write_text("FAL_KEY=doomed\n", encoding="utf-8")
        resp = client.request(
            "DELETE",
            "/api/env",
            json={"key": "FAL_KEY", "profile": "worker_beta"},
        )
        assert resp.status_code == 200
        assert "doomed" not in (home / ".env").read_text()


class TestProfileScopedMcp:
    def test_mcp_add_and_list_land_in_the_one_global_table(
        self, client, shared_home
    ):
        """MCP 服务器是一张**全局表**（家退休之后只有一份）。"""
        home = shared_home["home"]
        resp = client.post(
            "/api/mcp/servers",
            json={"name": "scoped-srv", "url": "http://localhost:1234/sse",
                  "profile": "worker_beta"},
        )
        assert resp.status_code == 200

        assert "scoped-srv" in _cfg(home).get("mcp_servers", {})

        listing = client.get("/api/mcp/servers", params={"profile": "worker_beta"}).json()
        assert any(s["name"] == "scoped-srv" for s in listing["servers"])
        listing = client.get("/api/mcp/servers").json()
        assert any(s["name"] == "scoped-srv" for s in listing["servers"])

    def test_mcp_enabled_toggle_scoped(self, client, shared_home):
        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "mcp_servers:\n  srv1:\n    url: http://x/sse\n", encoding="utf-8"
        )
        resp = client.put(
            "/api/mcp/servers/srv1/enabled",
            json={"enabled": False, "profile": "worker_beta"},
        )
        assert resp.status_code == 200
        assert _cfg(home)["mcp_servers"]["srv1"]["enabled"] is False

    def test_mcp_probe_runs_inside_the_shared_home(
        self, client, shared_home, monkeypatch
    ):
        """The test-server probe must execute with the selected profile's
        scope active so env-placeholder expansion reads the profile's .env,
        matching the config the server was saved into."""
        import plobi_cli.mcp_config as mcp_config
        from plobi_constants import get_plobi_home

        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "mcp_servers:\n  probe-srv:\n    url: http://x/sse\n",
            encoding="utf-8",
        )
        seen = {}

        def fake_probe(name, config, connect_timeout=30, details=None):
            seen["home"] = str(get_plobi_home())
            return [("tool-a", "desc")]

        monkeypatch.setattr(mcp_config, "_probe_single_server", fake_probe)
        resp = client.post(
            "/api/mcp/servers/probe-srv/test", params={"profile": "worker_beta"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert seen["home"] == str(home)

    def test_mcp_test_oauth_server_without_token_is_not_ok(
        self, client, shared_home, monkeypatch
    ):
        """An `auth: oauth` server that serves tools/list anonymously must not
        false-green: a successful probe with no token on disk reports needs-auth."""
        import plobi_cli.mcp_config as mcp_config

        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "mcp_servers:\n  oauth-srv:\n    url: http://x/sse\n    auth: oauth\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            mcp_config,
            "_probe_single_server",
            lambda name, config, connect_timeout=30, details=None: [("tool-a", "desc")],
        )
        monkeypatch.setattr(mcp_config, "_oauth_tokens_present", lambda name: False)

        resp = client.post(
            "/api/mcp/servers/oauth-srv/test", params={"profile": "worker_beta"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "oauth" in body["error"].lower()

        # With a token present, the same probe is genuinely authenticated.
        monkeypatch.setattr(mcp_config, "_oauth_tokens_present", lambda name: True)
        resp = client.post(
            "/api/mcp/servers/oauth-srv/test", params={"profile": "worker_beta"}
        )
        assert resp.json()["ok"] is True

    def test_mcp_remove_scoped(self, client, shared_home):
        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "mcp_servers:\n  srv2:\n    url: http://x/sse\n", encoding="utf-8"
        )
        # 一张全局表 ⇒ 带不带 profile 说的是同一件事（家退休之后没有「分身那张表」）
        resp = client.delete("/api/mcp/servers/srv2", params={"profile": "worker_beta"})
        assert resp.status_code == 200
        assert "srv2" not in _cfg(home).get("mcp_servers", {})


class TestProfileScopedModel:
    def test_model_set_with_profile_lands_in_the_one_shared_config(
        self, client, shared_home
    ):
        home = shared_home["home"]
        resp = client.post(
            "/api/model/set",
            json={
                "scope": "main",
                "provider": "openrouter",
                "model": "test/model-1",
                "confirm_expensive_model": True,
                "profile": "worker_beta",
            },
        )
        assert resp.status_code == 200
        model_cfg = _cfg(home).get("model", {})
        assert isinstance(model_cfg, dict)
        assert model_cfg.get("provider") == "openrouter"

    def test_auxiliary_read_matches_write_in_the_one_shared_config(
        self, client, shared_home
    ):
        """Reads and writes must be symmetric: an aux pin written to the
        config must show up in BOTH the scoped and the unscoped read — they
        are the same file now.
        (Regression: /api/model/auxiliary used to read unscoped while
        /api/model/set wrote scoped — the Models page displayed the
        dashboard profile's pins while editing the selected profile's.)"""
        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "auxiliary:\n  vision:\n    provider: openrouter\n"
            "    model: worker/vision-pin\n",
            encoding="utf-8",
        )
        resp = client.get("/api/model/auxiliary", params={"profile": "worker_beta"})
        assert resp.status_code == 200
        vision = next(t for t in resp.json()["tasks"] if t["task"] == "vision")
        assert vision["model"] == "worker/vision-pin"

        # Unscoped read = the same one file.
        resp = client.get("/api/model/auxiliary")
        assert resp.status_code == 200
        vision = next(t for t in resp.json()["tasks"] if t["task"] == "vision")
        assert vision["model"] == "worker/vision-pin"

    def test_auxiliary_unknown_profile_404(self, client, shared_home):
        resp = client.get("/api/model/auxiliary", params={"profile": "ghost"})
        assert resp.status_code == 404

    def test_model_options_with_profile_reads_the_one_shared_config(
        self, client, shared_home
    ):
        """The Models picker must read the SAME config /api/model/set writes —
        current model/provider in the payload come from that config."""
        home = shared_home["home"]
        (home / "config.yaml").write_text(
            "model:\n  provider: openrouter\n  default: worker/current-pin\n",
            encoding="utf-8",
        )
        resp = client.get("/api/model/options", params={"profile": "worker_beta"})
        assert resp.status_code == 200
        body = resp.json()
        assert "worker/current-pin" in resp.text
        resp = client.get("/api/model/options")
        assert resp.status_code == 200
        assert "worker/current-pin" in resp.text
        assert isinstance(body, dict)

    def test_model_options_unknown_profile_404(self, client, shared_home):
        resp = client.get("/api/model/options", params={"profile": "ghost"})
        assert resp.status_code == 404

    def test_model_options_hides_unconfigured_providers_by_default(
        self, client, monkeypatch
    ):
        calls = []

        monkeypatch.setattr(
            "plobi_cli.inventory.load_picker_context",
            lambda: object(),
        )

        def _fake_build_models_payload(_ctx, **kwargs):
            calls.append(kwargs)
            return {"providers": [], "model": "", "provider": ""}

        monkeypatch.setattr(
            "plobi_cli.inventory.build_models_payload",
            _fake_build_models_payload,
        )

        resp = client.get("/api/model/options")
        assert resp.status_code == 200
        assert calls[-1]["explicit_only"] is False
        assert calls[-1]["include_unconfigured"] is False

        resp = client.get("/api/model/options", params={"explicit_only": "1"})
        assert resp.status_code == 200
        assert calls[-1]["explicit_only"] is True

        resp = client.get("/api/model/options", params={"include_unconfigured": "1"})
        assert resp.status_code == 200
        assert calls[-1]["include_unconfigured"] is True

    def test_model_info_unknown_profile_404(self, client, shared_home):
        """Regression: the broad except used to convert the 404 into a 200
        with empty model info ("no model set" — silently wrong)."""
        resp = client.get("/api/model/info", params={"profile": "ghost"})
        assert resp.status_code == 404

    def test_mcp_catalog_unknown_profile_404(self, client, shared_home):
        resp = client.get("/api/mcp/catalog", params={"profile": "ghost"})
        assert resp.status_code == 404


class TestProfileScopedPostSetup:
    def test_post_setup_spawns_with_profile_flag(
        self, client, shared_home, monkeypatch
    ):
        """Post-setup runs in a -p scoped subprocess so hooks that read
        config / write per-profile state see the same PLOBI_HOME the rest
        of the drawer's writes targeted."""
        import plobi_cli.web_server as web_server

        calls = []

        class _FakeProc:
            pid = 777

        monkeypatch.setattr(
            web_server,
            "_spawn_plobi_action",
            lambda subcommand, name: calls.append(list(subcommand)) or _FakeProc(),
        )
        monkeypatch.setattr(
            "plobi_cli.tools_config.valid_post_setup_keys",
            lambda: {"agent_browser"},
        )
        resp = client.post(
            "/api/tools/toolsets/browser/post-setup",
            json={"key": "agent_browser", "profile": "worker_beta"},
        )
        assert resp.status_code == 200
        assert calls == [
            ["-p", "worker_beta", "tools", "post-setup", "agent_browser"]
        ]

    def test_post_setup_without_profile_keeps_legacy_argv(
        self, client, shared_home, monkeypatch
    ):
        import plobi_cli.web_server as web_server

        calls = []

        class _FakeProc:
            pid = 777

        monkeypatch.setattr(
            web_server,
            "_spawn_plobi_action",
            lambda subcommand, name: calls.append(list(subcommand)) or _FakeProc(),
        )
        monkeypatch.setattr(
            "plobi_cli.tools_config.valid_post_setup_keys",
            lambda: {"agent_browser"},
        )
        resp = client.post(
            "/api/tools/toolsets/browser/post-setup",
            json={"key": "agent_browser"},
        )
        assert resp.status_code == 200
        assert calls == [["tools", "post-setup", "agent_browser"]]


class TestProfileScopedGateway:
    def test_lifecycle_spawns_with_profile_flag(
        self, client, shared_home, monkeypatch
    ):
        import plobi_cli.web_server as web_server

        calls = []

        class _FakeProc:
            pid = 888

        monkeypatch.setattr(
            web_server,
            "_spawn_plobi_action",
            lambda subcommand, name: calls.append((list(subcommand), name)) or _FakeProc(),
        )
        web_server._ACTION_PROCS.pop("gateway-restart", None)
        web_server._ACTION_COMMANDS.pop("gateway-restart", None)

        for verb in ("start", "stop", "restart"):
            resp = client.post(f"/api/gateway/{verb}", params={"profile": "worker_beta"})
            assert resp.status_code == 200

        assert calls == [
            (["-p", "worker_beta", "gateway", "start"], "gateway-start"),
            (["-p", "worker_beta", "gateway", "stop"], "gateway-stop"),
            (["-p", "worker_beta", "gateway", "restart"], "gateway-restart"),
        ]

    def test_status_reads_the_one_shared_home(
        self, client, shared_home, monkeypatch
    ):
        import plobi_cli.web_server as web_server
        from plobi_constants import get_plobi_home

        home = shared_home["home"]
        seen_homes = []

        def fake_get_running_pid():
            seen_homes.append(str(get_plobi_home()))
            return None

        monkeypatch.setattr(web_server, "check_config_version", lambda: (1, 1))
        # get_status probes via the TTL-cached wrapper (PR #53511 salvage);
        # patch the cached name so the fake still intercepts the probe.
        monkeypatch.setattr(web_server, "get_running_pid_cached", fake_get_running_pid)
        monkeypatch.setattr(
            web_server,
            "read_runtime_status",
            lambda: {"gateway_state": "startup_failed", "platforms": {}},
        )
        monkeypatch.setattr(web_server, "_GATEWAY_HEALTH_URL", None)

        resp = client.get("/api/status", params={"profile": "worker_beta"})

        assert resp.status_code == 200
        assert seen_homes[0] == str(home)
        assert resp.json()["plobi_home"] == str(home)

    def test_status_uses_runtime_pid_when_profile_pid_file_is_missing(
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
        runtime = {
            "pid": 4242,
            "gateway_state": "running",
            "platforms": {"telegram": {"state": "connected"}},
            "exit_reason": None,
            "updated_at": "2026-06-17T00:00:00+00:00",
        }
        monkeypatch.setattr(web_server, "check_config_version", lambda: (1, 1))
        monkeypatch.setattr(web_server, "get_running_pid_cached", lambda: None)
        monkeypatch.setattr(web_server, "read_runtime_status", lambda: runtime)
        monkeypatch.setattr(
            web_server, "get_runtime_status_running_pid", lambda payload: 4242
        )
        monkeypatch.setattr(web_server, "_GATEWAY_HEALTH_URL", None)
        from gateway.config import Platform

        class _FakeGatewayConfig:
            def get_connected_platforms(self):
                return [Platform.TELEGRAM]

        monkeypatch.setattr(
            "gateway.config.load_gateway_config", lambda: _FakeGatewayConfig()
        )

        resp = client.get("/api/status", params={"profile": "worker_beta"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["gateway_running"] is True
        assert data["gateway_pid"] == 4242
        assert data["gateway_state"] == "running"
        assert data["gateway_platforms"] == {"telegram": {"state": "connected"}}


class TestProfileScopedTelegramOnboarding:
    def test_apply_writes_the_one_shared_env_and_restarts_the_target(
        self, client, shared_home, monkeypatch
    ):
        import time
        import plobi_cli.web_server as web_server

        home = shared_home["home"]
        with web_server._telegram_onboarding_lock:
            web_server._telegram_onboarding_pairings.clear()
            web_server._telegram_onboarding_pairings["pair-worker"] = (
                web_server._TelegramOnboardingPairing(
                    poll_token="poll-secret",
                    expires_at="2027-05-18T00:00:00.000Z",
                    expires_at_ts=time.time() + 600,
                    bot_token="123456:SECRET",
                    bot_username="worker_bot",
                    owner_user_id="123456789",
                )
            )

        calls = []

        class _FakeProc:
            pid = 889

        monkeypatch.setattr(
            web_server,
            "_spawn_plobi_action",
            lambda subcommand, name: calls.append((list(subcommand), name)) or _FakeProc(),
        )
        web_server._ACTION_PROCS.pop("gateway-restart", None)
        web_server._ACTION_COMMANDS.pop("gateway-restart", None)

        resp = client.post(
            "/api/messaging/telegram/onboarding/pair-worker/apply",
            params={"profile": "worker_beta"},
            json={"allowed_user_ids": ["123456789"]},
        )

        assert resp.status_code == 200
        assert resp.json()["restart_started"] is True
        # 身份是**进程级**的：重启照旧带 -p
        assert calls == [
            (["-p", "worker_beta", "gateway", "restart"], "gateway-restart")
        ]

        env_text = (home / ".env").read_text()
        assert "TELEGRAM_BOT_TOKEN=123456:SECRET" in env_text
        assert "TELEGRAM_ALLOWED_USERS=123456789" in env_text

        cfg = _cfg(home)
        assert cfg["platforms"]["telegram"]["enabled"] is True


class TestProfileScopedChatPty:
    def test_chat_argv_no_longer_pins_plobi_home(self, shared_home, monkeypatch):
        """裁定 180：一个后端一个库——PTY 子进程的家不再钉到那个分身的家。

        「这个聊天属于哪个 Agent」是**请求属性**（RPC 的 profile →
        session.profile_home → 行级 ``sessions.agent_id``），不是进程属性；
        配置面按 contextvar 逐 turn rebind。钉家会把会话写回各家库，读侧
        （单库 WHERE agent_id）就再也看不见它。
        """
        import plobi_cli.web_server as web_server

        monkeypatch.setattr(
            "plobi_cli.main._make_tui_argv",
            lambda root, tui_dev=False: (["cat"], None),
            raising=False,
        )
        argv, cwd, env = web_server._resolve_chat_argv(profile="worker_beta")
        assert env is not None
        # 家只有一份 ⇒ 带 profile 与不带 profile 的 PTY 子进程落在**同一个**家
        unscoped_env = web_server._resolve_chat_argv()[2]
        assert env.get("PLOBI_HOME") == unscoped_env.get("PLOBI_HOME")

    def test_chat_argv_profile_attaches_in_process_gateway(
        self, shared_home, monkeypatch
    ):
        """单后端后一个网关：profile 聊天也 attach dashboard 的 in-process 网关。

        旧行为「scoped chat 自起 tui_gateway 子进程」存在的唯一理由是各跑各家的
        库；单库后那个理由不成立，留下它只会多一个进程。
        """
        import plobi_cli.web_server as web_server

        monkeypatch.setattr(
            "plobi_cli.main._make_tui_argv",
            lambda root, tui_dev=False: (["cat"], None),
            raising=False,
        )
        argv, cwd, env = web_server._resolve_chat_argv(profile="worker_beta")
        assert env is not None

        unscoped_argv, _cwd, unscoped_env = web_server._resolve_chat_argv()
        # 有没有 URL 取决于本机网关是否在跑；判据是「scoped 与 unscoped 同等待遇」。
        assert ("PLOBI_TUI_GATEWAY_URL" in env) == (
            "PLOBI_TUI_GATEWAY_URL" in unscoped_env
        )
        if "PLOBI_TUI_GATEWAY_URL" in unscoped_env:
            assert env["PLOBI_TUI_GATEWAY_URL"] == unscoped_env["PLOBI_TUI_GATEWAY_URL"]

    def test_chat_argv_unscoped_keeps_legacy_env(self, shared_home, monkeypatch):
        import plobi_cli.web_server as web_server

        monkeypatch.setattr(
            "plobi_cli.main._make_tui_argv",
            lambda root, tui_dev=False: (["cat"], None),
            raising=False,
        )
        argv, cwd, env = web_server._resolve_chat_argv()
        assert env is not None
        scoped_env = web_server._resolve_chat_argv(profile="worker_beta")[2]
        assert env.get("PLOBI_HOME") == scoped_env.get("PLOBI_HOME")

    def test_chat_argv_unknown_profile_raises(self, shared_home, monkeypatch):
        import plobi_cli.web_server as web_server

        monkeypatch.setattr(
            "plobi_cli.main._make_tui_argv",
            lambda root, tui_dev=False: (["cat"], None),
            raising=False,
        )
        # Reuse the HTTPException class web_server itself raises — avoids a
        # direct fastapi import (unresolvable in the ty lint environment).
        with pytest.raises(web_server.HTTPException) as exc:
            web_server._resolve_chat_argv(profile="ghost")
        assert exc.value.status_code == 404
