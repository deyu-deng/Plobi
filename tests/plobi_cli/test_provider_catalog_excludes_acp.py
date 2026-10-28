"""ACP 腿不算 provider（裁定 205，用户 2026-10-28）。

「那就不属于 provider 了，放到外包那一栏去了」。判据是关系不是名单：模型清单里不许
出现任何 ACP 形状的行（`auth_type=external_process` 那一族，base_url 是 `acp://`）。
理由在裁定 204 那张表里：它们的工具在上游闭环执行，回到 Plobi 的是成稿而不是
`tool_calls`（`terminal` 跑出文件的实测，`tool_calls` 为 0）。
"""

from __future__ import annotations

from plobi_cli.model_switch import list_authenticated_providers


def test_no_acp_leg_is_offered_as_a_model_provider():
    rows = list_authenticated_providers()

    offenders = [
        row.get("slug")
        for row in rows
        if str(row.get("slug") or "").lower().endswith("-acp")
        or str(row.get("base_url") or "").lower().startswith("acp://")
    ]
    assert offenders == [], f"这些 ACP 腿还在模型清单里：{offenders}"
