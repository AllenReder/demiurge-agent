from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from demiurge.packages import PackageManager, PackageOperationError, PresetInfo, PresetOption
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

        index = {"value": max(0, min(default_index, len(choices) - 1))}

        def _text():
            rows: list[tuple[str, str]] = [("class:title", f"{title}\n")]
            for offset, choice in enumerate(choices):
                selected = offset == index["value"]
                prefix = "> " if selected else "  "
                style = "reverse" if selected else ""
                detail = f" - {choice.description}" if choice.description else ""
                rows.append((style, f"{prefix}{choice.label}{detail}\n"))
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
        console: Console | None = None,
        prompt: PackagePrompt | None = None,
        default_core_id: str = "assistant",
    ) -> None:
        self.manager = manager
        self.version_store = version_store
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
                        SelectChoice("browse", "Browse catalog", "View presets and install"),
                        SelectChoice("installed", "Installed packages", "View and uninstall"),
                        SelectChoice("exit", "Exit"),
                    ],
                )
                if action == "browse":
                    self._browse_catalog(core_id)
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

    def _browse_catalog(self, core_id: str) -> None:
        while True:
            result = self.manager.list(core_id=core_id)
            installed_ids = {item.preset_id for item in result.installed}
            choices = [
                SelectChoice(
                    preset.preset_id,
                    f"{preset.preset_id}{' [installed]' if preset.preset_id in installed_ids else ''}",
                    preset.summary,
                )
                for preset in result.presets
            ]
            choices.append(SelectChoice("back", "Back"))
            selected = self.prompt.select("Browse catalog", choices)
            if selected == "back":
                return
            preset = self.manager.catalog.presets[selected]
            self._print_preset(preset, installed=selected in installed_ids)
            actions = [SelectChoice("install", "Install"), SelectChoice("back", "Back")]
            if selected in installed_ids:
                actions = [SelectChoice("back", "Back")]
            action = self.prompt.select(f"Preset {selected}", actions)
            if action == "install":
                self._install(core_id, preset)

    def _installed_packages(self, core_id: str) -> None:
        while True:
            installed = self.manager.list(core_id=core_id).installed
            if not installed:
                self.console.print("No packages installed.")
                return
            self._print_installed(core_id)
            choices = [SelectChoice(item.preset_id, item.preset_id, ", ".join(item.tags)) for item in installed]
            choices.append(SelectChoice("back", "Back"))
            selected = self.prompt.select("Installed packages", choices)
            if selected == "back":
                return
            record = next(item for item in installed if item.preset_id == selected)
            self._print_installed_detail(core_id, selected)
            action = self.prompt.select(
                f"Installed preset {selected}",
                [SelectChoice("uninstall", "Uninstall"), SelectChoice("back", "Back")],
            )
            if action != "uninstall":
                continue
            if not self.prompt.confirm(f"Uninstall {selected} from {core_id}?", default=False):
                self.console.print("Uninstall canceled.")
                continue
            result = self.manager.uninstall(core_id=core_id, preset_id=record.preset_id)
            self.console.print(f"uninstalled {result.preset_id} for {result.core_id}")

    def _install(self, core_id: str, preset: PresetInfo) -> None:
        warnings = self.manager.install_warnings(core_id=core_id, preset_id=preset.preset_id)
        if warnings:
            self.console.print(Panel("\n".join(warnings), title="Tag conflict warning", style="yellow"))
            if not self.prompt.confirm("Install anyway?", default=False):
                self.console.print("Install canceled.")
                return
        answers = self._collect_options(preset)
        try:
            result = self.manager.install(core_id=core_id, preset_id=preset.preset_id, option_answers=answers)
        except PackageOperationError as exc:
            self.console.print(f"[red]Install failed:[/red] {exc}")
            return
        self.console.print(f"installed {result.preset_id} for {result.core_id}")
        for warning in result.warnings:
            self.console.print(f"warning: {warning}")

    def _collect_options(self, preset: PresetInfo) -> dict[str, object]:
        answers: dict[str, object] = {}
        for option in preset.options:
            answers[option.option_id] = self._collect_option(option)
        return answers

    def _collect_option(self, option: PresetOption) -> object:
        if option.option_type == "bool":
            default = bool(option.default) if option.has_default and option.default is not None else False
            return self.prompt.confirm(option.prompt, default=default)
        if option.option_type == "choice":
            default_index = option.choices.index(option.default) if option.default in option.choices else 0
            return self.prompt.select(
                option.prompt,
                [SelectChoice(choice, choice) for choice in option.choices],
                default_index=default_index,
            )
        default = str(option.default) if option.has_default and option.default is not None else None
        value = self.prompt.input(option.prompt, default=default, secret=option.secret)
        if value == "" and not option.required and default is None:
            return None
        if value == "" and default is not None:
            return default
        return value

    def _print_preset(self, preset: PresetInfo, *, installed: bool) -> None:
        table = Table(title=f"Package preset: {preset.preset_id}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("name", preset.name)
        table.add_row("feature", preset.feature_id)
        table.add_row("tags", ", ".join(preset.tags))
        table.add_row("summary", preset.summary)
        table.add_row("installed", "yes" if installed else "no")
        table.add_row("options", self._format_options(preset))
        table.add_row(
            "components",
            "\n".join(
                f"{component.kind}:{component.source}"
                + (f" -> {component.target}" if component.target else "")
                + (f" -> core {component.target_core_id}" if component.target_core_id else "")
                for component in preset.components
            ),
        )
        self.console.print(table)

    def _print_installed(self, core_id: str) -> None:
        result = self.manager.list(core_id=core_id)
        table = Table(title=f"Installed packages: {core_id}")
        table.add_column("preset")
        table.add_column("tags")
        table.add_column("installed")
        for item in result.installed:
            table.add_row(item.preset_id, ", ".join(item.tags), item.installed_at)
        self.console.print(table)

    def _print_installed_detail(self, core_id: str, preset_id: str) -> None:
        record = next(item for item in self.manager.list(core_id=core_id).installed if item.preset_id == preset_id)
        table = Table(title=f"Installed preset: {preset_id}")
        table.add_column("field")
        table.add_column("value")
        table.add_row("tags", ", ".join(record.tags))
        table.add_row("options", "\n".join(f"{key}: {value}" for key, value in record.options.items()) or "(none)")
        table.add_row(
            "components",
            "\n".join(
                f"{component.get('kind')}:{component.get('target') or component.get('target_core_id')}"
                for component in record.components
            ),
        )
        self.console.print(table)

    def _format_options(self, preset: PresetInfo) -> str:
        if not preset.options:
            return "(none)"
        rows = []
        for option in preset.options:
            required = "required" if option.required else "optional"
            secret = ", secret" if option.secret else ""
            choices = f", choices={','.join(option.choices)}" if option.choices else ""
            rows.append(f"{option.option_id}: {option.option_type} ({required}{secret}{choices})")
        return "\n".join(rows)


def run_package_wizard(
    *,
    manager: PackageManager,
    version_store: VersionStore,
    default_core_id: str = "assistant",
    console: Console | None = None,
    prompt: PackagePrompt | None = None,
) -> None:
    PackageWizard(
        manager=manager,
        version_store=version_store,
        default_core_id=default_core_id,
        console=console,
        prompt=prompt,
    ).run()
