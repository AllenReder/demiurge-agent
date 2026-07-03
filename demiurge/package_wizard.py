from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
from typing import Protocol

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from demiurge.app import HostConfig, HostPackageRepositoryConfig, write_host_config
from demiurge.gates import GateRunner
from demiurge.packages import PackageInfo, PackageManager, PackageOperationError, PackageOption
from demiurge.packages import (
    inspect_package_repository_candidate,
    installed_repository_dependents,
    load_package_repository_collection,
    package_repository_cache_root,
    sync_package_repository,
)
from demiurge.storage import VersionStore


@dataclass(frozen=True, slots=True)
class SelectChoice:
    value: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class TableColumn:
    label: str


@dataclass(frozen=True, slots=True)
class TableRow:
    value: str
    cells: tuple[str, ...]
    description: str = ""
    row_type: str = "item"


class PackagePrompt(Protocol):
    def select(self, title: str, choices: list[SelectChoice], *, default_index: int = 0) -> str:
        ...

    def select_table(self, title: str, columns: list[TableColumn], rows: list[TableRow], *, default_index: int = 0) -> str:
        ...

    def confirm(self, message: str, *, default: bool = False) -> bool:
        ...

    def input(self, message: str, *, default: str | None = None, secret: bool = False) -> str:
        ...


class PromptToolkitPackagePrompt:
    def select(self, title: str, choices: list[SelectChoice], *, default_index: int = 0) -> str:
        if not choices:
            raise ValueError("select requires at least one choice")
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style

        index = {"value": max(0, min(default_index, len(choices) - 1))}

        def _text():
            rows: list[tuple[str, str]] = [("class:title", f"{title}\n")]
            for offset, choice in enumerate(choices):
                selected = offset == index["value"]
                prefix = "> " if selected else "  "
                style = "reverse" if selected else ""
                rows.append((style, f"{prefix}{choice.label}"))
                if choice.description:
                    description_style = "reverse" if selected else "class:description"
                    rows.append((description_style, f" - {choice.description}"))
                rows.append((style, "\n"))
            rows.append(("", "\nUp/Down to choose, Enter to confirm, Ctrl-C to cancel."))
            return rows

        kb = KeyBindings()

        @kb.add("up", eager=True)
        def _up(event):
            index["value"] = (index["value"] - 1) % len(choices)
            event.app.invalidate()

        @kb.add("down", eager=True)
        def _down(event):
            index["value"] = (index["value"] + 1) % len(choices)
            event.app.invalidate()

        @kb.add("enter", eager=True)
        def _enter(event):
            event.app.exit(result=choices[index["value"]].value)

        @kb.add("c-c", eager=True)
        def _cancel(event):
            event.app.exit(result=None)

        app = Application(
            layout=Layout(Window(FormattedTextControl(_text), dont_extend_height=True)),
            key_bindings=kb,
            style=Style.from_dict({"description": "ansibrightblack", "title": "bold"}),
            full_screen=False,
            erase_when_done=True,
        )
        result = app.run()
        if result is None:
            raise KeyboardInterrupt
        return str(result)

    def select_table(self, title: str, columns: list[TableColumn], rows: list[TableRow], *, default_index: int = 0) -> str:
        if not rows:
            raise ValueError("select_table requires at least one row")
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style

        index = {"value": max(0, min(default_index, len(rows) - 1))}

        def _table_widths() -> list[int]:
            widths = [len(column.label) for column in columns]
            for row in rows:
                if row.row_type != "item":
                    continue
                for offset, cell in enumerate(row.cells[: len(widths)]):
                    widths[offset] = max(widths[offset], len(cell))
            return widths

        def _format_item_row(row: TableRow, widths: list[int]) -> str:
            cells = list(row.cells)
            cells.extend([""] * max(0, len(columns) - len(cells)))
            rendered = []
            for offset, cell in enumerate(cells[: len(columns)]):
                if offset == len(columns) - 1:
                    rendered.append(cell)
                else:
                    rendered.append(cell.ljust(widths[offset]))
            return "  ".join(rendered).rstrip()

        def _format_action_row(row: TableRow) -> str:
            label = row.cells[0] if row.cells else row.value
            return f"{label} - {row.description}" if row.description else label

        def _text():
            widths = _table_widths()
            header = _format_item_row(TableRow("", tuple(column.label for column in columns)), widths)
            rows_out: list[tuple[str, str]] = [("class:title", f"{title}\n")]
            rows_out.append(("class:header", f"  {header}\n"))
            action_started = False
            for offset, row in enumerate(rows):
                selected = offset == index["value"]
                prefix = "> " if selected else "  "
                style = "reverse" if selected else ""
                if row.row_type != "item":
                    if not action_started:
                        rows_out.append(("", "\n"))
                        action_started = True
                    text = _format_action_row(row)
                    rows_out.append((style or "class:action", f"{prefix}{text}\n"))
                    continue
                rows_out.append((style, f"{prefix}{_format_item_row(row, widths)}\n"))
            rows_out.append(("", "\nUp/Down to choose, Enter to confirm, Ctrl-C to cancel."))
            return rows_out

        kb = KeyBindings()

        @kb.add("up", eager=True)
        def _up(event):
            index["value"] = (index["value"] - 1) % len(rows)
            event.app.invalidate()

        @kb.add("down", eager=True)
        def _down(event):
            index["value"] = (index["value"] + 1) % len(rows)
            event.app.invalidate()

        @kb.add("enter", eager=True)
        def _enter(event):
            event.app.exit(result=rows[index["value"]].value)

        @kb.add("c-c", eager=True)
        def _cancel(event):
            event.app.exit(result=None)

        app = Application(
            layout=Layout(Window(FormattedTextControl(_text), dont_extend_height=True)),
            key_bindings=kb,
            style=Style.from_dict({"action": "ansibrightblack", "header": "ansibrightblack bold", "title": "bold"}),
            full_screen=False,
            erase_when_done=True,
        )
        result = app.run()
        if result is None:
            raise KeyboardInterrupt
        return str(result)

    def confirm(self, message: str, *, default: bool = False) -> bool:
        from prompt_toolkit import prompt

        suffix = "[Y/n]" if default else "[y/N]"
        answer = prompt(f"{message} {suffix} ").strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes", "true", "1", "on"}

    def input(self, message: str, *, default: str | None = None, secret: bool = False) -> str:
        from prompt_toolkit import prompt

        suffix = ""
        if default and not secret:
            suffix = f" [{default}]"
        elif default and secret:
            suffix = " [configured]"
        return prompt(f"{message}{suffix}: ", default=default or "", is_password=secret)


class PackageWizard:
    def __init__(
        self,
        *,
        manager: PackageManager,
        version_store: VersionStore,
        home: Path,
        host_config: HostConfig,
        console: Console | None = None,
        prompt: PackagePrompt | None = None,
        default_core_id: str = "assistant",
    ) -> None:
        self.manager = manager
        self.version_store = version_store
        self.home = home
        self.host_config = host_config
        self.console = console or Console()
        self.prompt = prompt or PromptToolkitPackagePrompt()
        self.default_core_id = default_core_id or "assistant"

    def run(self) -> None:
        try:
            while True:
                action = self.prompt.select(
                    "Package manager",
                    [
                        SelectChoice("packages", "Packages", "Install or uninstall packages for an agent core"),
                        SelectChoice("repos", "Repos", "Manage host-level package repositories"),
                        SelectChoice("exit", "Exit"),
                    ],
                )
                if action == "packages":
                    self._packages()
                elif action == "repos":
                    self._repositories()
                elif action == "exit":
                    return
        except KeyboardInterrupt:
            self.console.print("Canceled.")

    def _packages(self) -> None:
        core_id = self._select_core()
        self.console.print(Panel(f"Managing packages for [bold]{core_id}[/bold]", title="demiurge package"))
        self._manage_core_packages(core_id)

    def _select_core(self) -> str:
        core_ids = sorted(
            core_id
            for core_id in self.version_store.list_core_ids()
            if (self.version_store.active_core_path(core_id) / "agent.yaml").exists()
        )
        if not core_ids:
            raise PackageOperationError("no runtime cores found")
        default_index = core_ids.index(self.default_core_id) if self.default_core_id in core_ids else 0
        return self.prompt.select_table(
            "Select agent core",
            [TableColumn("Status"), TableColumn("Core"), TableColumn("Path")],
            [
                TableRow(
                    core_id,
                    (
                        "default" if core_id == self.default_core_id else "",
                        core_id,
                        str(self.version_store.active_core_path(core_id)),
                    ),
                )
                for core_id in core_ids
            ],
            default_index=default_index,
        )

    def _manage_core_packages(self, core_id: str) -> None:
        search_query: str | None = None
        repo_filter: str | None = None
        tag_filter: str | None = None
        while True:
            result = self.manager.list(core_id=core_id)
            installed_by_ref = {self._installed_ref(item): item for item in result.installed}
            installed_by_id = {item.package_id: item for item in result.installed}
            packages = self._filtered_packages(result.packages, query=search_query, repository_alias=repo_filter, tag=tag_filter)
            rows = [
                TableRow(
                    package.ref,
                    (
                        self._package_state_marker(package, installed_by_ref, installed_by_id),
                        package.repository_alias,
                        package.package_id,
                        self._package_table_detail(package, installed_by_ref, installed_by_id),
                    ),
                )
                for package in packages
            ]
            rows.extend(
                [
                    TableRow("__search__", ("Search",), search_query or "Filter by id, name, summary, or tag", row_type="action"),
                    TableRow("__filter_repo__", ("Filter repo",), repo_filter or "All repositories", row_type="action"),
                    TableRow("__filter_tag__", ("Filter tag",), tag_filter or "All tags", row_type="action"),
                    TableRow("__clear_filters__", ("Clear filters",), row_type="action"),
                    TableRow("__back__", ("Back",), row_type="action"),
                ]
            )
            selected = self.prompt.select_table(
                self._package_list_title(core_id, search_query, repo_filter, tag_filter),
                [TableColumn(""), TableColumn("Repo"), TableColumn("Package"), TableColumn("Tags / Summary")],
                rows,
            )
            if selected == "__back__":
                return
            if selected == "__search__":
                search_query = self.prompt.input("Search query", default=search_query or "").strip() or None
                continue
            if selected == "__filter_repo__":
                repo_filter = self._select_repository_filter(repo_filter)
                continue
            if selected == "__filter_tag__":
                tag_filter = self._select_tag_filter(tag_filter)
                continue
            if selected == "__clear_filters__":
                search_query = None
                repo_filter = None
                tag_filter = None
                continue
            package = self.manager.repositories.resolve_package_ref(selected)
            installed_record = installed_by_ref.get(package.ref)
            if installed_record is not None:
                self._uninstall_package(core_id, installed_record)
                continue
            conflicting_record = installed_by_id.get(package.package_id)
            if conflicting_record is not None:
                self.console.print(
                    f"[yellow]Blocked:[/yellow] {conflicting_record.repository_alias}/{conflicting_record.package_id} "
                    f"is already installed for {core_id}. Uninstall it before installing {package.ref}."
                )
                continue
            self._install_package(core_id, package)

    def _filtered_packages(
        self,
        packages: list[PackageInfo],
        *,
        query: str | None,
        repository_alias: str | None,
        tag: str | None,
    ) -> list[PackageInfo]:
        filtered = packages
        if repository_alias:
            filtered = [package for package in filtered if package.repository_alias == repository_alias]
        if tag:
            filtered = [package for package in filtered if tag in package.tags]
        if query:
            normalized = query.lower()
            filtered = [
                package
                for package in filtered
                if normalized in package.package_id.lower()
                or normalized in package.name.lower()
                or normalized in package.summary.lower()
                or any(normalized in item.lower() for item in package.tags)
            ]
        return filtered

    def _package_state_marker(self, package: PackageInfo, installed_by_ref: dict[str, object], installed_by_id: dict[str, object]) -> str:
        if package.ref in installed_by_ref:
            return "✓"
        if package.package_id in installed_by_id:
            return "!"
        return ""

    def _package_table_detail(self, package: PackageInfo, installed_by_ref: dict[str, object], installed_by_id: dict[str, object]) -> str:
        tags = ", ".join(package.tags) or "no tags"
        if package.ref in installed_by_ref:
            return f"{tags} - {package.summary}"
        conflicting = installed_by_id.get(package.package_id)
        if conflicting is not None:
            conflict_ref = self._installed_ref(conflicting)
            return f"blocked: {conflict_ref} already installed - {tags} - {package.summary}"
        return f"{tags} - {package.summary}"

    def _package_list_title(self, core_id: str, query: str | None, repository_alias: str | None, tag: str | None) -> str:
        filters = []
        if query:
            filters.append(f"search={query}")
        if repository_alias:
            filters.append(f"repo={repository_alias}")
        if tag:
            filters.append(f"tag={tag}")
        suffix = f" ({', '.join(filters)})" if filters else ""
        return f"Packages for {core_id}{suffix}"

    def _select_repository_filter(self, current: str | None) -> str | None:
        aliases = sorted(self.manager.repositories.repositories)
        choices = [SelectChoice(alias, alias) for alias in aliases]
        choices.extend([SelectChoice("__all__", "All repositories"), SelectChoice("__back__", "Back")])
        default_index = aliases.index(current) if current in aliases else 0
        selected = self.prompt.select("Filter repo", choices, default_index=default_index)
        if selected == "__back__":
            return current
        if selected == "__all__":
            return None
        return selected

    def _select_tag_filter(self, current: str | None) -> str | None:
        tags = self.manager.list().tags
        choices = [SelectChoice(tag, tag, f"{self._tag_count(tag)} package(s)") for tag in tags]
        choices.extend([SelectChoice("__all__", "All tags"), SelectChoice("__back__", "Back")])
        default_index = tags.index(current) if current in tags else 0
        selected = self.prompt.select("Filter tag", choices, default_index=default_index)
        if selected == "__back__":
            return current
        if selected == "__all__":
            return None
        return selected

    def _install_package(self, core_id: str, package: PackageInfo) -> None:
        self._print_package(package, installed=False)
        answers = self._collect_options(package)
        try:
            preview = self.manager.preview_install(core_id=core_id, package_id=package.ref, option_answers=answers)
        except PackageOperationError as exc:
            self.console.print(f"[red]Install blocked:[/red] {exc}")
            return
        self._print_preview(preview)
        action = self.prompt.select(
            f"Install {package.ref} into {core_id}",
            [SelectChoice("install", "Install now"), SelectChoice("back", "Back")],
        )
        if action != "install":
            self.console.print("Install canceled.")
            return
        try:
            result = self._commit_package_transaction(
                f"install {package.ref}",
                lambda: self.manager.install(core_id=core_id, package_id=package.ref, option_answers=answers),
            )
        except PackageOperationError as exc:
            self.console.print(f"[red]Install failed:[/red] {exc}")
            return
        self.console.print(f"installed {result.package_ref} for {result.core_id}")
        if result.revision:
            self.console.print(f"revision: {result.revision[:12]}")
        for warning in result.warnings:
            self.console.print(f"warning: {warning}")

    def _uninstall_package(self, core_id: str, record) -> None:
        package_ref = self._installed_ref(record)
        self._print_installed_detail(core_id, package_ref)
        try:
            preview = self.manager.preview_uninstall(core_id=core_id, package_id=package_ref)
        except PackageOperationError as exc:
            self.console.print(f"[red]Uninstall blocked:[/red] {exc}")
            return
        self._print_preview(preview)
        action = self.prompt.select(
            f"Uninstall {package_ref} from {core_id}",
            [SelectChoice("uninstall", "Uninstall now"), SelectChoice("back", "Back")],
        )
        if action != "uninstall":
            self.console.print("Uninstall canceled.")
            return
        destructive = False
        if preview.warnings:
            destructive = self.prompt.confirm("Package provenance has drifted. Force removal?", default=False)
            if not destructive:
                self.console.print("Uninstall canceled.")
                return
        result = self._commit_package_transaction(
            f"uninstall {package_ref}",
            lambda: self.manager.uninstall(core_id=core_id, package_id=package_ref, destructive=destructive),
        )
        self.console.print(f"uninstalled {result.package_ref} for {result.core_id}")
        if result.revision:
            self.console.print(f"revision: {result.revision[:12]}")
        for warning in result.warnings:
            self.console.print(f"warning: {warning}")

    def _commit_package_transaction(self, action: str, operation):
        repository = self.version_store.core_repository
        repository.prepare_live_for_edit(
            validate=lambda agents_root, changed_paths: asyncio.run(
                GateRunner(project_root=Path.cwd().resolve()).run(agents_root, changed_paths=changed_paths)
            )
        )
        with repository.live_transaction(reason=f"package {action}"):
            result = operation()
            changed_paths = repository.live_changed_paths()
            gates = asyncio.run(GateRunner(project_root=Path.cwd().resolve()).run(repository.active_agents_root(), changed_paths=changed_paths))
            if not gates.passed:
                failures = [phase for phase in gates.phases if not phase.passed]
                summary = "; ".join(f"{phase.name}: {phase.detail}" for phase in failures[:5]) or "unknown gate failure"
                raise PackageOperationError("package gates failed: " + summary)
            commit = repository.commit_live(reason=f"package {action}", summary=f"package {action}")
            return replace(result, revision=commit.revision, previous_revision=commit.previous_revision)

    def _repositories(self) -> None:
        while True:
            rows = [
                TableRow(
                    status.alias,
                    (
                        "ready" if status.ready else "error",
                        status.alias,
                        status.name or status.repository_id or "(unknown)",
                        status.source_type,
                        str(status.package_count),
                        self._repository_ref_root(status),
                    ),
                )
                for status in self.manager.repositories.statuses
            ]
            rows.extend(
                [
                    TableRow("__add__", ("Add repo",), "Add a trusted path or git package repository", row_type="action"),
                    TableRow("__sync_all__", ("Sync all",), "Fetch git repositories and validate configured repositories", row_type="action"),
                    TableRow("__back__", ("Back",), row_type="action"),
                ]
            )
            selected = self.prompt.select_table(
                "Package repositories",
                [
                    TableColumn("Status"),
                    TableColumn("Alias"),
                    TableColumn("Repository"),
                    TableColumn("Type"),
                    TableColumn("Packages"),
                    TableColumn("Ref / Root"),
                ],
                rows,
            )
            if selected == "__back__":
                return
            if selected == "__sync_all__":
                self._sync_all_repositories()
            elif selected == "__add__":
                self._add_repository()
            else:
                self._repository_detail(selected)

    def _repository_detail(self, alias: str) -> None:
        status = self._repository_status(alias)
        if status is not None:
            self._print_repository_detail(status)
        actions = [SelectChoice("sync", "Sync"), SelectChoice("back", "Back")]
        if alias != "builtin":
            actions.insert(1, SelectChoice("remove", "Remove"))
        action = self.prompt.select(f"Repository {alias}", actions)
        if action == "sync":
            self._sync_repository(alias)
        elif action == "remove":
            self._remove_repository(alias)

    def _sync_all_repositories(self) -> None:
        for alias in list(self.host_config.packages.repositories):
            self._sync_repository(alias, reload_manager=False)
        self._reload_manager()

    def _sync_repository(self, alias: str, *, reload_manager: bool = True) -> None:
        config = self.host_config.packages.repositories[alias]
        try:
            status = sync_package_repository(
                home=self.home,
                alias=alias,
                config=config.model_dump(mode="python", exclude_none=True),
            )
            self.console.print(f"synced {alias}: {status.package_count} package(s)")
        except Exception as exc:
            self.console.print(f"[red]Sync failed for {alias}:[/red] {exc}")
        if reload_manager:
            self._reload_manager()

    def _add_repository(self) -> None:
        location = self.prompt.input("Git URL or local path").strip()
        if not location:
            self.console.print("Repository location is required.")
            return
        ref = self.prompt.input("Git ref", default="").strip() or None
        subdir = self.prompt.input("Subdir", default="").strip() or None
        config = self._repository_config_from_location(location=location, ref=ref, subdir=subdir, trusted=False)
        try:
            candidate = inspect_package_repository_candidate(
                home=self.home,
                config=config.model_dump(mode="python", exclude_none=True),
            )
        except Exception as exc:
            self.console.print(f"[red]Add failed:[/red] {exc}")
            return
        alias = self._prompt_repository_alias(candidate.repository_id or "", candidate.name)
        if not alias:
            return
        if not self.prompt.confirm(
            f"Trust {alias}? Package repositories can install local code into host-shared agent slots.",
            default=False,
        ):
            self.console.print("Repository was not trusted.")
            return
        config.trusted = True
        try:
            status = sync_package_repository(home=self.home, alias=alias, config=config.model_dump(mode="python", exclude_none=True))
        except Exception as exc:
            self.console.print(f"[red]Add failed:[/red] {exc}")
            return
        self.host_config.packages.repositories[alias] = config
        write_host_config(self.home / "config.yaml", self.host_config)
        self._reload_manager()
        self.console.print(f"added {alias}: {status.package_count} package(s)")

    def _prompt_repository_alias(self, repository_id: str, name: str | None) -> str | None:
        if not repository_id:
            self.console.print("Repository metadata did not include an id.")
            return None
        self.console.print(f"repository: {name or repository_id} ({repository_id})")
        alias = self.prompt.input("Repository alias", default=repository_id).strip() or repository_id
        suffix = 2
        while alias in self.host_config.packages.repositories:
            self.console.print(f"Repository already exists: {alias}")
            suggested = f"{repository_id}-{suffix}"
            alias = self.prompt.input("Repository alias", default=suggested).strip() or suggested
            suffix += 1
        return alias

    def _remove_repository(self, alias: str) -> None:
        if alias == "builtin":
            self.console.print("builtin package repository cannot be removed.")
            return
        if alias not in self.host_config.packages.repositories:
            self.console.print(f"Unknown repository: {alias}")
            return
        dependents = installed_repository_dependents(self.version_store, alias)
        if dependents:
            self.console.print("Installed package records still reference this repository: " + ", ".join(dependents))
            action = self.prompt.select(
                f"Remove repository {alias}",
                [SelectChoice("force", "Force remove source"), SelectChoice("back", "Back")],
            )
            if action != "force":
                return
        else:
            action = self.prompt.select(
                f"Remove repository {alias}",
                [SelectChoice("remove", "Remove"), SelectChoice("back", "Back")],
            )
            if action != "remove":
                return
        config = self.host_config.packages.repositories.pop(alias)
        if config.type == "git":
            cache_path = package_repository_cache_root(self.home) / alias
            if cache_path.exists():
                shutil.rmtree(cache_path)
        write_host_config(self.home / "config.yaml", self.host_config)
        self._reload_manager()
        self.console.print(f"removed {alias}")

    def _collect_options(self, package: PackageInfo) -> dict[str, object]:
        answers: dict[str, object] = {}
        for option in package.options:
            answers[option.option_id] = self._collect_option(option)
        return answers

    def _collect_option(self, option: PackageOption) -> object:
        if option.description and option.option_type != "choice":
            self.console.print(f"[dim]{option.description}[/dim]")
        if option.option_type == "bool":
            default = bool(option.default) if option.has_default and option.default is not None else False
            return self.prompt.confirm(option.prompt, default=default)
        if option.option_type == "choice":
            default_index = option.choices.index(option.default) if option.default in option.choices else 0
            return self.prompt.select(
                option.prompt,
                [SelectChoice(choice, choice, option.choice_descriptions.get(choice, "")) for choice in option.choices],
                default_index=default_index,
            )
        default = str(option.default) if option.has_default and option.default is not None else None
        value = self.prompt.input(option.prompt, default=default, secret=option.secret)
        if value == "" and not option.required and default is None:
            return None
        if value == "" and default is not None:
            return default
        return value

    def _print_package(self, package: PackageInfo, *, installed: bool) -> None:
        table = Table(title=f"Package: {package.ref}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("repository", package.repository_alias)
        table.add_row("name", package.name)
        table.add_row("tags", ", ".join(package.tags))
        table.add_row("summary", package.summary)
        table.add_row("installed", "yes" if installed else "no")
        table.add_row("manual dependencies", "\n".join(package.manual_dependencies) or "(none)")
        table.add_row("options", self._format_options(package))
        table.add_row(
            "components",
            "\n".join(
                f"{component.kind}:{component.source}"
                + (f" -> {component.target}" if component.target else "")
                + (f" -> core {component.target_core_id}" if component.target_core_id else "")
                + (f" when {component.when}" if component.when else "")
                for component in package.components
            ),
        )
        self.console.print(table)

    def _print_preview(self, preview) -> None:
        table = Table(title=f"{preview.action.title()} preview: {preview.package_ref}")
        table.add_column("kind")
        table.add_column("target")
        table.add_column("action")
        for component in preview.components:
            target = str(component.get("target") or component.get("target_core_id") or "")
            if preview.action == "uninstall":
                action = "remove" if component.get("remove", True) else "keep shared"
            else:
                action = "reuse" if component.get("reused") else "write"
            table.add_row(str(component.get("kind") or ""), target, action)
        self.console.print(table)
        for warning in preview.warnings:
            self.console.print(f"warning: {warning}")

    def _print_installed_detail(self, core_id: str, package_ref: str) -> None:
        record = next(item for item in self.manager.list(core_id=core_id).installed if self._installed_ref(item) == package_ref)
        table = Table(title=f"Installed package: {package_ref}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("repository alias", record.repository_alias)
        table.add_row("repository id", record.repository_id)
        table.add_row("repository type", record.repository_type)
        table.add_row("repository ref", record.repository_ref or "(none)")
        table.add_row("repository commit", record.repository_commit or "(none)")
        table.add_row("tags", ", ".join(record.tags))
        table.add_row("options", "\n".join(f"{key}: {value}" for key, value in record.options.items()) or "(none)")
        table.add_row("drift", "\n".join(record.drift) or "(none)")
        table.add_row(
            "components",
            "\n".join(
                f"{component.get('kind')}:{component.get('target') or component.get('target_core_id')}"
                + (" (reused)" if component.get("reused") else "")
                for component in record.components
            ),
        )
        self.console.print(table)

    def _format_options(self, package: PackageInfo) -> str:
        if not package.options:
            return "(none)"
        rows = []
        for option in package.options:
            required = "required" if option.required else "optional"
            secret = ", secret" if option.secret else ""
            choices = f", choices={','.join(option.choices)}" if option.choices else ""
            description = f" - {option.description}" if option.description else ""
            choice_details = [
                f"{choice}: {option.choice_descriptions[choice]}"
                for choice in option.choices
                if choice in option.choice_descriptions
            ]
            choice_suffix = f"\n  " + "\n  ".join(choice_details) if choice_details else ""
            rows.append(f"{option.option_id}: {option.option_type} ({required}{secret}{choices}){description}{choice_suffix}")
        return "\n".join(rows)

    def _tag_count(self, tag: str) -> int:
        return sum(1 for package in self.manager.list().packages if tag in package.tags)

    def _print_repositories(self) -> None:
        table = Table(title="Package repositories")
        table.add_column("alias")
        table.add_column("repository")
        table.add_column("type")
        table.add_column("status")
        table.add_column("packages")
        table.add_column("ref")
        table.add_column("commit")
        table.add_column("root")
        for status in self.manager.repositories.statuses:
            table.add_row(
                status.alias,
                status.name or status.repository_id or "(unknown)",
                status.source_type,
                "ready" if status.ready else f"error: {status.error}",
                str(status.package_count),
                status.ref or "",
                status.commit[:12] if status.commit else "",
                str(status.root or ""),
            )
        self.console.print(table)

    def _print_repository_detail(self, status) -> None:
        table = Table(title=f"Repository: {status.alias}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("alias", status.alias)
        table.add_row("repository id", status.repository_id or "(unknown)")
        table.add_row("name", status.name or "(unknown)")
        table.add_row("type", status.source_type)
        table.add_row("status", "ready" if status.ready else f"error: {status.error}")
        table.add_row("packages", str(status.package_count))
        table.add_row("ref", status.ref or "(none)")
        table.add_row("commit", status.commit or "(none)")
        table.add_row("root", str(status.root or ""))
        self.console.print(table)

    def _repository_status(self, alias: str):
        return next((status for status in self.manager.repositories.statuses if status.alias == alias), None)

    def _repository_ref_root(self, status) -> str:
        parts = []
        if status.ref:
            parts.append(f"ref={status.ref}")
        if status.commit:
            parts.append(f"commit={status.commit[:12]}")
        if status.root:
            parts.append(str(status.root))
        if status.error and not status.ready:
            parts.append(str(status.error))
        return " | ".join(parts)

    def _reload_manager(self) -> None:
        repositories = load_package_repository_collection(
            home=self.home,
            repository_configs=self.host_config.packages.repositories,
        )
        self.manager = PackageManager(agents_root=self.version_store.agents_root, repository=repositories)

    def _repository_config_from_location(
        self,
        *,
        location: str,
        ref: str | None,
        subdir: str | None,
        trusted: bool,
    ) -> HostPackageRepositoryConfig:
        if self._looks_like_git_url(location):
            return HostPackageRepositoryConfig(type="git", url=location, ref=ref, subdir=subdir, trusted=trusted)
        return HostPackageRepositoryConfig(
            type="path",
            path=str(Path(location).expanduser().resolve()),
            subdir=subdir,
            trusted=trusted,
        )

    def _looks_like_git_url(self, value: str) -> bool:
        return (
            value.startswith(("http://", "https://", "ssh://", "git://"))
            or value.startswith("git@")
            or value.endswith(".git")
        )

    def _installed_ref(self, item) -> str:
        return f"{item.repository_alias}/{item.package_id}" if item.repository_alias else item.package_id


def run_package_wizard(
    *,
    manager: PackageManager,
    version_store: VersionStore,
    home: Path,
    host_config: HostConfig,
    default_core_id: str = "assistant",
    console: Console | None = None,
    prompt: PackagePrompt | None = None,
) -> None:
    PackageWizard(
        manager=manager,
        version_store=version_store,
        home=home,
        host_config=host_config,
        default_core_id=default_core_id,
        console=console,
        prompt=prompt,
    ).run()
