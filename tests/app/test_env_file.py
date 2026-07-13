import os
import stat

import pytest

from demiurge.env_file import load_runtime_env, parse_env_text, upsert_env_value


def test_parse_env_text_supports_comments_export_and_quotes():
    values = parse_env_text(
        "\n"
        "# comment\n"
        "export A=one\n"
        "B='two # kept'\n"
        'C="line\\nvalue"\n'
        "D=plain # stripped\n"
    )

    assert values == {
        "A": "one",
        "B": "two # kept",
        "C": "line\nvalue",
        "D": "plain",
    }


def test_load_runtime_env_overrides_shell_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEMIURGE_TEST_KEY", "shell")
    (home / ".env").write_text('DEMIURGE_TEST_KEY="file"\n', encoding="utf-8")

    loaded = load_runtime_env(home)

    assert loaded == {"DEMIURGE_TEST_KEY": "file"}
    assert __import__("os").environ["DEMIURGE_TEST_KEY"] == "file"


def test_upsert_env_value_preserves_other_lines_and_replaces_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# existing\nA=\"old\"\nB=\"two\"\n", encoding="utf-8")

    upsert_env_value(path, "A", "new secret")

    assert path.read_text(encoding="utf-8") == '# existing\nA="new secret"\nB="two"\n'


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_upsert_env_value_sets_private_permissions_under_permissive_umask(tmp_path):
    home = tmp_path / "home"
    path = home / ".env"
    previous = os.umask(0)
    try:
        upsert_env_value(path, "API_TOKEN", "synthetic-secret")
    finally:
        os.umask(previous)

    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
