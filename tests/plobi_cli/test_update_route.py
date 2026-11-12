"""`plobi update` 走哪条路，必须按**安装方式**判定，而不是按平台。

回归：随包（bundled）安装的桌面版后端没有 .git，Windows 上原先一律落到
"ZIP 覆盖"修理分支 —— 那条路把 main HEAD 的源码盖到发行版源码树上，却不重装
依赖（依赖钉在随包 wheelhouse 里）。覆盖完第一次 import 就可能炸，等于把正式
安装换成未发布代码。判定抽成 plan_update_route() 之后才可测。
"""

from unittest.mock import patch


def _bundled_root(tmp_path):
    """造一个随包安装树的形状：install-stamp.json 与 wheelhouse 在源码树旁边。"""
    root = tmp_path / "resources" / "backend"
    root.mkdir(parents=True)

    (root / "requirements.txt").write_text("plobi-agent==0.3.1\n", encoding="utf-8")
    (tmp_path / "resources" / "wheelhouse").mkdir()
    (tmp_path / "resources" / "install-stamp.json").write_text(
        '{"branch": "v0.3.1", "commit": "345c35b4ed9cecd2f59d667fcaec379d7d215d53"}',
        encoding="utf-8",
    )

    return root


def _plan(root, platform):
    with patch("plobi_cli.config.get_managed_system", return_value=None), \
         patch("plobi_cli.config.get_plobi_home", return_value=root):
        from plobi_cli.config import plan_update_route

        return plan_update_route(root, platform)


def test_git_checkout_pulls(tmp_path):
    (tmp_path / ".git").mkdir()

    assert _plan(tmp_path, "win32") == "git"
    assert _plan(tmp_path, "linux") == "git"


def test_bundled_install_refuses_on_both_platforms(tmp_path):
    root = _bundled_root(tmp_path)

    assert _plan(root, "win32") == "bundled"
    assert _plan(root, "linux") == "bundled"


def test_stamp_next_to_backend_alone_is_enough(tmp_path):
    root = tmp_path / "resources" / "backend"
    root.mkdir(parents=True)
    (tmp_path / "resources" / "install-stamp.json").write_text("{}", encoding="utf-8")

    assert _plan(root, "win32") == "bundled"


def test_bare_windows_tree_keeps_the_zip_repair_route(tmp_path):
    """没有 .git 也不是 bundled —— 保留历史的 ZIP 修理路径，行为不变。"""
    assert _plan(tmp_path, "win32") == "zip"


def test_bare_posix_pip_install_delegates_to_pip(tmp_path):
    assert _plan(tmp_path, "linux") == "pip"


def test_bare_posix_managed_install_asks_for_reinstall(tmp_path):
    with patch("plobi_cli.config.get_managed_system", return_value="NixOS"), \
         patch("plobi_cli.config.get_plobi_home", return_value=tmp_path):
        from plobi_cli.config import plan_update_route

        assert plan_update_route(tmp_path, "linux") == "reinstall"


def test_refusal_message_names_the_real_update_path():
    from plobi_cli.config import bundled_update_refusal_message

    message = bundled_update_refusal_message()

    assert "wheelhouse" in message
    assert "installer" in message
    # 不能再把用户引向 pip —— 那会在 site-packages 里再装一份，盖住随包的那份。
    assert "pip install" not in message
