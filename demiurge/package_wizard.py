from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from demiurge.app import HostConfig, HostPackageRepositoryConfig, write_host_config
from demiurge.packages import PackageInfo, PackageManager, PackageOperationError, PackageOption
from demiurge.packages import (
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


class PackagePrompt(Protocol):
    def select(self, title: str, choices: list[SelectChoice], *, default_index: int = 0) -> str:
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
            core_id = self._select_core()
            self.console.print(Panel(f"Managing packages for [bold]{core_id}[/bold]", title="demiurge package"))
            while True:
                action = self.prompt.select(
                    "Package manager",
                    [
                        SelectChoice("all", "All packages", "Browse all package repository packages"),
                        SelectChoice("search", "Search packages", "Filter by package id, name, summary, or tag"),
                        SelectChoice("tags", "Browse by tag", "View packages grouped by tag"),
                        SelectChoice("repositories", "Repositories", "Manage package repositories"),
                        SelectChoice("installed", "Installed packages", "View and uninstall"),
                        SelectChoice("exit", "Exit"),
                    ],
                )
                if action == "search":
                    query = self.prompt.input("Search query").strip()
                    self._browse_packages(core_id, self._search_packages(query), title=f"Search: {query or '(all)'}")
                elif action == "tags":
                    self._browse_tags(core_id)
                elif action == "all":
                    self._browse_packages(core_id, self.manager.list(core_id=core_id).packages, title="All packages")
                elif action == "repositories":
                    self._repositories()
                elif action == "installed":
                    self._installed_packages(core_id)
                elif action == "exit":
                    return
        except KeyboardInterrupt:
            self.console.print("Canceled.")

    def _select_core(self) -> str:
        core_ids = [
            core_id
            for core_id in self.version_store.list_core_ids()
            if (self.version_store.active_core_path(core_id) / "agent.yaml").exists()
        ]
        if not core_ids:
            raise PackageOperationError("no runtime cores found")
        default_index = core_ids.index(self.default_core_id) if self.default_core_id in core_ids else 0
        return self.prompt.select(
            "Select target runtime core",
            [SelectChoice(core_id, core_id, str(self.version_store.active_core_path(core_id))) for core_id in core_ids],
            default_index=default_index,
        )

    def _browse_tags(self, core_id: str) -> None:
        result = self.manager.list(core_id=core_id)
        choices = [SelectChoice(tag, tag, f"{self._tag_count(tag)} package(s)") for tag in result.tags]
        choices.append(SelectChoice("back", "Back"))
        selected = self.prompt.select("Browse by tag", choices)
        if selected == "back":
            return
        tagged = self.manager.list(core_id=core_id, tag=selected).packages
        self._browse_packages(core_id, tagged, title=f"Tag: {selected}")

    def _search_packages(self, query: str) -> list[PackageInfo]:
        packages = self.manager.list().packages
        if not query:
            return packages
        normalized = query.lower()
        return [
            package
            for package in packages
            if normalized in package.package_id.lower()
            or normalized in package.name.lower()
            or normalized in package.summary.lower()
            or any(normalized in tag.lower() for tag in package.tags)
        ]

    def _browse_packages(self, core_id: str, packages: list[PackageInfo], *, title: str) -> None:
        while True:
            installed_ids = {item.package_id for item in self.manager.list(core_id=core_id).installed}
            if not packages:
                self.console.print("No packages found.")
                return
            choices = [
                SelectChoice(
                    package.ref,
                    f"{package.ref}{' [installed]' if package.package_id in installed_ids else ''}",
                    package.summary,
                )
                for package in packages
            ]
            choices.append(SelectChoice("back", "Back"))
            selected = self.prompt.select(title, choices)
            if selected == "back":
                return
            package = self.manager.repositories.resolve_package_ref(selected)
            self._package_detail(core_id, package)

    def _package_detail(self, core_id: str, package: PackageInfo) -> None:
        installed_ids = {item.package_id for item in self.manager.list(core_id=core_id).installed}
        installed = package.package_id in installed_ids
        self._print_package(package, installed=installed)
        actions = [SelectChoice("back", "Back")]
        if not installed:
            actions.insert(0, SelectChoice("install", "Install"))
        action = self.prompt.select(f"Package {package.ref}", actions)
        if action != "install":
            return
        answers = self._collect_options(package)
        try:
            preview = self.manager.preview_install(core_id=core_id, package_id=package.ref, option_answers=answers)
        except PackageOperationError as exc:
            self.console.print(f"[red]Install blocked:[/red] {exc}")
            return
        self._print_preview(preview)
        if not self.prompt.confirm(f"Install {package.ref} into {core_id}?", default=False):
            self.console.print("Install canceled.")
            return
        try:
            result = self.manager.install(core_id=core_id, package_id=package.ref, option_answers=answers)
        except PackageOperationError as exc:
            self.console.print(f"[red]Install failed:[/red] {exc}")
            return
        self.console.print(f"installed {result.package_ref} for {result.core_id}")
        for warning in result.warnings:
            self.console.print(f"warning: {warning}")

    def _installed_packages(self, core_id: str) -> None:
        while True:
            installed = self.manager.list(core_id=core_id).installed
            if not installed:
                self.console.print("No packages installed.")
                return
            self._print_installed(core_id)
            choices = [
                SelectChoice(
                    self._installed_ref(item),
                    self._installed_ref(item),
                    ", ".join(item.tags),
                )
                for item in installed
            ]
            choices.append(SelectChoice("back", "Back"))
            selected = self.prompt.select("Installed packages", choices)
            if selected == "back":
                return
            record = next(item for item in installed if self._installed_ref(item) == selected)
            self._print_installed_detail(core_id, selected)
            try:
                preview = self.manager.preview_uninstall(core_id=core_id, package_id=selected)
            except PackageOperationError as exc:
                self.console.print(f"[red]Uninstall blocked:[/red] {exc}")
                continue
            action = self.prompt.select(
                f"Installed package {selected}",
                [SelectChoice("uninstall", "Uninstall"), SelectChoice("back", "Back")],
            )
            if action != "uninstall":
                continue
            self._print_preview(preview)
            if not self.prompt.confirm(f"Uninstall {selected} from {core_id}?", default=False):
                self.console.print("Uninstall canceled.")
                continue
            result = self.manager.uninstall(core_id=core_id, package_id=selected)
            self.console.print(f"uninstalled {result.package_ref} for {result.core_id}")
            for warning in result.warnings:
                self.console.print(f"warning: {warning}")

    def _repositories(self) -> None:
        while True:
            self._print_repositories()
            action = self.prompt.select(
                "Package repositories",
                [
                    SelectChoice("sync", "Sync", "Fetch git repositories and validate configured repositories"),
                    SelectChoice("add", "Add", "Add a trusted path or git package repository"),
                    SelectChoice("remove", "Remove", "Remove a configured repository source"),
                    SelectChoice("back", "Back"),
                ],
            )
            if action == "back":
                return
            if action == "sync":
                self._sync_repository()
            elif action == "add":
                self._add_repository()
            elif action == "remove":
                self._remove_repository()

    def _sync_repository(self) -> None:
        aliases = list(self.host_config.packages.repositories)
        choices = [SelectChoice("all", "All repositories")]
        choices.extend(SelectChoice(alias, alias) for alias in aliases)
        selected = self.prompt.select("Sync repository", choices)
        targets = aliases if selected == "all" else [selected]
        for alias in targets:
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
        self._reload_manager()

    def _add_repository(self) -> None:
        alias = self.prompt.input("Repository alias").strip()
        if not alias:
            self.console.print("Repository alias is required.")
            return
        if alias in self.host_config.packages.repositories:
            self.console.print(f"Repository already exists: {alias}")
            return
        location = self.prompt.input("Git URL or local path").strip()
        if not location:
            self.console.print("Repository location is required.")
            return
        ref = self.prompt.input("Git ref", default="").strip() or None
        subdir = self.prompt.input("Subdir", default="").strip() or None
        if self._looks_like_git_url(location):
            config = HostPackageRepositoryConfig(type="git", url=location, ref=ref, subdir=subdir, trusted=False)
        else:
            config = HostPackageRepositoryConfig(
                type="path",
                path=str(Path(location).expanduser().resolve()),
                subdir=subdir,
                trusted=False,
            )
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

    def _remove_repository(self) -> None:
        aliases = [alias for alias in self.host_config.packages.repositories if alias != "builtin"]
        if not aliases:
            self.console.print("No removable repositories.")
            return
        selected = self.prompt.select("Remove repository", [SelectChoice(alias, alias) for alias in aliases] + [SelectChoice("back", "Back")])
        if selected == "back":
            return
        dependents = installed_repository_dependents(self.version_store, selected)
        if dependents:
            self.console.print("Installed package records still reference this repository: " + ", ".join(dependents))
            if not self.prompt.confirm("Remove only the repository source?", default=False):
                return
        config = self.host_config.packages.repositories.pop(selected)
        if config.type == "git":
            cache_path = package_repository_cache_root(self.home) / selected
            if cache_path.exists():
                import shutil

                shutil.rmtree(cache_path)
        write_host_config(self.home / "config.yaml", self.host_config)
        self._reload_manager()
        self.console.print(f"removed {selected}")

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

    def _print_installed(self, core_id: str) -> None:
        result = self.manager.list(core_id=core_id)
        table = Table(title=f"Installed packages: {core_id}")
        table.add_column("package")
        table.add_column("repository")
        table.add_column("tags")
        table.add_column("installed")
        for item in result.installed:
            table.add_row(self._installed_ref(item), item.repository_alias, ", ".join(item.tags), item.installed_at)
        self.console.print(table)

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
        table.add_column("type")
        table.add_column("status")
        table.add_column("packages")
        table.add_column("root")
        for status in self.manager.repositories.statuses:
            table.add_row(
                status.alias,
                status.source_type,
                "ready" if status.ready else f"error: {status.error}",
                str(status.package_count),
                str(status.root or ""),
            )
        self.console.print(table)

    def _reload_manager(self) -> None:
        repositories = load_package_repository_collection(
            home=self.home,
            repository_configs=self.host_config.packages.repositories,
        )
        self.manager = PackageManager(version_store=self.version_store, repository=repositories)

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
