from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from .app import HostConfig, create_app, ensure_runtime_defaults, init_runtime, load_host_config, refresh_runtime, source_agents_root
from demiurge.channels.gateway import GatewayConfigError, run_gateway
from demiurge.diagnostics.doctor import DoctorReport, DoctorRuntime
from demiurge.packages import PackageCatalog, PackageManager, default_catalog_root
from demiurge.package_wizard import run_package_wizard
from demiurge.storage import VersionStore
from demiurge.ui.tui import run_tui_from_args
from demiurge.util import default_home


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="demiurge")
    parser.add_argument("--home", type=Path, default=default_home(), help="Runtime home directory")
    parser.add_argument("--core", default=None, help="Core id to run")
    parser.add_argument("--agents-root", type=Path, default=None, help="Source agents root override")
    parser.add_argument("--provider", default="auto", choices=["auto", "fake", "openai", "openai-compatible"])
    parser.add_argument("--model", default=None, help="Model override for real providers")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible provider base URL override")
    parser.add_argument("--api-key", default=None, help="OpenAI-compatible provider API key override")
    parser.add_argument("--fake-script", type=Path, default=None, help="Fake provider script JSON")
    parser.add_argument("--workspace", type=Path, default=None, help="Workspace root for file and terminal tools")
    parser.add_argument("--session", default=None, help="Session id to create or resume")
    parser.add_argument("--resume", default=None, help="Existing session id to resume")
    parser.add_argument(
        "--tool-display",
        default=None,
        choices=["quiet", "summary", "full"],
        help="TUI tool call display level",
    )
    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser("init", help="Initialize or refresh the runtime assistant agent")
    init_parser.add_argument("--home", dest="init_home", type=Path, default=None, help="Runtime home directory")
    init_parser.add_argument("--core", dest="init_core", default=None, help="Core id to initialize")
    init_parser.add_argument("--check", action="store_true", help="Check runtime/source drift without writing files")
    init_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    init_parser.add_argument(
        "--refresh",
        choices=["assistant", "evolver", "global", "all"],
        default=None,
        help="Refresh runtime templates after backing up existing files",
    )
    init_parser.add_argument(
        "--agents-root",
        dest="init_agents_root",
        type=Path,
        default=None,
        help="Source agents root override",
    )
    doctor_parser = subparsers.add_parser("doctor", help="Check runtime/source template drift")
    doctor_parser.add_argument("--core", dest="doctor_core", default=None, help="Core id to check")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_parser = subparsers.add_parser("package", help="Interactively manage, list, install, or uninstall agent package presets")
    package_parser.add_argument("--catalog-root", type=Path, default=None, help="Agent catalog root")
    package_subparsers = package_parser.add_subparsers(dest="package_command")
    package_list = package_subparsers.add_parser("list", help="List catalog presets and optionally installed packages")
    package_list.add_argument("--core", dest="package_core", default=None, help="Runtime core id to include installed state")
    package_list.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_install = package_subparsers.add_parser("install", help="Install a preset into a runtime core")
    package_install.add_argument("preset", help="Preset id to install")
    package_install.add_argument("--core", dest="package_core", required=True, help="Target runtime core id")
    package_install.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_uninstall = package_subparsers.add_parser("uninstall", help="Uninstall a preset from a runtime core")
    package_uninstall.add_argument("preset", help="Preset id to uninstall")
    package_uninstall.add_argument("--core", dest="package_core", required=True, help="Target runtime core id")
    package_uninstall.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    update_parser = subparsers.add_parser("update", help="Update a managed demiurge checkout")
    update_parser.add_argument("--home", dest="update_home", type=Path, default=None, help="Runtime home directory")
    update_parser.add_argument(
        "--install-dir",
        type=Path,
        default=None,
        help="Managed checkout directory; defaults to <home>/demiurge-agent",
    )
    update_parser.add_argument("--ref", default=None, help="Optional branch, tag, or commit to check out before syncing")
    update_parser.add_argument("--skip-init-check", action="store_true", help="Skip read-only runtime/source drift check")
    gateway_parser = subparsers.add_parser("gateway", help="Run enabled external channels for the selected core")
    _add_gateway_args(gateway_parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.session and args.resume:
        raise SystemExit("--session and --resume cannot be used together")
    if args.command == "init":
        home = args.init_home or args.home or default_home()
        host_config = _host_config_or_default(home)
        core_id = args.init_core or args.core or host_config.runtime.default_core or "assistant"
        agents_root = args.init_agents_root or args.agents_root
        if args.check:
            report = DoctorRuntime(
                home=home,
                source_agents_root=source_agents_root(agents_root),
                core_id=core_id,
            ).run()
            _print_doctor_report(report, as_json=args.json)
            return
        if args.refresh:
            result = refresh_runtime(
                home=home,
                core_id=core_id,
                target=args.refresh,
                agents_root=agents_root,
                reason="cli refresh",
            )
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                _print_refresh_result(result)
            return
        result = init_runtime(
            home=home,
            core_id=core_id,
            agents_root=agents_root,
            reason="cli init",
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"initialized {result['core_id']}@{result['active_version']} at {result['active_path']}")
            if result["previous_stable_version"]:
                print(f"backed up previous active version: {result['previous_stable_version']}")
        return

    if args.command == "doctor":
        host_config = _host_config_or_default(args.home or default_home())
        report = DoctorRuntime(
            home=args.home or default_home(),
            source_agents_root=source_agents_root(args.agents_root),
            core_id=args.doctor_core or args.core or host_config.runtime.default_core or "assistant",
        ).run()
        _print_doctor_report(report, as_json=args.json)
        return

    if args.command == "package":
        _apply_host_config_defaults(args)
        _handle_package_command(args)
        return

    if args.command == "update":
        result = _handle_update_command(args)
        print(f"updated managed checkout: {result['install_dir']}")
        print(f"home: {result['home']}")
        if result["init_check"]:
            print("runtime check: demiurge init --check completed")
        else:
            print("runtime check: skipped")
        return

    if args.command == "gateway":
        _apply_host_config_defaults(args)
        app = create_app(
            home=args.home,
            core_id=args.core,
            agents_root=args.agents_root,
            provider_name=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            fake_script=args.fake_script,
            workspace=args.workspace,
            tool_display=args.tool_display,
            session_id=args.resume or args.session,
            resume_required=args.resume is not None,
        )
        try:
            run_gateway(app)
        except GatewayConfigError as exc:
            raise SystemExit(str(exc)) from None
        return

    _apply_host_config_defaults(args)
    run_tui_from_args(args)
    return


def _add_gateway_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", type=Path, default=argparse.SUPPRESS, help="Runtime home directory")
    parser.add_argument("--core", default=argparse.SUPPRESS, help="Core id to run")
    parser.add_argument("--agents-root", type=Path, default=argparse.SUPPRESS, help="Source agents root override")
    parser.add_argument("--provider", default=argparse.SUPPRESS, choices=["auto", "fake", "openai", "openai-compatible"])
    parser.add_argument("--model", default=argparse.SUPPRESS, help="Model override for real providers")
    parser.add_argument("--base-url", default=argparse.SUPPRESS, help="OpenAI-compatible provider base URL override")
    parser.add_argument("--api-key", default=argparse.SUPPRESS, help="OpenAI-compatible provider API key override")
    parser.add_argument("--fake-script", type=Path, default=argparse.SUPPRESS, help="Fake provider script JSON")
    parser.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root for file and terminal tools")
    parser.add_argument("--session", default=argparse.SUPPRESS, help="Session id to create or resume")
    parser.add_argument("--resume", default=argparse.SUPPRESS, help="Existing session id to resume")
    parser.add_argument(
        "--tool-display",
        default=argparse.SUPPRESS,
        choices=["quiet", "summary", "full"],
        help="Gateway tool call display level",
    )


def _host_config_or_default(home: Path) -> HostConfig:
    return load_host_config(home / "config.yaml")[0]


def _apply_host_config_defaults(args: argparse.Namespace) -> None:
    host_config = _host_config_or_default(args.home or default_home())
    if args.core is None:
        args.core = host_config.runtime.default_core or "assistant"
    if args.workspace is None and host_config.runtime.workspace is not None and not _workspace_env_value():
        args.workspace = host_config.runtime.workspace
    if not hasattr(args, "channel_busy_mode"):
        args.channel_busy_mode = host_config.channel.busy_mode


def _workspace_env_value() -> str | None:
    return os.environ.get("DEMIURGE_WORKSPACE") or None


def _handle_update_command(args: argparse.Namespace) -> dict[str, object]:
    home = (args.update_home or args.home or default_home()).expanduser().resolve()
    install_dir = (args.install_dir or (home / "demiurge-agent")).expanduser().resolve()
    if not (install_dir / ".git").is_dir():
        raise SystemExit(
            f"managed checkout not found at {install_dir}; "
            "install with scripts/install.sh or pass --install-dir"
        )
    _run_update_command(["git", "fetch", "--all", "--prune"], cwd=install_dir)
    if args.ref:
        _run_update_command(["git", "checkout", args.ref], cwd=install_dir)
    else:
        _run_update_command(["git", "pull", "--ff-only"], cwd=install_dir)
    _run_update_command(["uv", "sync"], cwd=install_dir)
    init_check = not args.skip_init_check
    if init_check:
        _run_update_command(["uv", "run", "demiurge", "init", "--home", str(home), "--check"], cwd=install_dir)
    return {"home": str(home), "install_dir": str(install_dir), "init_check": init_check}


def _run_update_command(command: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"update command failed ({exc.returncode}): {' '.join(command)}") from exc


def _print_doctor_report(report: DoctorReport, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return
    counts = report.counts()
    print(
        f"doctor: {counts.get('error', 0)} error(s), "
        f"{counts.get('warning', 0)} warning(s), {counts.get('ok', 0)} ok"
    )
    for finding in report.findings:
        print(f"[{finding.severity}] {finding.code}: {finding.message}")
        if finding.details:
            print(f"  details: {json.dumps(finding.details, ensure_ascii=False, sort_keys=True)}")
        if finding.remediation:
            print(f"  remediation: {finding.remediation}")


def _print_refresh_result(result: dict[str, object]) -> None:
    print(f"refreshed {result['target']} from {result['source_agents_root']}")
    items = result.get("items") or {}
    if isinstance(items, dict):
        for name, item in items.items():
            print(f"- {name}: {item}")


def _handle_package_command(args: argparse.Namespace) -> None:
    home = args.home or default_home()
    version_store = VersionStore(home)
    target_core = getattr(args, "package_core", None)
    if target_core:
        ensure_runtime_defaults(version_store, source_agents_root(args.agents_root), requested_core_id=target_core)
    catalog = PackageCatalog.load(default_catalog_root(args.catalog_root))
    manager = PackageManager(version_store=version_store, catalog=catalog)
    if args.package_command is None:
        ensure_runtime_defaults(
            version_store,
            source_agents_root(args.agents_root),
            requested_core_id=args.core or "assistant",
        )
        run_package_wizard(
            manager=manager,
            version_store=version_store,
            default_core_id=args.core or "assistant",
        )
        return
    if args.package_command == "list":
        result = manager.list(core_id=target_core)
        if args.json:
            print(json.dumps(_package_list_to_dict(result), indent=2, ensure_ascii=False))
            return
        _print_package_list(result, core_id=target_core)
        return
    if args.package_command == "install":
        result = manager.install(core_id=target_core, preset_id=args.preset)
        if args.json:
            print(json.dumps(_package_operation_to_dict(result), indent=2, ensure_ascii=False))
            return
        _print_package_operation(result)
        return
    if args.package_command == "uninstall":
        result = manager.uninstall(core_id=target_core, preset_id=args.preset)
        if args.json:
            print(json.dumps(_package_operation_to_dict(result), indent=2, ensure_ascii=False))
            return
        _print_package_operation(result)
        return
    raise SystemExit(f"unknown package command: {args.package_command}")


def _print_package_list(result, *, core_id: str | None) -> None:
    print(f"catalog: {result.catalog.catalog_id} - {result.catalog.name}")
    installed_ids = {item.preset_id for item in result.installed}
    print("presets:")
    for preset in result.presets:
        marker = "*" if preset.preset_id in installed_ids else " "
        print(f"{marker} {preset.preset_id} [{preset.feature_id}] - {preset.summary}")
    if core_id:
        print(f"installed for {core_id}:")
        if not result.installed:
            print("  (none)")
        for item in result.installed:
            print(f"  - {item.preset_id} ({', '.join(item.tags) or 'no tags'})")


def _print_package_operation(result) -> None:
    print(f"{result.action}ed {result.preset_id} for {result.core_id}")
    print(f"registry: {result.registry_path}")
    for component in result.components:
        if component.get("kind") == "core":
            print(f"- core {component.get('target_core_id')}")
        else:
            print(f"- {component.get('kind')} {component.get('target')}")
    for warning in result.warnings:
        print(f"warning: {warning}")


def _package_list_to_dict(result) -> dict[str, object]:
    return {
        "catalog": {
            "id": result.catalog.catalog_id,
            "name": result.catalog.name,
            "summary": result.catalog.summary,
            "root": str(result.catalog.root),
        },
        "features": [
            {
                "id": feature.feature_id,
                "name": feature.name,
                "summary": feature.summary,
                "tags": feature.tags,
            }
            for feature in result.features
        ],
        "presets": [
            {
                "id": preset.preset_id,
                "name": preset.name,
                "summary": preset.summary,
                "feature": preset.feature_id,
                "tags": preset.tags,
                "components": [
                    {
                        "id": component.component_id,
                        "kind": component.kind,
                        "source": component.source,
                        "target": component.target,
                        "target_core_id": component.target_core_id,
                        "config": component.config,
                    }
                    for component in preset.components
                ],
                "options": [_preset_option_to_dict(option) for option in preset.options],
                "writes": [
                    {
                        "option": write.option_id,
                        "component": write.component_id,
                        "path": write.path,
                    }
                    for write in preset.writes
                ],
            }
            for preset in result.presets
        ],
        "installed": [
            {
                "preset_id": item.preset_id,
                "catalog_id": item.catalog_id,
                "feature_id": item.feature_id,
                "tags": item.tags,
                "components": item.components,
                "installed_at": item.installed_at,
                "warnings": item.warnings,
                "options": item.options,
            }
            for item in result.installed
        ],
    }


def _package_operation_to_dict(result) -> dict[str, object]:
    return {
        "action": result.action,
        "core_id": result.core_id,
        "preset_id": result.preset_id,
        "components": result.components,
        "warnings": result.warnings,
        "registry_path": str(result.registry_path),
    }


def _preset_option_to_dict(option) -> dict[str, object]:
    default = option.default
    if option.secret and default is not None and default != "":
        default = "<redacted>"
    return {
        "id": option.option_id,
        "type": option.option_type,
        "prompt": option.prompt,
        "default": default if option.has_default else None,
        "has_default": option.has_default,
        "required": option.required,
        "choices": option.choices,
        "secret": option.secret,
    }
