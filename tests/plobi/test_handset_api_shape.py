"""裁定 139④(a) —「接口预留」的可执行形状：设备面公开 API 的**形状冻结**。

用户 10-14 19:2x 把移动端线整体押后，但要求「架构层面预留好接口」。裁定 139④ 明写
**预留不等于留着不管**，第一条就是这套公开 API 的形状：改名、删口、开私口都算违契约
（SPEC-FINAL「App / 手机面走同一套公开 API」）。口头留着的话，这条线重启那天要从头长——
所以钉成测试，动形状就红。

四条用例分别钉：
① **读面五张口**一条不多一条不少（`agenda` / `agenda/pending` / `agenda/day` / `tasks` / `events`）；
② **写面只有 confirm 与 dismiss**（POST），任务历史是唯一的带参 GET；创建 / PATCH / DELETE
   永远不在这条缝上——手机不需要写板子就能看和确认一天（裁定 64 的原意）；
③ **令牌形状**：前缀、provider 名、principal、三个 scope 一字不易；令牌是**每台设备自己那一份**，
   不是全后端共用密钥（已退役的 `app_token` 不许回来）；
④ **公开面只有一个配对入口** `/api/handset/pair/redeem`（裁定 64：没有凭据的设备是调用方，
   边界在 `DeviceStore.redeem` 的次数预算里，不在 allowlist）；其余 `/api/handset/*` 一旦被
   放进 `PUBLIC_API_PATHS` 就是开私口，这条当场红。

配对令牌「可单独撤销」的行为证据不在这里重复：`tests/plobi_cli/test_pairing_device_cli.py`
（撤销一台不伤另一台、撤销后 401 的实测信封）与 `tests/plobi/test_handset_pairing_auth.py`
（gated 绑上真 cookie 门）已经钉过；本文件只管**形状**。
"""

from __future__ import annotations

import pytest

from plobi_cli.dashboard_auth import pairing, public_paths, registry, token_auth

# ---- 冻结的形状（改这里=改契约，必须连着改 SPEC-FINAL 与鸿蒙 App）----

FROZEN_HANDSET_PREFIX = "/api/handset"
FROZEN_PROVIDER_NAME = "pairing"
FROZEN_PRINCIPAL = "handset"
FROZEN_SCOPES = frozenset({"agenda:read", "agenda:confirm", "task:read"})
FROZEN_READS = frozenset({
    "/api/handset/agenda",
    "/api/handset/agenda/pending",
    "/api/handset/agenda/day",
    "/api/handset/tasks",
    "/api/handset/events",
})
FROZEN_POST_PATTERNS = frozenset({
    r"/api/handset/agenda/[^/]+/confirm",
    r"/api/handset/agenda/[^/]+/dismiss",
})
FROZEN_GET_PATTERNS = frozenset({r"/api/handset/tasks/[^/]+/events"})
FROZEN_PUBLIC_HANDSET_PATHS = frozenset({"/api/handset/pair/redeem"})


@pytest.fixture()
def armed_seam():
    """装好设备面路由的 seam，用完复原全局注册表（与既有 gated 套件同纪律）。"""
    token_auth.clear_token_routes()
    registry.clear_providers()
    assert pairing.install_pairing_auth() is True
    yield
    token_auth.clear_token_routes()
    registry.clear_providers()


def test_read_surface_is_exactly_the_five_frozen_paths(armed_seam):
    assert frozenset(pairing._TOKEN_ROUTES) == FROZEN_READS
    assert pairing.HANDSET_API_PREFIX == FROZEN_HANDSET_PREFIX
    for path in FROZEN_READS:
        assert token_auth.is_token_route(path, "GET"), path
        # 读面不借道写：GET 口拒绝 POST，免得哪天有人把写操作挂到同一路径上
        assert not token_auth.is_token_route(path, "POST"), path


def test_write_surface_is_only_confirm_and_dismiss(armed_seam):
    assert frozenset(pairing._TOKEN_ROUTE_PATTERNS_POST) == FROZEN_POST_PATTERNS
    assert frozenset(pairing._TOKEN_ROUTE_PATTERNS_GET) == FROZEN_GET_PATTERNS

    hit = "/api/handset/agenda/evt_0123/confirm"
    assert token_auth.is_token_route(hit, "POST")
    for verb in ("GET", "PATCH", "PUT", "DELETE"):
        assert not token_auth.is_token_route(hit, verb), verb

    # 创建 / 改 / 删与任何其它子路径都不许出现在设备令牌这条缝上
    for path in (
        "/api/handset/agenda",            # POST = 建事件
        "/api/handset/agenda/evt_1",      # PATCH / DELETE
        "/api/handset/agenda/evt_1/patch",
        "/api/handset/agenda/evt_1/transfer",
        "/api/handset/tasks/evt_1",
        "/api/handset/pair/redeem",       # 配对口不在 token 缝上（它先于令牌存在）
    ):
        assert not token_auth.is_token_route(path, "POST"), path
    assert not token_auth.is_token_route("/api/handset/agenda/evt_1", "PATCH")
    assert not token_auth.is_token_route("/api/handset/agenda/evt_1", "DELETE")

    # 带参 GET 只有任务历史那一张口
    assert token_auth.is_token_route("/api/handset/tasks/t_1/events", "GET")
    assert not token_auth.is_token_route("/api/handset/agenda/evt_1/events", "GET")

    # 浏览器面不因设备令牌而开：手机只能走自己的前缀
    for path in ("/api/agenda", "/api/agenda/day", "/api/tasks", "/api/events"):
        assert not token_auth.is_token_route(path, "GET"), path


def test_credential_shape_is_per_device_and_scoped(armed_seam):
    """令牌是设备自己那一份、带三个 scope、principal 里点名是哪台——共用密钥回不来。"""
    assert pairing.PROVIDER_NAME == FROZEN_PROVIDER_NAME
    assert pairing.HANDSET_PRINCIPAL == FROZEN_PRINCIPAL
    assert frozenset(pairing.HANDSET_SCOPES) == FROZEN_SCOPES

    provider = registry.get_provider(FROZEN_PROVIDER_NAME)
    assert provider is not None
    assert provider.name == FROZEN_PROVIDER_NAME
    assert provider.supports_token is True
    # 不是人登录：不进 /login 候选、不铸 cookie，也不可能认出 session cookie
    assert provider.supports_session is False
    assert provider.verify_session(access_token="anything") is None
    assert provider.verify_token(token="") is None
    with pytest.raises(NotImplementedError):
        provider.start_login(redirect_uri="http://127.0.0.1/cb")


def test_only_the_pairing_bootstrap_is_public(armed_seam):
    """除配对入口外，任何 `/api/handset/*` 出现在公开 allowlist 里都是开私口。"""
    open_handset = {
        path for path in public_paths.PUBLIC_API_PATHS
        if path.startswith(FROZEN_HANDSET_PREFIX)
    }
    assert open_handset == FROZEN_PUBLIC_HANDSET_PATHS
    assert "/api/handset/agenda" not in public_paths.PUBLIC_API_PATHS
    assert "/api/handset/agenda/day" not in public_paths.PUBLIC_API_PATHS
