from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path

from .app import (
    HostConfig,
    HostPackageRepositoryConfig,
    create_app,
    ensure_runtime_defaults,
    init_runtime,
    load_host_config,
    refresh_runtime,
    source_agents_root,
    write_host_config,
)
from demiurge.channels.gateway import GatewayConfigError, run_gateway
from demiurge.diagnostics.doctor import DoctorReport, DoctorRuntime
from demiurge.packages import (
    PackageManager,
    PackageOperationPreview,
    PackageRepositoryError,
    inspect_package_repository_candidate,
    installed_repository_dependents,
    list_package_repository_statuses,
    load_package_repository_collection,
    package_repository_cache_root,
    sync_package_repository,
)
from demiurge.package_wizard import run_package_wizard
from demiurge.provider_presets import BUILTIN_PROVIDER_PRESETS_BY_ID
from demiurge.setup_cli import handle_setup_command
from demiurge.storage import VersionStore
from demiurge.ui.tui import run_tui_from_args
from demiurge.util import default_home


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="demiurge")
    parser.add_argument("--home", type=Path, default=default_home(), help="Runtime home directory")
    parser.add_argument("--core", default=None, help="Core id to run")
    parser.add_argument("--agents-root", type=Path, default=None, help="Source agents root override")
    parser.add_argument("--provider", default="auto", help="Provider profile id, auto, or fake")
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument("--fake-script", type=Path, default=None, help="Fake provider script JSON")
    parser.add_argument("--workspace", type=Path, default=None, help="Workspace root for file and terminal tools")
    parser.add_argument("--timezone", default=None, help="Runtime IANA timezone override, e.g. Asia/Shanghai")
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
    package_parser = subparsers.add_parser("package", help="Interactively manage, list, install, or uninstall agent packages")
    package_subparsers = package_parser.add_subparsers(dest="package_command")
    package_list = package_subparsers.add_parser("list", help="List package repository packages and optionally installed packages")
    package_list.add_argument("--core", dest="package_core", default=None, help="Runtime core id to include installed state")
    package_list.add_argument("--tag", dest="package_tag", default=None, help="Filter packages by tag")
    package_list.add_argument("--repo", dest="package_repo", default=None, help="Filter packages by repository alias")
    package_list.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_install = package_subparsers.add_parser("install", help="Install a package into a runtime core")
    package_install.add_argument("package_id", help="Package id or repo/package ref to install")
    package_install.add_argument("--core", dest="package_core", required=True, help="Target runtime core id")
    package_install.add_argument(
        "--option",
        dest="package_options",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Package option value; may be repeated",
    )
    package_install.add_argument("--preview", action="store_true", help="Show install plan without writing files")
    package_install.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_uninstall = package_subparsers.add_parser("uninstall", help="Uninstall a package from a runtime core")
    package_uninstall.add_argument("package_id", help="Package id or repo/package ref to uninstall")
    package_uninstall.add_argument("--core", dest="package_core", required=True, help="Target runtime core id")
    package_uninstall.add_argument("--preview", action="store_true", help="Show uninstall plan without writing files")
    package_uninstall.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_repo = package_subparsers.add_parser("repo", help="Manage package repositories")
    package_repo_subparsers = package_repo.add_subparsers(dest="package_repo_command")
    package_repo_list = package_repo_subparsers.add_parser("list", help="List configured package repositories")
    package_repo_list.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_repo_add = package_repo_subparsers.add_parser("add", help="Add a path or git package repository")
    package_repo_add.add_argument("location", help="Git URL or local path")
    package_repo_add.add_argument("--alias", default=None, help="Local repository alias; defaults to repository.yaml id")
    package_repo_add.add_argument("--ref", default=None, help="Git branch, tag, or commit to sync")
    package_repo_add.add_argument("--subdir", default=None, help="Repository subdirectory containing repository.yaml")
    package_repo_add.add_argument("--trust", action="store_true", help="Trust this external repository's local code")
    package_repo_add.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_repo_remove = package_repo_subparsers.add_parser("remove", help="Remove a configured package repository")
    package_repo_remove.add_argument("alias", help="Repository alias to remove")
    package_repo_remove.add_argument("--force", action="store_true", help="Remove even when installed packages reference it")
    package_repo_remove.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    package_repo_sync = package_repo_subparsers.add_parser("sync", help="Sync package repository caches")
    package_repo_sync.add_argument("alias", nargs="?", default=None, help="Repository alias to sync; defaults to all")
    package_repo_sync.add_argument("--json", action="store_true", help="Print machine-readable JSON")
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
    _add_setup_parser(subparsers)
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

    if args.command == "setup":
        handle_setup_command(args)
        return

    if args.command == "gateway":
        _apply_host_config_defaults(args)
        app = create_app(
            home=args.home,
            core_id=args.core,
            agents_root=args.agents_root,
            provider_name=args.provider,
            model=args.model,
            fake_script=args.fake_script,
            workspace=args.workspace,
            tool_display=args.tool_display,
            timezone=getattr(args, "timezone", None),
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
    parser.add_argument("--provider", default=argparse.SUPPRESS, help="Provider profile id, auto, or fake")
    parser.add_argument("--model", default=argparse.SUPPRESS, help="Model override")
    parser.add_argument("--fake-script", type=Path, default=argparse.SUPPRESS, help="Fake provider script JSON")
    parser.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root for file and terminal tools")
    parser.add_argument("--timezone", default=argparse.SUPPRESS, help="Runtime IANA timezone override, e.g. Asia/Shanghai")
    parser.add_argument("--session", default=argparse.SUPPRESS, help="Session id to create or resume")
    parser.add_argument("--resume", default=argparse.SUPPRESS, help="Existing session id to resume")
    parser.add_argument(
        "--tool-display",
        default=argparse.SUPPRESS,
        choices=["quiet", "summary", "full"],
        help="Gateway tool call display level",
    )


def _add_setup_parser(subparsers: argparse._SubParsersAction) -> None:
    setup_parser = subparsers.add_parser("setup", help="Configure provider profiles and core model defaults")
    setup_subparsers = setup_parser.add_subparsers(dest="setup_command")

    status_parser = setup_subparsers.add_parser("status", help="Show provider setup status")
    status_parser.add_argument("--core", default=None, help="Runtime core id to inspect")
    status_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    providers_parser = setup_subparsers.add_parser("providers", help="Manage provider profiles")
    provider_subparsers = providers_parser.add_subparsers(dest="provider_command")
    provider_list = provider_subparsers.add_parser("list", help="List configured provider profiles")
    provider_list.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    provider_show = provider_subparsers.add_parser("show", help="Show one provider profile")
    provider_show.add_argument("provider_id", help="Provider profile id")
    provider_show.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    for command in ("add", "edit"):
        provider_edit = provider_subparsers.add_parser(command, help=f"{command.title()} a provider profile")
        provider_edit.add_argument("provider_id", help="Provider profile id")
        provider_edit.add_argument(
            "--preset",
            choices=sorted(BUILTIN_PROVIDER_PRESETS_BY_ID),
            default=None,
            help="Built-in provider preset to start from",
        )
        provider_edit.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
        provider_edit.add_argument("--api-key-env", default=None, help="Environment variable containing the API key")
        provider_edit.add_argument("--api-key", default=None, help="Direct API key value")
        provider_edit.add_argument("--write-env", action="store_true", help="Write --api-key into <home>/.env")
        provider_edit.add_argument("--set-default", action="store_true", help="Use this profile as the host default")
        provider_edit.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    provider_remove = provider_subparsers.add_parser("remove", help="Remove a provider profile")
    provider_remove.add_argument("provider_id", help="Provider profile id")
    provider_remove.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    provider_default = provider_subparsers.add_parser("set-default", help="Set the host default provider profile")
    provider_default.add_argument("provider_id", help="Provider profile id")
    provider_default.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    provider_test = provider_subparsers.add_parser("test", help="Run an explicit live provider test")
    provider_test.add_argument("provider_id", help="Provider profile id")
    provider_test.add_argument("--model", default=None, help="Model name to test")
    provider_test.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    model_parser = setup_subparsers.add_parser("model", help="Manage core model defaults")
    model_subparsers = model_parser.add_subparsers(dest="model_command")
    model_set = model_subparsers.add_parser("set", help="Set model.provider and model.model_name in a runtime core")
    model_set.add_argument("--core", required=True, help="Runtime core id")
    model_set.add_argument("--provider", required=True, help="Provider profile id or fake")
    model_set.add_argument("--model", required=True, help="Model name")
    model_set.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    timezone_parser = setup_subparsers.add_parser("timezone", help="Manage host runtime timezone")
    timezone_subparsers = timezone_parser.add_subparsers(dest="timezone_command")
    timezone_set = timezone_subparsers.add_parser("set", help="Set runtime.timezone in host config")
    timezone_set.add_argument("timezone", help="IANA timezone name, e.g. Asia/Shanghai")
    timezone_set.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    timezone_clear = timezone_subparsers.add_parser("clear", help="Clear runtime.timezone from host config")
    timezone_clear.add_argument("--json", action="store_true", help="Print machine-readable JSON")


def _host_config_or_default(home: Path) -> HostConfig:
    return load_host_config(home / "config.yaml")[0]


def _apply_host_config_defaults(args: argparse.Namespace) -> None:
    host_config = _host_config_or_default(args.home or default_home())
    if args.core is None:
        args.core = host_config.runtime.default_core or "assistant"
    if not hasattr(args, "channel_busy_mode"):
        args.channel_busy_mode = host_config.channel.busy_mode


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
    host_config = _host_config_or_default(home)
    version_store = VersionStore(home)
    if args.package_command == "repo":
        _handle_package_repo_command(args, home=home, host_config=host_config, version_store=version_store)
        return
    target_core = getattr(args, "package_core", None)
    if target_core:
        ensure_runtime_defaults(version_store, source_agents_root(args.agents_root), requested_core_id=target_core)
    repositories = load_package_repository_collection(
        home=home,
        repository_configs=host_config.packages.repositories,
        repository_alias=getattr(args, "package_repo", None),
    )
    manager = PackageManager(version_store=version_store, repository=repositories)
    if args.package_command is None:
        ensure_runtime_defaults(
            version_store,
            source_agents_root(args.agents_root),
            requested_core_id=args.core or "assistant",
        )
        run_package_wizard(
            manager=manager,
            version_store=version_store,
            home=home,
            host_config=host_config,
            default_core_id=args.core or "assistant",
        )
        return
    if args.package_command == "list":
        result = manager.list(
            core_id=target_core,
            tag=getattr(args, "package_tag", None),
            repository_alias=getattr(args, "package_repo", None),
        )
        if args.json:
            print(json.dumps(_package_list_to_dict(result), indent=2, ensure_ascii=False))
            return
        _print_package_list(result, core_id=target_core)
        return
    if args.package_command == "install":
        package_options = _parse_package_options(args.package_options)
        if args.preview:
            result = manager.preview_install(core_id=target_core, package_id=args.package_id, option_answers=package_options)
        else:
            result = manager.install(core_id=target_core, package_id=args.package_id, option_answers=package_options)
        if args.json:
            print(json.dumps(_package_operation_to_dict(result), indent=2, ensure_ascii=False))
            return
        _print_package_operation(result)
        return
    if args.package_command == "uninstall":
        if args.preview:
            result = manager.preview_uninstall(core_id=target_core, package_id=args.package_id)
        else:
            result = manager.uninstall(core_id=target_core, package_id=args.package_id)
        if args.json:
            print(json.dumps(_package_operation_to_dict(result), indent=2, ensure_ascii=False))
            return
        _print_package_operation(result)
        return
    raise SystemExit(f"unknown package command: {args.package_command}")


def _handle_package_repo_command(
    args: argparse.Namespace,
    *,
    home: Path,
    host_config: HostConfig,
    version_store: VersionStore,
) -> None:
    command = args.package_repo_command or "list"
    if command == "list":
        statuses = list_package_repository_statuses(home=home, repository_configs=host_config.packages.repositories)
        if args.json:
            print(json.dumps({"repositories": [_repository_status_to_dict(item) for item in statuses]}, indent=2, ensure_ascii=False))
            return
        _print_repository_statuses(statuses)
        return
    if command == "add":
        config = _package_repo_config_from_location(args)
        if config.type != "builtin" and not config.trusted:
            if args.json or not sys.stdin.isatty():
                raise SystemExit("external package repositories require --trust in non-interactive mode")
        try:
            candidate = inspect_package_repository_candidate(
                home=home,
                config=config.model_dump(mode="python", exclude_none=True),
            )
        except PackageRepositoryError as exc:
            raise SystemExit(str(exc)) from None
        alias = str(args.alias or candidate.repository_id or "").strip()
        if not alias:
            raise SystemExit("package repository metadata did not include an id; rerun with --alias")
        if alias in host_config.packages.repositories:
            if args.alias:
                raise SystemExit(f"package repository already exists: {alias}")
            raise SystemExit(f"package repository alias already exists: {alias}; rerun with --alias")
        if config.type != "builtin" and not config.trusted:
            if not _confirm_trust(alias, args.location, candidate):
                raise SystemExit("package repository was not trusted")
            config.trusted = True
        status = sync_package_repository(home=home, alias=alias, config=config.model_dump(mode="python", exclude_none=True))
        host_config.packages.repositories[alias] = config
        write_host_config(home / "config.yaml", host_config)
        if args.json:
            print(json.dumps(_repository_status_to_dict(status), indent=2, ensure_ascii=False))
            return
        print(f"added package repository {alias}: {status.name or status.repository_id}")
        return
    if command == "remove":
        if args.alias == "builtin":
            raise SystemExit("builtin package repository cannot be removed")
        if args.alias not in host_config.packages.repositories:
            raise SystemExit(f"unknown package repository: {args.alias}")
        dependents = installed_repository_dependents(version_store, args.alias)
        if dependents and not args.force:
            raise SystemExit(
                "package repository is still referenced by installed packages: "
                + ", ".join(dependents)
                + "; rerun with --force to remove only the repository source"
            )
        removed = host_config.packages.repositories.pop(args.alias)
        if removed.type == "git":
            cache_path = package_repository_cache_root(home) / args.alias
            if cache_path.exists():
                shutil.rmtree(cache_path)
        write_host_config(home / "config.yaml", host_config)
        result = {"removed": args.alias, "dependents": dependents, "forced": bool(args.force)}
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return
        print(f"removed package repository {args.alias}")
        if dependents:
            print("warning: installed package records still reference it: " + ", ".join(dependents))
        return
    if command == "sync":
        aliases = [args.alias] if args.alias else list(host_config.packages.repositories)
        statuses = []
        for alias in aliases:
            config = host_config.packages.repositories.get(alias)
            if config is None:
                raise SystemExit(f"unknown package repository: {alias}")
            statuses.append(sync_package_repository(home=home, alias=alias, config=config.model_dump(mode="python", exclude_none=True)))
        if args.json:
            print(json.dumps({"repositories": [_repository_status_to_dict(item) for item in statuses]}, indent=2, ensure_ascii=False))
            return
        _print_repository_statuses(statuses)
        return
    raise SystemExit(f"unknown package repo command: {command}")


def _print_package_list(result, *, core_id: str | None) -> None:
    print("repositories:")
    for repository in result.repositories:
        ready = "ready" if repository.ready else f"error: {repository.error}"
        print(f"- {repository.alias} ({repository.source_type}) - {repository.name or repository.repository_id or '(unknown)'} - {ready}")
    installed_ids = {item.package_id for item in result.installed}
    print("tags: " + (", ".join(result.tags) or "(none)"))
    print("packages:")
    for package in result.packages:
        marker = "*" if package.package_id in installed_ids else " "
        print(f"{marker} {package.ref} [{', '.join(package.tags) or 'no tags'}] - {package.summary}")
    if core_id:
        print(f"installed for {core_id}:")
        if not result.installed:
            print("  (none)")
        for item in result.installed:
            package_ref = f"{item.repository_alias}/{item.package_id}" if item.repository_alias else item.package_id
            print(f"  - {package_ref} ({', '.join(item.tags) or 'no tags'})")


def _print_package_operation(result) -> None:
    if isinstance(result, PackageOperationPreview):
        print(f"preview {result.action} {result.package_ref} for {result.core_id}")
    else:
        print(f"{result.action}ed {result.package_ref} for {result.core_id}")
    print(f"registry: {result.registry_path}")
    for component in result.components:
        if "remove" in component:
            marker = "remove" if component.get("remove") else "keep"
        elif component.get("reused"):
            marker = "reuse"
        else:
            marker = "write"
        if component.get("kind") == "core":
            print(f"- {marker} core {component.get('target_core_id')}")
        else:
            print(f"- {marker} {component.get('kind')} {component.get('target')}")
    for warning in result.warnings:
        print(f"warning: {warning}")


def _package_list_to_dict(result) -> dict[str, object]:
    return {
        "repositories": [_repository_status_to_dict(repository) for repository in result.repositories],
        "tags": result.tags,
        "packages": [
            {
                "id": package.package_id,
                "ref": package.ref,
                "repository_alias": package.repository_alias,
                "repository_id": package.repository_id,
                "repository_type": package.repository_type,
                "repository_ref": package.repository_ref,
                "repository_commit": package.repository_commit,
                "name": package.name,
                "summary": package.summary,
                "tags": package.tags,
                "manual_dependencies": package.manual_dependencies,
                "components": [
                    {
                        "id": component.component_id,
                        "kind": component.kind,
                        "source": component.source,
                        "target": component.target,
                        "target_core_id": component.target_core_id,
                        "config": component.config,
                        "when": component.when,
                    }
                    for component in package.components
                ],
                "options": [_package_option_to_dict(option) for option in package.options],
            }
            for package in result.packages
        ],
        "installed": [
            {
                "package_id": item.package_id,
                "package_ref": f"{item.repository_alias}/{item.package_id}" if item.repository_alias else item.package_id,
                "repository_alias": item.repository_alias,
                "repository_id": item.repository_id,
                "repository_type": item.repository_type,
                "repository_ref": item.repository_ref,
                "repository_commit": item.repository_commit,
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
        "preview": isinstance(result, PackageOperationPreview),
        "core_id": result.core_id,
        "package_id": result.package_id,
        "package_ref": result.package_ref,
        "repository_alias": result.repository_alias,
        "repository_id": result.repository_id,
        "repository_type": result.repository_type,
        "repository_ref": result.repository_ref,
        "repository_commit": result.repository_commit,
        "components": result.components,
        "warnings": result.warnings,
        "registry_path": str(result.registry_path),
        "options": result.options,
    }


def _print_repository_statuses(statuses) -> None:
    print("package repositories:")
    for status in statuses:
        ready = "ready" if status.ready else f"error: {status.error}"
        ref = f" ref={status.ref}" if status.ref else ""
        commit = f" commit={status.commit[:12]}" if status.commit else ""
        print(
            f"- {status.alias} ({status.source_type}) {status.name or status.repository_id or '(unknown)'} "
            f"packages={status.package_count}{ref}{commit} {ready}"
        )


def _repository_status_to_dict(status) -> dict[str, object]:
    return {
        "alias": status.alias,
        "type": status.source_type,
        "trusted": status.trusted,
        "root": str(status.root) if status.root else None,
        "repository_id": status.repository_id,
        "name": status.name,
        "summary": status.summary,
        "package_count": status.package_count,
        "ref": status.ref,
        "commit": status.commit,
        "ready": status.ready,
        "error": status.error,
    }


def _package_repo_config_from_location(args: argparse.Namespace) -> HostPackageRepositoryConfig:
    location = str(args.location).strip()
    if _looks_like_git_url(location):
        return HostPackageRepositoryConfig(
            type="git",
            url=location,
            ref=args.ref,
            subdir=args.subdir,
            trusted=bool(args.trust),
        )
    path = Path(location).expanduser().resolve()
    return HostPackageRepositoryConfig(
        type="path",
        path=str(path),
        subdir=args.subdir,
        trusted=bool(args.trust),
    )


def _looks_like_git_url(value: str) -> bool:
    return (
        value.startswith(("http://", "https://", "ssh://", "git://"))
        or value.startswith("git@")
        or value.endswith(".git")
    )


def _confirm_trust(alias: str, location: str, status) -> bool:
    name = status.name or status.repository_id or alias
    print(f"Package repository {alias} ({name}) can install local code into host-shared agent slots.")
    answer = input(f"Trust {location}? [y/N] ").strip().lower()
    return answer in {"y", "yes", "true", "1", "on"}


def _package_option_to_dict(option) -> dict[str, object]:
    default = option.default
    if option.secret and default is not None and default != "":
        default = "<redacted>"
    return {
        "id": option.option_id,
        "type": option.option_type,
        "prompt": option.prompt,
        "description": option.description,
        "default": default if option.has_default else None,
        "has_default": option.has_default,
        "required": option.required,
        "choices": option.choices,
        "choice_descriptions": option.choice_descriptions,
        "secret": option.secret,
    }


def _parse_package_options(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, sep, raw = value.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"invalid --option value, expected KEY=VALUE: {value}")
        result[key.strip()] = raw
    return result
