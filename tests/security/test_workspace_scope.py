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
