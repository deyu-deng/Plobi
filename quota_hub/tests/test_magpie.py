"""Offline contracts for the Magpie leg.

magpie is somebody else's project (MIT, github.com/yetone/magpie): a local
multi-model gateway that keeps the vendor subscriptions behind one
OpenAI-compatible endpoint and serves most of them through community provider
plugins. This leg only has to do three things, and each is pinned here:

1. find the gateway and know it is there (`GET /api/hello` says `magpie`);
2. publish **their** roster with **their** per-model numbers — the window, the
   thinking levels, the output cap — because that is the whole point of reading
   a live catalogue instead of writing one down ourselves;
3. send the turn under our own key name, so the calls are counted as ours.

No network, no magpie installed: the transport is an httpx mock and the hello
probe is answered by the leg's own seam.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from quota_hub.providers.apps.magpie import MagpieProvider, MagpieProviderLeg, their_list


def _provider(http, **extra):
    config = {
        "timeout": 30,
        **extra,
    }
    return MagpieProvider(config, http)


@pytest.fixture(autouse=True)
def no_local_settings(tmp_path, monkeypatch):
    """Nobody's magpie settings on this machine — the port is ours to set."""
    settings = tmp_path / "magpie" / "settings.json"
    monkeypatch.setattr(MagpieProvider, "_settings_path", lambda self: settings)
    return settings


def _models_payload() -> dict:
    """A slice of magpie's own catalogue, in the shape their docs describe."""
    return {
        "object": "list",
        "data": [
            {
                "id": "opencode-zen-free/big-pickle",
                "display_name": "Big Pickle",
                "owned_by": "opencode-zen-free",
                "context_window": 200000,
                "max_output_tokens": 32000,
                "reasoning": {"effort": "high"},
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
            },
            {
                "id": "workbuddy/hy4free",
                "display_name": "Hy4",
                "owned_by": "workbuddy",
                "context_window": 960000,
                "max_output_tokens": 64000,
            },
            {"display_name": "a row with no id cannot be routed"},
        ],
    }


def _http(routes: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            return routes[path]
        return httpx.Response(404, json={"error": "no route"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _with_gateway(prov, *, live=True):
    """Answer the hello probe the way the gateway would."""
    prov._hello_body = lambda: ({"name": "magpie", "version": "0.1.0"} if live else None)
    return prov


# --- is it there -----------------------------------------------------------


def test_magpie_is_not_a_card_the_user_configures():
    """The Providers screen lists what *he* signs in to; magpie is behind it.

    User ruling (11-09 20:0x): 「不要这样弄。我说了前端还是用我们自己的操作逻辑。
    要配置 workbuddy 就要在相应的卡片点击加号配置的」 — so WorkBuddy keeps its own
    card and its own "+", and magpie (which ships with us) must not appear as
    one more thing to configure.
    """
    from quota_hub.registry import ADAPTED_APPS

    prov = _with_gateway(_provider(_http({})))
    assert prov.present() is False, "a leg that is not a product must not become a card"
    assert "magpie" not in ADAPTED_APPS, "the card list is the table of products"


def test_a_gateway_that_answers_is_live():
    prov = _with_gateway(_provider(_http({})))
    assert prov.credential_health() == (True, "ok")


def test_a_gateway_that_is_not_there_says_so_with_a_reason_the_screen_owns():
    """The reason must be one the four locales already know how to say.

    A machine word we invent would reach the card's tooltip verbatim.
    """
    prov = _with_gateway(_provider(_http({})), live=False)
    live, reason = prov.credential_health()
    assert live is False
    assert reason == "no_desktop_credential"


def test_something_else_on_that_port_is_not_magpie():
    prov = _provider(_http({}))
    prov._hello_body = lambda: {"name": "something-else"}
    assert prov.credential_health() == (False, "no_desktop_credential")


# --- where it is -----------------------------------------------------------


def test_the_port_comes_from_magpies_own_settings(no_local_settings):
    no_local_settings.parent.mkdir(parents=True, exist_ok=True)
    no_local_settings.write_text(json.dumps({"port": 4321}), encoding="utf-8")
    prov = _provider(_http({}))
    assert prov._base_url() == "http://127.0.0.1:4321"


def test_a_missing_settings_file_falls_back_to_their_default_port(no_local_settings):
    prov = _provider(_http({}))
    assert prov._base_url() == "http://127.0.0.1:3425"


# --- the roster ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_roster_is_theirs_and_it_carries_their_own_numbers():
    http = _http({"/v1/models": httpx.Response(200, json=_models_payload())})
    prov = _with_gateway(_provider(http))

    accounts = await prov.discover_accounts()
    assert len(accounts) == 1, "the gateway needs no credential of ours"

    got = await prov.refresh_models(accounts[0])
    # The umbrella publishes nothing: every row belongs to the provider leg for
    # its own vendor, so the same models are never listed twice under two names.
    assert prov.served_models == (), prov.served_models
    assert got is None, "the umbrella is a reader, not a leg"

    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        prov.http,
        {"id": "opencode-zen-free", "name": "OpenCode Zen Free"},
        umbrella=prov,
    )
    served = await leg.refresh_models(accounts[0])
    assert served == ["opencode-zen-free/big-pickle"], served
    assert leg.served_models == tuple(served)

    big_pickle = leg.tuning("opencode-zen-free/big-pickle")
    assert big_pickle["context"]["choices"] == [200000], big_pickle
    assert big_pickle["context"]["max"] == 200000
    assert big_pickle["max_output"] == 32000
    assert big_pickle["efforts"] == ["low", "high"], big_pickle
    assert big_pickle["default_effort"] in big_pickle["efforts"]

    wb_leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        prov.http,
        {"id": "workbuddy", "name": "WorkBuddy"},
        umbrella=prov,
    )
    await wb_leg.refresh_models(accounts[0])
    hy4 = wb_leg.tuning("workbuddy/hy4free")
    assert hy4["context"]["choices"] == [960000], hy4
    assert "efforts" not in hy4, "a model that says nothing keeps the generic controls"


@pytest.mark.asyncio
async def test_a_gateway_that_cannot_be_read_keeps_what_we_had():
    http = _http({"/v1/models": httpx.Response(500, json={"error": "boom"})})
    prov = _with_gateway(_provider(http))
    accounts = await prov.discover_accounts()

    assert await prov.refresh_models(accounts[0]) is None
    assert prov.served_models == (), "a failed read must not invent a roster"


# --- the turn --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_turn_goes_out_under_our_own_key_name():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "id": "chatcmpl-x",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                         "finish_reason": "stop"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        prov = _with_gateway(_provider(http))
        accounts = await prov.discover_accounts()
        out = await prov.chat(accounts[0], {
            "model": "magpie/opencode-zen-free/big-pickle",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        })

    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["auth"] == "Bearer magpie-plobi", "our calls are counted under our own name"
    assert seen["body"]["model"] == "opencode-zen-free/big-pickle", "their id, not ours"
    assert seen["body"]["stream"] is False
    assert seen["body"]["tools"], "the tool list has to reach the gateway"
    assert out["model"] == "magpie/opencode-zen-free/big-pickle"
    assert out["choices"][0]["message"]["content"] == "hi"

# --- our table is the truth about the cards --------------------------------
#
# magpie ships several releases a day and its `plugin list --json` is an
# internal shape. If that JSON decided the cards, a rename on their side would
# rename our cards — and our model ids with them (the user's presets and
# sessions hold those ids). So: our table decides, their CLI only answers
# "does this provider still exist".


def test_the_login_command_gets_a_real_terminal():
    """（这一条已被「点名方法」取代：magpie 的菜单不该出现，也就不需要终端窗口。）

    留着的理由是**记下那条走错的路**：2026-11-10 先试的是「给它一个真终端」
    （`tty`），那确实让菜单画出来了，可菜单在**另一个窗口**里，用户看不见 ⇒
    卡片照样一直转圈。真正的解是下面那条：自己点名方法，菜单压根不出现。
    """
    prov = _provider(_http({}))
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        prov.http,
        {"id": "workbuddy", "upstream": "workbuddy", "name": "WorkBuddy"},
        umbrella=prov,
    )
    plan = leg.login_plan()

    # 没有 magpie 的表（他们那份读不到）时退回「给个终端」——那是唯一还能走的路。
    assert plan["argv"][:3] == ["plugin", "login", "workbuddy"]
    assert plan.get("tty") is True, "读不到他们那张表 ⇒ 退回真终端，别让登录无声无息地死掉"


def test_the_login_names_the_method_so_no_menu_appears(tmp_path, monkeypatch):
    """magpie 的登录不给方法就会在终端上问菜单——被管道接走时它一个字都不吐、干等。

    实测 2026-11-10 17:0x：卡片一直转圈，会话 `failed`，`detail` 是菜单尾巴
    「3. WorkBuddy API key；>」。magpie 的 `plugin login <provider> [method]`
    接受 1 基序号／标签／类型 ⇒ 我们自己从它那张表里点名「浏览器登录」那一个。
    """
    magpie_dir = tmp_path / "magpie"
    magpie_dir.mkdir(parents=True, exist_ok=True)
    (magpie_dir / "plugin-providers.json").write_text(
        json.dumps([
            {
                "id": "workbuddy",
                "methods": [
                    {"type": "oauth", "label": "WorkBuddy account (browser)"},
                    {"type": "oauth", "label": "WorkBuddy desktop's sign-in"},
                    {"type": "api", "label": "WorkBuddy API key"},
                ],
            }
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    prov = _provider(_http({}))
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        prov.http,
        {"id": "workbuddy", "upstream": "workbuddy", "name": "WorkBuddy"},
        umbrella=prov,
    )
    plan = leg.login_plan()

    assert plan["argv"] == ["plugin", "login", "workbuddy", "WorkBuddy account (browser)"]
    assert plan.get("tty") is False, "点了方法名就不该再开终端窗口（菜单不会再出现）"


def test_a_provider_with_only_a_key_sign_in_keeps_the_terminal(tmp_path, monkeypatch):
    """只有一个「填 key」的方法时，键要人敲 ⇒ 终端还是得给。"""
    magpie_dir = tmp_path / "magpie"
    magpie_dir.mkdir(parents=True, exist_ok=True)
    (magpie_dir / "plugin-providers.json").write_text(
        json.dumps([
            {"id": "opencode-zen-free", "methods": [{"type": "api", "label": "Zen 免费访问（密钥填 public）"}]}
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    prov = _provider(_http({}))
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        prov.http,
        {"id": "opencode-zen-free", "upstream": "opencode-zen-free"},
        umbrella=prov,
    )
    plan = leg.login_plan()

    assert plan["argv"] == ["plugin", "login", "opencode-zen-free"]
    assert plan.get("tty") is True, "键要人敲 ⇒ 得有终端"


def test_the_card_list_is_our_table_not_their_json():
    prov = _provider(_http({}))
    prov._their_provider_ids = lambda: {"workbuddy", "opencode-zen-free", "brand-new-one"}
    specs = asyncio.run(prov.provider_specs())

    ids = [s["id"] for s in specs]
    assert "workbuddy" in ids and "opencode-zen-free" in ids, ids
    assert "brand-new-one" not in ids, "a provider we have not claimed gets no card"

    names = {s["id"]: s["name"] for s in specs}
    assert names["workbuddy"] == "WorkBuddy"
    assert names["workbuddy-ai"] == "WorkBuddy AI", "国际版与国内版是两张卡"


def test_a_provider_we_have_not_claimed_is_reported_but_not_offered():
    prov = _provider(_http({}))
    prov._their_provider_ids = lambda: {"workbuddy", "brand-new-one"}
    unclaimed = prov.unclaimed_providers()
    assert [u["id"] for u in unclaimed] == ["brand-new-one"], unclaimed


def test_our_model_ids_survive_their_rename():
    """Their provider id is a wire detail; ours is what the user's presets hold."""
    prov = _with_gateway(_provider(_http({})))
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        prov.http,
        {"id": "workbuddy", "upstream": "workbuddy-v2", "name": "WorkBuddy"},
        umbrella=prov,
    )
    assert leg._map_model("workbuddy/hy4free") == "workbuddy-v2/hy4free"
    assert leg.provider_of("workbuddy/hy4free") == "workbuddy"


def test_the_cards_exist_even_when_magpie_is_not_running():
    """Magpie missing is a reason we can say out loud — not a missing card."""
    prov = _provider(_http({}))
    prov._hello_body = lambda: None
    prov._their_provider_ids = lambda: None
    specs = asyncio.run(prov.provider_specs())
    assert specs, "our table does not depend on them being up"

    leg = MagpieProviderLeg({"base_url": "http://127.0.0.1:3425"}, prov.http, specs[0], umbrella=prov)
    signed, reason = leg.signed_in()
    assert signed is False
    assert "Magpie" in reason, reason


# --- their roster ---------------------------------------------------------
#
# magpie has **no** HTTP endpoint for its provider list (that was measured:
# /api/providers, /v1/providers, /api/plugins all answer 404), and
# `plugin list --json` answers `providers: null` unless their plugin host is
# up. So the roster is read from their own files: the host's table on disk
# first, their printed table as the fallback.


def test_their_roster_is_read_from_their_own_table(tmp_path, monkeypatch):
    """The plugin host writes the roster next to the rest of its data."""
    data = tmp_path / "magpie"
    data.mkdir(parents=True, exist_ok=True)
    (data / "plugin-providers.json").write_text(
        json.dumps([
            {"id": "workbuddy", "name": "WorkBuddy", "signedIn": False},
            {"id": "opencode-zen-free", "name": "OpenCode Zen Free", "signedIn": True},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    prov = _provider(_http({}))
    assert prov._their_provider_ids() == {"workbuddy", "opencode-zen-free"}


def test_an_empty_roster_is_not_a_verdict(tmp_path, monkeypatch):
    """A host that has not loaded yet must not take every card away.

    The card list is our table; their roster only says whether a vendor is
    still theirs. A file we can read but that says nothing is not the same as
    "they have no providers" — it is "we do not know".
    """
    data = tmp_path / "magpie"
    data.mkdir(parents=True, exist_ok=True)
    (data / "plugin-providers.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MagpieProvider, "_binary", lambda self: None)
    prov = _provider(_http({}))
    assert prov._their_provider_ids() is None


def test_their_printed_table_is_the_fallback(tmp_path, monkeypatch):
    """Their CLI prints one line per provider; their host's logs share the stream."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    seen: dict = {}

    def fake_cli(binary, env=None, timeout=120.0):
        seen["binary"] = binary
        return {"workbuddy-ai", "grok"}

    monkeypatch.setattr(their_list, "cli_provider_ids", fake_cli)
    monkeypatch.setattr(MagpieProvider, "_binary", lambda self: "magpie-cli.exe")
    prov = _provider(_http({}))
    assert prov._their_provider_ids() == {"workbuddy-ai", "grok"}
    assert seen["binary"] == "magpie-cli.exe"


def test_their_printed_table_is_read_by_shape():
    """Only the rows with their shape are read — a name may carry brackets."""
    text = "\n".join([
        "2026/11/10 plugin: host starts with no proxy NO_PROXY=localhost",
        "  workbuddy-ai (WorkBuddy AI, 16 models)  not signed in · magpie plugin login workbuddy-ai",
        "  opencode-zen-free (OpenCode Zen Free, 11 models)  signed in",
        "  grok (Grok (SuperGrok), 1 models)  not signed in · magpie plugin login grok",
        "  kiro (Kiro, 1 model)  not signed in · magpie plugin login kiro",
        "  @magpie-community/opencode-workbuddy-auth@latest  on",
    ])
    assert their_list.parse_cli_table(text) == {
        "workbuddy-ai",
        "opencode-zen-free",
        "grok",
        "kiro",
    }


def test_a_vendor_magpie_no_longer_has_loses_its_card():
    """Their roster decides whether a card is still worth showing.

    Not the other way round: a provider they added gets no card until somebody
    claims it (see the test above), and a provider they dropped takes its card
    with it.
    """
    prov = _provider(_http({}))
    prov._their_provider_ids = lambda: {"workbuddy", "opencode-zen-free"}
    specs = asyncio.run(prov.provider_specs())
    present = {s["id"]: s["present"] for s in specs}
    assert present["workbuddy"] is True
    assert present["qoder"] is False, "magpie has no qoder on this machine"


def test_our_table_claims_every_vendor_it_names():
    """Every row of our table is a card, and every row is well formed."""
    prov = _provider(_http({}))
    table = prov._our_table()
    assert table, "our table is the card list — it cannot be empty"

    seen: set[str] = set()
    for key, row in table.items():
        upstream = str(row.get("upstream") or key)
        assert upstream not in seen, f"{upstream} is claimed twice"
        seen.add(upstream)
        assert str(row.get("name") or "").strip(), f"{key} has no name on screen"
        assert row.get("card") is True, f"{key} is claimed but has no card"
        assert str(row.get("login") or "") in {"oauth", "api"}, f"{key} has no way in"

    assert "workbuddy" in table and "workbuddy-ai" in table, "国内版与国际版是两张卡"
    assert table["workbuddy"]["name"] == "WorkBuddy"
    assert table["workbuddy-ai"]["name"] == "WorkBuddy AI"
