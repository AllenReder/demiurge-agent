from pathlib import Path

import pytest

from demiurge.security.workspace import WorkspaceScope, WorkspaceScopeError, truncate_text


def test_workspace_scope_resolves_relative_and_absolute_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    scope = WorkspaceScope(workspace)

    relative = scope.resolve_path("note.txt")
    absolute = scope.resolve_path(workspace / "note.txt")

    assert relative.path == absolute.path
    assert relative.relative == "note.txt"


def test_workspace_scope_rejects_parent_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scope = WorkspaceScope(workspace)

    with pytest.raises(WorkspaceScopeError):
        scope.resolve_path("../outside.txt")


def test_workspace_scope_allows_explicit_outside_read(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    outside.write_text("outside", encoding="utf-8")
    scope = WorkspaceScope(workspace)

    resolved = scope.resolve_path(outside, allow_outside_read=True)

    assert resolved.path == outside.resolve()
    assert resolved.relative == str(outside.resolve())
    assert resolved.outside is True
    assert resolved.sensitive is False


def test_workspace_scope_marks_outside_secret_reads_sensitive(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    private_key = outside / "id_ed25519"
    private_key.write_text("secret", encoding="utf-8")
    scope = WorkspaceScope(workspace)

    resolved = scope.resolve_path(private_key, allow_outside_read=True)

    assert resolved.outside is True
    assert resolved.sensitive is True


def test_workspace_scope_expands_home_before_outside_read_check(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    (home / "note.txt").write_text("home note", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    scope = WorkspaceScope(workspace)

    resolved = scope.resolve_path("~/note.txt", allow_outside_read=True)

    assert resolved.path == (home / "note.txt").resolve()
    assert resolved.outside is True


def test_workspace_scope_still_rejects_outside_writes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scope = WorkspaceScope(workspace)

    with pytest.raises(WorkspaceScopeError):
        scope.resolve_path(tmp_path / "outside.txt", operation="write")


def test_workspace_scope_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "link").symlink_to(outside)
    scope = WorkspaceScope(workspace)

    with pytest.raises(WorkspaceScopeError):
        scope.resolve_path("link/secret.txt")


def test_workspace_scope_marks_sensitive_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scope = WorkspaceScope(workspace)

    assert scope.resolve_path(".env", operation="read").sensitive is True
    assert scope.resolve_path(".ssh/id_rsa", operation="read").sensitive is True
    assert scope.resolve_path("private.key", operation="read").sensitive is True
    assert scope.resolve_path("pyproject.toml", operation="write").sensitive is True
    assert scope.resolve_path("pyproject.toml", operation="read").sensitive is False


def test_truncate_text_reports_omitted_chars():
    assert truncate_text("abcdef", limit=3) == "abc\n...[truncated 3 chars]"
