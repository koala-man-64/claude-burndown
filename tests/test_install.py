import json

from claude_burndown import install


def test_install_and_uninstall_statusline(tmp_path):
    settings_file = tmp_path / "settings.json"

    # Dry run
    dry_msg = install.install_statusline(settings_file, apply=False)
    assert "would set statusLine" in dry_msg
    assert not settings_file.exists()

    # Apply
    apply_msg = install.install_statusline(settings_file, apply=True)
    assert "statusLine set" in apply_msg
    assert settings_file.exists()
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "statusLine" in data
    assert "claude-burndown" in data["statusLine"]["command"]

    # Re-install is idempotent
    idemp_msg = install.install_statusline(settings_file, apply=True)
    assert "already installed" in idemp_msg

    # Uninstall
    un_msg = install.uninstall_statusline(settings_file, apply=True)
    assert "statusLine removed" in un_msg
    data2 = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "statusLine" not in data2
