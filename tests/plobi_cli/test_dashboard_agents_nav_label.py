"""Static dashboard tests for the Agents navigation copy."""
from pathlib import Path


def test_agents_nav_label_uses_short_copy():
    en_i18n = Path(__file__).resolve().parents[2] / "web" / "src" / "i18n" / "en.ts"

    content = en_i18n.read_text(encoding="utf-8")

    # Nav label should be the clean short form, not the old verbose string
    assert 'profiles: "Agents"' in content
    assert "profiles : multi agents" not in content


def test_profiles_page_route_renamed_to_agents():
    """WP-PROFILE-CANCEL 收尾：URL 面只暴露 /agents，旧 /profiles 只做重定向。"""
    app_tsx = Path(__file__).resolve().parents[2] / "web" / "src" / "App.tsx"

    content = app_tsx.read_text(encoding="utf-8")

    assert '"/agents": AgentsPage' in content
    assert '"/agents/new": AgentBuilderPage' in content
    # 旧路径必须仍在，但只作为 <Navigate> 重定向存在
    assert '"/profiles": () => <Navigate to="/agents" replace />' in content
    assert 'path: "/agents", labelKey: "profiles", label: "Agents"' in content
