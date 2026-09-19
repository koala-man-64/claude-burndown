from claude_burndown.cli import main


def test_cli_version(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    out, _ = capsys.readouterr()
    assert "claude-burndown" in out


def test_cli_capacity_disconnected(tmp_path, capsys):
    code = main(["--home", str(tmp_path), "capacity", "--json"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "schema_version" in out or "service_state" in out


def test_cli_render(tmp_path):
    code = main(["--home", str(tmp_path), "render"])
    assert code == 0
    html_file = tmp_path / "burndown.html"
    assert html_file.is_file()
    content = html_file.read_text(encoding="utf-8")
    assert "Claude Burndown" in content
