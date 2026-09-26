from pathlib import Path


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = Path("website/docs/user-guide/windows-native.md").read_text()
    install = Path("scripts/install.ps1").read_text()

    assert "%LOCALAPPDATA%\\plobi\\plobi-agent\\venv\\Scripts" in doc
    assert "Get-Command plobi        # should print C:\\Users\\<you>\\AppData\\Local\\plobi\\plobi-agent\\venv\\Scripts\\plobi.exe" in doc
    assert '$plobiBin = "$InstallDir\\venv\\Scripts"' in install
