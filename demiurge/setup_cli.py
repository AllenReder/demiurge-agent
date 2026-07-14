from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from demiurge.app import (
    HostBuiltinProviderOverride,
    HostConfig,
    HostCustomProviderProfile,
    HostProvidersConfig,
    ResolvedHostProviderProfile,
    create_provider,
    ensure_runtime_defaults,
    load_agent_fallback,
    load_host_config,
    normalize_provider_profile_id,
    resolve_host_provider_profile,
    resolve_model_name,
    resolve_profile_api_key,
    resolve_provider_config,
    source_agents_root,
    write_default_host_config_if_missing,
    write_host_config,
)
from demiurge.core import CoreLoader, ModelInfo
from demiurge.env_file import load_runtime_env, runtime_env_path, upsert_env_value
from demiurge.gates import GateResult, GateRunner
from demiurge.package_wizard import PromptToolkitPackagePrompt, SelectChoice
from demiurge.provider_presets import BUILTIN_PROVIDER_PRESETS, ProviderPreset, get_provider_preset
from demiurge.providers import LLMMessage, LLMRequest
from demiurge.providers.profiles import get_builtin_provider_profile, is_builtin_provider
from demiurge.runtime_timezone import resolve_runtime_timezone, validate_timezone_name
from demiurge.security.private_files import require_private_runtime_permissions
from demiurge.storage import VersionStore
from demiurge.util import default_home


class SetupPrompt(Protocol):
    def select(self, title: str, choices: list[SelectChoice], *, default_index: int = 0) -> str:
        ...

    def confirm(self, message: str, *, default: bool = False) -> bool:
        ...

    def input(self, message: str, *, default: str | None = None, secret: bool = False) -> str:
        ...


@dataclass(slots=True)
class SetupContext:
    home: Path
    host_config_path: Path
    host_config: HostConfig
    version_store: VersionStore
    agents_root: Path


def handle_setup_command(args: argparse.Namespace) -> None:
    if args.setup_command == "status":
        context = load_setup_status_context(
            args.home,
            agents_root=args.agents_root,
        )
        data = setup_status(
            context,
            core_id=args.core,
            timezone_override=getattr(args, "timezone", None),
        )
        _print_result(data, as_json=args.json)
        return
    context = load_setup_context(args.home, agents_root=args.agents_root)
    if args.setup_command is None:
        run_setup_wizard(context)
        return
    if args.setup_command == "providers":
        _handle_provider_command(context, args)
        return
    if args.setup_command == "model":
        _handle_model_command(context, args)
        return
    if args.setup_command == "timezone":
        _handle_timezone_command(context, args)
        return
    raise SystemExit(f"unknown setup command: {args.setup_command}")


def load_setup_context(home: Path | None, *, agents_root: Path | None = None) -> SetupContext:
    resolved_home = (home or default_home()).expanduser().resolve()
    require_private_runtime_permissions(resolved_home)
    load_runtime_env(resolved_home)
    host_config_path = resolved_home / "config.yaml"
    write_default_host_config_if_missing(host_config_path)
    host_config = load_host_config(host_config_path)[0]
    source_agents = source_agents_root(agents_root)
    version_store = VersionStore(resolved_home)
    ensure_runtime_defaults(
        version_store,
        source_agents,
        requested_core_id=host_config.runtime.default_core or "assistant",
    )
    require_private_runtime_permissions(resolved_home)
    return SetupContext(
        home=resolved_home,
        host_config_path=host_config_path,
        host_config=host_config,
        version_store=version_store,
        agents_root=source_agents,
    )


def load_setup_status_context(
    home: Path | None,
    *,
    agents_root: Path | None = None,
) -> SetupContext:
    resolved_home = (home or default_home()).expanduser().resolve()
    load_runtime_env(resolved_home)
    host_config_path = resolved_home / "config.yaml"
    host_config = load_host_config(host_config_path)[0]
    return SetupContext(
        home=resolved_home,
        host_config_path=host_config_path,
        host_config=host_config,
        version_store=VersionStore(resolved_home),
        agents_root=source_agents_root(agents_root),
    )


def run_setup_wizard(
    context: SetupContext,
    *,
    console: Console | None = None,
    prompt: SetupPrompt | None = None,
) -> None:
    wizard = SetupWizard(context=context, console=console, prompt=prompt)
    wizard.run()


class SetupWizard:
    def __init__(
        self,
        *,
        context: SetupContext,
        console: Console | None = None,
        prompt: SetupPrompt | None = None,
    ) -> None:
        self.context = context
        self.console = console or Console()
        self.prompt = prompt or PromptToolkitPackagePrompt()

    def run(self) -> None:
        try:
            self.console.print(Panel(f"Runtime home: [bold]{self.context.home}[/bold]", title="demiurge setup"))
            while True:
                action = self.prompt.select(
                    "Setup",
                    [
                        SelectChoice("status", "Status", "Show provider and active core model configuration"),
                        SelectChoice("add-provider", "Add provider", "Create or update a provider profile"),
                        SelectChoice("set-default", "Set default provider", "Choose the host default provider profile"),
                        SelectChoice("set-model", "Set core model", "Write model.provider and model.model_name for a core"),
                        SelectChoice("set-timezone", "Set timezone", "Write runtime.timezone in host config"),
                        SelectChoice("exit", "Exit"),
                    ],
                )
                if action == "status":
                    self._print_status(setup_status(self.context))
                elif action == "add-provider":
                    self._add_provider()
                elif action == "set-default":
                    self._set_default_provider()
                elif action == "set-model":
                    self._set_core_model()
                elif action == "set-timezone":
                    self._set_timezone()
                elif action == "exit":
                    return
        except KeyboardInterrupt:
            self.console.print("Canceled.")

    def _add_provider(self) -> None:
        choices = [
            SelectChoice(preset.preset_id, preset.label, "Built-in provider") for preset in BUILTIN_PROVIDER_PRESETS
        ]
        choices.append(SelectChoice("custom", "Custom", "Any OpenAI Chat-compatible endpoint"))
        preset_id = self.prompt.select("Provider preset", choices)
        preset = get_provider_preset(preset_id)
        if preset:
            provider_id = preset.preset_id
            runtime_profile = preset.runtime_profile
            default_env = runtime_profile.env_vars[0] if runtime_profile.env_vars else ""
            base_url = None
            api_key_env = default_env
        else:
            provider_id = _normalize_setup_provider_id(self.prompt.input("Profile id", default="custom"))
            default_env = f"{provider_id.upper().replace('-', '_')}_API_KEY"
            base_url = self.prompt.input("Base URL", default="http://localhost:11434/v1").strip()
            api_key_env = self.prompt.input("API key environment variable", default=default_env).strip()
        api_key = self.prompt.input("API key", secret=True).strip()
        if preset:
            profile = HostBuiltinProviderOverride()
        else:
            profile = HostCustomProviderProfile(
                base_url=base_url or "",
                api_key_env=api_key_env or None,
            )
        write_provider_profile(
            self.context,
            provider_id=provider_id,
            profile=profile,
            set_default=self.prompt.confirm(f"Use {provider_id} as default provider?", default=False),
        )
        if api_key and api_key_env:
            upsert_env_value(runtime_env_path(self.context.home), api_key_env, api_key)
            load_runtime_env(self.context.home)
        self.context.host_config = load_host_config(self.context.host_config_path)[0]
        self.console.print(f"saved provider profile {provider_id}")
        self._set_core_model_for_provider(provider_id, preset=preset)

    def _set_default_provider(self) -> None:
        profiles = provider_profile_ids(self.context.host_config)
        if not profiles:
            self.console.print("No provider profiles configured.")
            return
        provider_id = self.prompt.select("Default provider", [SelectChoice(item, item) for item in profiles])
        set_default_provider(self.context, provider_id)
        self.context.host_config = load_host_config(self.context.host_config_path)[0]
        self.console.print(f"default provider: {provider_id}")

    def _set_core_model(self) -> None:
        core_id = self.prompt.input("Core id", default=self.context.host_config.runtime.default_core or "assistant").strip()
        profiles = ["fake"] + provider_profile_ids(self.context.host_config)
        provider_id = self.prompt.select("Provider", [SelectChoice(item, item) for item in profiles])
        model_name = self._prompt_model_name(provider_id, preset=get_provider_preset(provider_id))
        result = set_core_model(self.context, core_id=core_id, provider_id=provider_id, model_name=model_name)
        self.console.print(f"updated {result['core']} model")

    def _set_timezone(self) -> None:
        current = self.context.host_config.runtime.timezone
        timezone = self.prompt.input("Timezone", default=current or "").strip()
        result = clear_runtime_timezone(self.context) if not timezone else set_runtime_timezone(self.context, timezone)
        self.console.print(f"timezone: {result['runtime_timezone']}")

    def _set_core_model_for_provider(self, provider_id: str, *, preset: ProviderPreset | None) -> None:
        core_id = self.prompt.input("Core id", default=self.context.host_config.runtime.default_core or "assistant").strip()
        model_name = self._prompt_model_name(provider_id, preset=preset)
        result = set_core_model(self.context, core_id=core_id, provider_id=provider_id, model_name=model_name)
        self.console.print(f"updated {result['core']} model")

    def _prompt_model_name(self, provider_id: str, *, preset: ProviderPreset | None) -> str:
        default = _suggested_model_for_provider(provider_id, preset=preset)
        while True:
            model_name = self.prompt.input("Model", default=default).strip()
            if model_name:
                return model_name
            self.console.print("Model is required.")

    def _print_status(self, data: dict[str, object]) -> None:
        provider_table = Table(title="Provider Profiles")
        provider_table.add_column("id")
        provider_table.add_column("base_url")
        provider_table.add_column("api_key")
        providers = data.get("providers", {})
        if isinstance(providers, dict):
            for provider_id, item in providers.items():
                if isinstance(item, dict):
                    provider_table.add_row(provider_id, str(item.get("base_url")), str(item.get("api_key") or "not configured"))
        self.console.print(provider_table)
        core = data.get("core_model", {})
        if isinstance(core, dict):
            self.console.print(f"core {core.get('core')}: {core.get('provider')} / {core.get('model')}")
        self.console.print(f"timezone: {data.get('runtime_timezone')} ({data.get('runtime_timezone_source')})")


def setup_status(context: SetupContext, *, core_id: str | None = None, timezone_override: str | None = None) -> dict[str, object]:
    host_config, host_sources = load_host_config(context.host_config_path)
    runtime_timezone = resolve_runtime_timezone(
        override=timezone_override,
        config_value=host_config.runtime.timezone,
        config_source=host_sources.get("runtime.timezone", "config.yaml:runtime.timezone"),
    )
    core_id = core_id or host_config.runtime.default_core or "assistant"
    core_path = context.version_store.active_core_path(core_id)
    core_model: dict[str, object] = {"core": core_id, "path": str(core_path), "provider": None, "model": None}
    if (core_path / "agent.yaml").exists():
        core = CoreLoader().load(core_path)
        fallback = load_agent_fallback(context.version_store.fallback_config_path)
        model_name, model_source = resolve_model_name(core.manifest.model, fallback.model)
        provider_config = resolve_provider_config(host_config, core.manifest.model, fallback.model)
        core_model.update(
            {
                "provider": provider_config.provider_id,
                "provider_source": provider_config.provider_source,
                "provider_api_mode": provider_config.api_mode,
                "model": model_name,
                "model_source": model_source,
            }
        )
    return {
        "home": str(context.home),
        "host_config": str(context.host_config_path),
        "env_file": str(runtime_env_path(context.home)),
        "default_provider": host_config.providers.default,
        "runtime_timezone": runtime_timezone.name,
        "runtime_timezone_source": runtime_timezone.source,
        "runtime_timezone_explicit": runtime_timezone.explicit,
        "runtime_local_now": runtime_timezone.local_now().isoformat(),
        "providers": provider_profiles_dict(host_config),
        "core_model": core_model,
    }


def provider_profiles_dict(host_config: HostConfig) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for preset in BUILTIN_PROVIDER_PRESETS:
        profile = resolve_host_provider_profile(host_config, preset.preset_id)
        result[preset.preset_id] = provider_profile_dict(profile)
    for provider_id in sorted(host_config.providers.custom):
        profile = resolve_host_provider_profile(host_config, provider_id)
        result[provider_id] = provider_profile_dict(profile)
    return result


def provider_profile_dict(profile: ResolvedHostProviderProfile) -> dict[str, object]:
    _, resolved_source = resolve_profile_api_key(profile, provider_id=profile.provider_id)
    api_key_source = resolved_source
    if not api_key_source and profile.api_key_env:
        api_key_source = f"env:{profile.api_key_env} (missing)"
    return {
        "type": profile.profile_kind,
        "api_mode": profile.api_mode,
        "api_mode_source": profile.api_mode_source,
        "base_url": profile.base_url,
        "base_url_source": profile.base_url_source,
        "api_key_env": profile.api_key_env,
        "api_key": "<redacted>" if profile.api_key else None,
        "api_key_source": api_key_source or "not configured",
    }


def provider_profile_ids(host_config: HostConfig) -> list[str]:
    return sorted({*(preset.preset_id for preset in BUILTIN_PROVIDER_PRESETS), *host_config.providers.custom})


def write_provider_profile(
    context: SetupContext,
    *,
    provider_id: str,
    profile: HostBuiltinProviderOverride | HostCustomProviderProfile,
    set_default: bool = False,
) -> dict[str, object]:
    provider_id = _normalize_setup_provider_id(provider_id)
    host_config = load_host_config(context.host_config_path)[0]
    builtin = dict(host_config.providers.builtin)
    custom = dict(host_config.providers.custom)
    if is_builtin_provider(provider_id):
        if not isinstance(profile, HostBuiltinProviderOverride):
            raise SystemExit(f"builtin provider `{provider_id}` cannot be written as a custom provider")
        if _has_builtin_override(profile):
            builtin[provider_id] = profile
        else:
            builtin.pop(provider_id, None)
    else:
        if isinstance(profile, HostBuiltinProviderOverride):
            raise SystemExit(f"custom provider `{provider_id}` requires --base-url")
        custom[provider_id] = profile
    host_config.providers = HostProvidersConfig(default=host_config.providers.default, builtin=builtin, custom=custom)
    if set_default:
        host_config.providers.default = provider_id
    write_host_config(context.host_config_path, host_config)
    context.host_config = host_config
    resolved = resolve_host_provider_profile(host_config, provider_id)
    return {"provider": provider_id, "profile": provider_profile_dict(resolved), "default": host_config.providers.default}


def set_default_provider(context: SetupContext, provider_id: str) -> dict[str, object]:
    provider_id = _normalize_setup_provider_id(provider_id)
    host_config = load_host_config(context.host_config_path)[0]
    if provider_id != "fake":
        resolve_host_provider_profile(host_config, provider_id)
    host_config.providers.default = provider_id
    write_host_config(context.host_config_path, host_config)
    context.host_config = host_config
    return {"default_provider": provider_id}


def set_runtime_timezone(context: SetupContext, timezone: str) -> dict[str, object]:
    name = validate_timezone_name(timezone)
    host_config = load_host_config(context.host_config_path)[0]
    host_config.runtime.timezone = name
    write_host_config(context.host_config_path, host_config)
    context.host_config = host_config
    return {"runtime_timezone": name, "runtime_timezone_source": "config.yaml:runtime.timezone"}


def clear_runtime_timezone(context: SetupContext) -> dict[str, object]:
    host_config = load_host_config(context.host_config_path)[0]
    host_config.runtime.timezone = None
    write_host_config(context.host_config_path, host_config)
    context.host_config = host_config
    runtime_timezone = resolve_runtime_timezone(config_value=None)
    return {
        "runtime_timezone": runtime_timezone.name,
        "runtime_timezone_source": runtime_timezone.source,
    }


def remove_provider_profile(context: SetupContext, provider_id: str) -> dict[str, object]:
    provider_id = _normalize_setup_provider_id(provider_id)
    host_config = load_host_config(context.host_config_path)[0]
    builtin = dict(host_config.providers.builtin)
    custom = dict(host_config.providers.custom)
    if provider_id in builtin:
        builtin.pop(provider_id)
    elif provider_id in custom:
        custom.pop(provider_id)
    else:
        raise SystemExit(f"provider profile not found: {provider_id}")
    host_config.providers = HostProvidersConfig(default=host_config.providers.default, builtin=builtin, custom=custom)
    if host_config.providers.default == provider_id:
        host_config.providers.default = None
    write_host_config(context.host_config_path, host_config)
    context.host_config = host_config
    return {"removed": provider_id, "default_provider": host_config.providers.default}


def set_core_model(context: SetupContext, *, core_id: str, provider_id: str, model_name: str) -> dict[str, object]:
    provider_id = _normalize_setup_provider_id(provider_id)
    model_name = model_name.strip()
    if not model_name:
        raise SystemExit("model name is required")
    if provider_id != "fake":
        resolve_host_provider_profile(load_host_config(context.host_config_path)[0], provider_id)
    ensure_runtime_defaults(context.version_store, context.agents_root, requested_core_id=core_id)
    repository = context.version_store.core_repository
    gate_runner = GateRunner(project_root=Path.cwd().resolve())
    repository.prepare_live_for_edit(
        validate=lambda agents_root, changed_paths: asyncio.run(gate_runner.run(agents_root, changed_paths=changed_paths))
    )
    with repository.live_transaction(reason="setup model set"):
        core_path = context.version_store.active_core_path(core_id)
        manifest_path = core_path / "agent.yaml"
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        model = raw.setdefault("model", {})
        if not isinstance(model, dict):
            raise SystemExit(f"invalid model block in {manifest_path}")
        for key in ("model_name_env", "base_url", "base_url_env", "api_key", "api_key_env"):
            model.pop(key, None)
        model["provider"] = provider_id
        model["model_name"] = model_name
        model.setdefault("model_options", {})
        manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        changed_paths = repository.live_changed_paths()
        gates = asyncio.run(gate_runner.run(repository.active_agents_root(), changed_paths=changed_paths))
        if not gates.passed:
            raise SystemExit("setup model gates failed: " + _gate_failure_summary(gates))
        commit = repository.commit_live(reason="setup model set", summary=f"update {core_id} model config")
    return {
        "core": core_id,
        "path": str(manifest_path),
        "provider": provider_id,
        "model": model_name,
        "revision": commit.revision,
        "previous_revision": commit.previous_revision,
    }


def _gate_failure_summary(gates: GateResult) -> str:
    failures = [phase for phase in gates.phases if not phase.passed]
    return "; ".join(f"{phase.name}: {phase.detail}" for phase in failures[:5]) or "unknown gate failure"


def provider_profile_from_args(
    args: argparse.Namespace,
    *,
    provider_id: str,
    existing_builtin: HostBuiltinProviderOverride | None = None,
    existing_custom: HostCustomProviderProfile | None = None,
) -> HostBuiltinProviderOverride | HostCustomProviderProfile:
    preset = get_provider_preset(args.preset) if getattr(args, "preset", None) else None
    if preset and preset.preset_id != provider_id:
        raise SystemExit("--preset must match the builtin provider id")
    if preset or is_builtin_provider(provider_id):
        if getattr(args, "api_mode", None):
            raise SystemExit("--api-mode is only supported for custom provider profiles")
        if args.api_key_env is not None:
            raise SystemExit("--api-key-env is only supported for custom provider profiles")
        if args.api_key and not args.write_env:
            raise SystemExit("--api-key for builtin providers requires --write-env")
        base_url = args.base_url if args.base_url is not None else (existing_builtin.base_url if existing_builtin else None)
        return HostBuiltinProviderOverride(base_url=base_url)

    base_url = args.base_url or (existing_custom.base_url if existing_custom else None)
    if not base_url:
        raise SystemExit("--base-url is required for custom provider profiles")
    api_mode = getattr(args, "api_mode", None) or (existing_custom.api_mode if existing_custom else "openai-chat")
    api_key_env = args.api_key_env if args.api_key_env is not None else (
        existing_custom.api_key_env if existing_custom else None
    )
    api_key = args.api_key if args.api_key is not None else (existing_custom.api_key if existing_custom else None)
    return HostCustomProviderProfile(api_mode=api_mode, base_url=base_url, api_key_env=api_key_env, api_key=api_key)


def _handle_provider_command(context: SetupContext, args: argparse.Namespace) -> None:
    host_config = load_host_config(context.host_config_path)[0]
    if args.provider_command in {None, "list"}:
        data = {"providers": provider_profiles_dict(host_config), "default_provider": host_config.providers.default}
        _print_result(data, as_json=getattr(args, "json", False))
        return
    if args.provider_command == "show":
        provider_id = _normalize_setup_provider_id(args.provider_id)
        profile = resolve_host_provider_profile(host_config, provider_id)
        _print_result({"provider": provider_id, "profile": provider_profile_dict(profile)}, as_json=args.json)
        return
    if args.provider_command in {"add", "edit"}:
        provider_id = _normalize_setup_provider_id(args.provider_id)
        existing_builtin = host_config.providers.builtin.get(provider_id)
        existing_custom = host_config.providers.custom.get(provider_id)
        profile = provider_profile_from_args(
            args,
            provider_id=provider_id,
            existing_builtin=existing_builtin,
            existing_custom=existing_custom,
        )
        write_env_name = _write_env_name_for_provider(provider_id, args)
        if args.api_key and write_env_name and args.write_env:
            if hasattr(profile, "api_key"):
                profile.api_key = None
            upsert_env_value(runtime_env_path(context.home), write_env_name, args.api_key)
            load_runtime_env(context.home)
        data = write_provider_profile(context, provider_id=provider_id, profile=profile, set_default=args.set_default)
        _print_result(data, as_json=args.json)
        return
    if args.provider_command == "remove":
        _print_result(remove_provider_profile(context, args.provider_id), as_json=args.json)
        return
    if args.provider_command == "set-default":
        _print_result(set_default_provider(context, args.provider_id), as_json=args.json)
        return
    if args.provider_command == "test":
        _print_result(_test_provider(context, args.provider_id, model=args.model), as_json=args.json)
        return
    raise SystemExit(f"unknown provider command: {args.provider_command}")


def _handle_model_command(context: SetupContext, args: argparse.Namespace) -> None:
    if args.model_command != "set":
        raise SystemExit(f"unknown model command: {args.model_command}")
    result = set_core_model(context, core_id=args.core, provider_id=args.provider, model_name=args.model)
    _print_result(result, as_json=args.json)


def _handle_timezone_command(context: SetupContext, args: argparse.Namespace) -> None:
    if args.timezone_command == "set":
        _print_result(set_runtime_timezone(context, args.timezone), as_json=args.json)
        return
    if args.timezone_command == "clear":
        _print_result(clear_runtime_timezone(context), as_json=args.json)
        return
    raise SystemExit(f"unknown timezone command: {args.timezone_command}")


def _test_provider(context: SetupContext, provider_id: str, *, model: str | None = None) -> dict[str, object]:
    provider_id = _normalize_setup_provider_id(provider_id)
    host_config = load_host_config(context.host_config_path)[0]
    profile = resolve_host_provider_profile(host_config, provider_id)
    preset = get_provider_preset(provider_id)
    test_model = model or _suggested_model_for_provider(provider_id, preset=preset) or "provider-test"
    provider_config = resolve_provider_config(
        host_config,
        ModelInfo(provider=provider_id, model_name=test_model),
    )
    provider, resolved_id = create_provider(provider_config=provider_config)
    request = LLMRequest(
        model=test_model,
        messages=[LLMMessage(role="user", content="Reply with ok.")],
    )
    response = asyncio.run(provider.complete(request))
    return {
        "provider": resolved_id,
        "api_mode": provider_config.api_mode,
        "base_url": profile.base_url,
        "model": test_model,
        "api_key": provider_config.api_key_source or "not configured",
        "ok": True,
        "response": response.content[:200],
    }


def _normalize_setup_provider_id(value: str | None) -> str:
    try:
        return normalize_provider_profile_id(value)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


def _has_builtin_override(profile: HostBuiltinProviderOverride) -> bool:
    return bool(profile.base_url)


def _write_env_name_for_provider(provider_id: str, args: argparse.Namespace) -> str | None:
    if not args.write_env:
        return None
    if is_builtin_provider(provider_id):
        profile = get_builtin_provider_profile(provider_id)
        return profile.env_vars[0] if profile and profile.env_vars else None
    return args.api_key_env


def _suggested_model_for_provider(provider_id: str, *, preset: ProviderPreset | None) -> str | None:
    if provider_id == "fake":
        return "fake/demo"
    if preset is not None:
        return preset.suggested_model
    return None


def _print_result(data: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
        return
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            print(f"{key}: {value}")
