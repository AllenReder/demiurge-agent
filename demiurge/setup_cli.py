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
    HostConfig,
    HostProvidersConfig,
    HostProviderProfile,
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
from demiurge.package_wizard import PromptToolkitPackagePrompt, SelectChoice
from demiurge.provider_presets import BUILTIN_PROVIDER_PRESETS, ProviderPreset, get_provider_preset
from demiurge.providers import LLMMessage, LLMRequest
from demiurge.runtime_timezone import resolve_runtime_timezone, validate_timezone_name
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
    context = load_setup_context(args.home, agents_root=args.agents_root)
    if args.setup_command is None:
        run_setup_wizard(context)
        return
    if args.setup_command == "status":
        data = setup_status(context, core_id=args.core, timezone_override=getattr(args, "timezone", None))
        _print_result(data, as_json=args.json)
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
    resolved_home.mkdir(parents=True, exist_ok=True)
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
    return SetupContext(
        home=resolved_home,
        host_config_path=host_config_path,
        host_config=host_config,
        version_store=version_store,
        agents_root=source_agents,
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
            SelectChoice(preset.preset_id, preset.label, preset.base_url) for preset in BUILTIN_PROVIDER_PRESETS
        ]
        choices.append(SelectChoice("custom", "Custom", "Any OpenAI-compatible endpoint"))
        preset_id = self.prompt.select("Provider preset", choices)
        preset = get_provider_preset(preset_id)
        default_id = preset.preset_id if preset else "custom"
        provider_id = _normalize_setup_provider_id(self.prompt.input("Profile id", default=default_id))
        default_base_url = preset.base_url if preset else "http://localhost:11434/v1"
        default_env = preset.api_key_env if preset else f"{provider_id.upper().replace('-', '_')}_API_KEY"
        base_url = self.prompt.input("Base URL", default=default_base_url).strip()
        api_key_env = self.prompt.input("API key environment variable", default=default_env).strip()
        api_key = self.prompt.input("API key", secret=True).strip()
        write_provider_profile(
            self.context,
            provider_id=provider_id,
            profile=HostProviderProfile(base_url=base_url, api_key_env=api_key_env or None),
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
    for provider_id, profile in sorted(host_config.providers.profiles.items()):
        result[provider_id] = provider_profile_dict(profile)
    return result


def provider_profile_dict(profile: HostProviderProfile) -> dict[str, object]:
    _, resolved_source = resolve_profile_api_key(profile, provider_id="profile")
    api_key_source = resolved_source
    if not api_key_source and profile.api_key_env:
        api_key_source = f"env:{profile.api_key_env} (missing)"
    return {
        "adapter": profile.adapter,
        "base_url": profile.base_url,
        "api_key_env": profile.api_key_env,
        "api_key": "<redacted>" if profile.api_key else None,
        "api_key_source": api_key_source or "not configured",
    }


def provider_profile_ids(host_config: HostConfig) -> list[str]:
    ids = sorted(host_config.providers.profiles)
    return ids or [preset.preset_id for preset in BUILTIN_PROVIDER_PRESETS]


def write_provider_profile(
    context: SetupContext,
    *,
    provider_id: str,
    profile: HostProviderProfile,
    set_default: bool = False,
) -> dict[str, object]:
    provider_id = _normalize_setup_provider_id(provider_id)
    host_config = load_host_config(context.host_config_path)[0]
    profiles = dict(host_config.providers.profiles)
    profiles[provider_id] = profile
    host_config.providers = HostProvidersConfig(default=host_config.providers.default, profiles=profiles)
    if set_default:
        host_config.providers.default = provider_id
    write_host_config(context.host_config_path, host_config)
    context.host_config = host_config
    return {"provider": provider_id, "profile": provider_profile_dict(profile), "default": host_config.providers.default}


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
    if provider_id not in host_config.providers.profiles:
        raise SystemExit(f"provider profile not found: {provider_id}")
    profiles = dict(host_config.providers.profiles)
    profiles.pop(provider_id)
    host_config.providers.profiles = profiles
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
    return {"core": core_id, "path": str(manifest_path), "provider": provider_id, "model": model_name}


def provider_profile_from_args(args: argparse.Namespace, *, existing: HostProviderProfile | None = None) -> HostProviderProfile:
    preset = get_provider_preset(args.preset) if getattr(args, "preset", None) else None
    base_url = args.base_url or (preset.base_url if preset else None) or (existing.base_url if existing else None)
    if not base_url:
        raise SystemExit("--base-url is required for custom provider profiles")
    api_key_env = args.api_key_env if args.api_key_env is not None else (preset.api_key_env if preset else None)
    if api_key_env is None and existing is not None:
        api_key_env = existing.api_key_env
    api_key = args.api_key if args.api_key is not None else (existing.api_key if existing else None)
    return HostProviderProfile(
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=api_key,
    )


def _handle_provider_command(context: SetupContext, args: argparse.Namespace) -> None:
    host_config = load_host_config(context.host_config_path)[0]
    if args.provider_command in {None, "list"}:
        data = {"providers": provider_profiles_dict(host_config), "default_provider": host_config.providers.default}
        _print_result(data, as_json=getattr(args, "json", False))
        return
    if args.provider_command == "show":
        provider_id = _normalize_setup_provider_id(args.provider_id)
        profile, _ = resolve_host_provider_profile(host_config, provider_id)
        _print_result({"provider": provider_id, "profile": provider_profile_dict(profile)}, as_json=args.json)
        return
    if args.provider_command in {"add", "edit"}:
        provider_id = _normalize_setup_provider_id(args.provider_id)
        existing_profile = None
        if args.provider_command == "edit":
            try:
                existing_profile = resolve_host_provider_profile(host_config, provider_id)[0]
            except ValueError:
                existing_profile = None
        profile = provider_profile_from_args(args, existing=existing_profile)
        if args.api_key and args.api_key_env and args.write_env:
            profile.api_key = None
            upsert_env_value(runtime_env_path(context.home), args.api_key_env, args.api_key)
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
    profile, _ = resolve_host_provider_profile(host_config, provider_id)
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
