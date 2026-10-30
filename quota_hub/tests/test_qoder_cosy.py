"""COSY 签名与 `Encode=1` 编码的判据：全部离线，把读来的规则逐条钉住，不打网络。

形状来自 `caigee-cmd/cli2api`（MIT）与它上游 `EchoPing07/Qoder-2API-Go`（MIT），
我们抄的是规则不是它的进程。真账号端到端仍未验（他们自己那句「真账号验收仍未完成」），
所以这份测试只保证一件事：**我们算出来的东西与我们读到的定义逐字一致**。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets

import pytest
from quota_hub.providers.apps.qoder.cosy import (
    COSY_VERSION,
    Account,
    Session,
    decode_body,
    encode_body,
    machine_hostname,
    machine_id,
    machine_token,
    machine_type,
    sign_path,
)

CUSTOM = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _reference_encode(plain: bytes) -> str:
    """按读到的三步自己重算一遍：标准 base64 → 按三分之一轮转 → 换字母表。"""
    std = base64.b64encode(plain).decode("ascii")
    n = len(std)
    a = n // 3
    rotated = std[n - a:] + std[a:n - a] + std[:a]
    return rotated.translate(str.maketrans(STD + "=", CUSTOM + "$"))


def test_encode_body_is_the_documented_three_step_transform():
    for payload in (b"", b"a", b'{"model":"qwen3.8-max"}', ("中文字节" * 9).encode("utf-8")):
        assert encode_body(payload) == _reference_encode(payload)


def test_encode_decode_round_trip_every_length():
    for size in list(range(0, 40)) + [64, 255, 1024]:
        blob = secrets.token_bytes(size)
        assert decode_body(encode_body(blob)) == blob


def test_encoded_body_never_leaves_the_standard_alphabet_marks():
    encoded = encode_body(b'{"x":' + b"y" * 300 + b"}")
    assert "+" not in encoded and "/" not in encoded and "=" not in encoded


def test_sign_path_strips_algo_and_drops_the_query():
    url = ("https://gateway.qoder.com.cn/algo/api/v2/service/pro/sse/"
           "agent_chat_generation?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1")
    assert sign_path(url) == "/api/v2/service/pro/sse/agent_chat_generation"
    assert sign_path("https://x/algo") == "/"
    assert sign_path("https://x/plain/path") == "/plain/path"


def _account() -> Account:
    return Account(
        uid="0123456789abcdef0123456789abcdef",
        name="tester",
        organization_id="org-1",
        organization_name="Org",
        user_type="individual",
        security_oauth_token="security-token-value",
        refresh_token="refresh-token-value",
    )


def test_session_wraps_a_16_character_temp_key_that_is_both_key_and_iv():
    session = Session(_account(), temp_key="0123456789abcdef")
    # Cosy-Key 是 RSA-1024 PKCS#1 v1.5 包起来的 temp key ⇒ 定长 128 字节。
    wrapped = base64.b64decode(session.cosy_key)
    assert len(wrapped) == 128
    # info 是 AES-128-CBC 的身份块，密文长度必须是块长的整数倍。
    cipher_text = base64.b64decode(session.info)
    assert len(cipher_text) % 16 == 0

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = b"0123456789abcdef"
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    padded = decryptor.update(cipher_text) + decryptor.finalize()
    identity = json.loads(padded[: -padded[-1]].decode("utf-8"))
    # 九个字段、键有序、令牌用的是 security_oauth_token（他们的取值顺序）
    assert sorted(identity) == [
        "aid", "name", "organization_id", "organization_name", "refresh_token",
        "security_oauth_token", "uid", "user_type", "yx_uid",
    ]
    assert identity["security_oauth_token"] == "security-token-value"
    assert identity["aid"] == identity["uid"], "没有 aid 时他们退回 uid"
    assert list(identity) == sorted(identity), "身份块的键必须排序（签名两边字节要一致）"


def test_session_requires_uid_and_token():
    with pytest.raises(ValueError):
        Session(Account(uid="", security_oauth_token="t"))
    with pytest.raises(ValueError):
        Session(Account(uid="u", security_oauth_token=""))


def test_machine_fields_are_stable_per_uid_and_differ_across_accounts():
    first, second = _account(), _account()
    second.uid = "fedcba9876543210fedcba9876543210"
    assert machine_id(first.uid) == machine_id(first.uid) and len(machine_id(first.uid)) == 32
    assert len(machine_type(first.uid)) == 18
    assert len(machine_token(first.uid)) == 43
    assert machine_hostname(first.uid).startswith("DESKTOP-")
    assert machine_id(first.uid) != machine_id(second.uid), "两个账号要像两台机器"


def test_headers_sign_the_documented_concat_and_carry_the_identity():
    session = Session(_account(), temp_key="abcdef0123456789")
    body = encode_body(b'{"model":"x","messages":[]}')
    url = "https://gateway.qoder.com.cn/algo/api/v2/model/list?Encode=1"
    headers = session.headers(body=body, raw_url=url, request_id="req-1", now=1793000000.4)

    payload = headers["Authorization"].split(".")[1]
    expect = hashlib.md5(
        "\n".join((payload, session.cosy_key, "1793000000", body, "/api/v2/model/list")).encode("utf-8")
    ).hexdigest()
    assert headers["Authorization"] == f"Bearer COSY.{payload}.{expect}"
    assert headers["Cosy-Date"] == "1793000000"
    assert headers["Cosy-Version"] == COSY_VERSION == "1.1.75"
    assert headers["Cosy-User"] == _account().uid
    assert headers["User-Agent"] == "undici"
    for header in (
        "Accept", "Content-Type", "Login-Version", "Cosy-Data-Policy", "Cosy-Data-Policy",
        "Cosy-MachineId", "Cosy-MachineToken", "Cosy-MachineType", "Cosy-MachineOS",
        "Cosy-MachineHostname", "Cosy-Business-Product", "Cosy-Business-Type", "Cosy-Scene",
        "Cosy-Clientip", "Cosy-Key",
    ):
        assert headers[header], header
    # 签名里的 body 是编码后的那份，不是明文（Encode=1 的门才认它）。
    assert body not in json.dumps(headers)


def test_payload_carries_the_five_fields_in_order():
    session = Session(_account(), temp_key="0123456789abcdef")
    payload = json.loads(base64.b64decode(session.payload("req-9")))
    assert list(payload) == sorted(["cosyVersion", "ideVersion", "info", "requestId", "version"])
    assert payload["cosyVersion"] == COSY_VERSION and payload["version"] == "v1"
    assert payload["requestId"] == "req-9" and payload["info"] == session.info
