from __future__ import annotations

import copy
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from demiurge.core import McpServerManifestInfo, ScheduleManifestInfo
from demiurge.storage import VersionStore
from demiurge.util import ensure_dir, require_relative_path, utc_id


class PackageRepositoryError(ValueError):
    pass


class PackageOperationError(RuntimeError):
    pass


REDACTED_SECRET = "<redacted>"
OPTION_TYPES = {"string", "bool", "choice", "path", "secret"}
MANIFEST_FILE_KINDS = {"mcp", "schedule"}
COMPONENT_KINDS = {"bootstrap", "input", "output", "tool", "skill", "lib", "core", *MANIFEST_FILE_KINDS}
CORE_LOCAL_KINDS = {"bootstrap", "input", "output", "tool", "skill", "lib"}
CORE_TARGET_KINDS = CORE_LOCAL_KINDS | MANIFEST_FILE_KINDS
PIPELINE_KINDS = {"bootstrap", "input", "output"}
DEFAULT_TARGET_ROOTS = {
    "bootstrap": "agent/bootstrap",
    "input": "agent/input",
    "output": "agent/output",
    "tool": "agent/tools",
    "skill": "agent/skills",
    "lib": "agent/lib",
}
MANIFEST_SLOT_NAMES = {
    "mcp": "mcp",
    "schedule": "schedules",
}
YAML_SUFFIXES = {".yaml", ".yml"}
_COPYTREE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
_OPTION_REF = re.compile(r"^\$\{options\.([A-Za-z0-9_-]+)\}$")
_OPTION_REF_ANYWHERE = re.compile(r"\$\{options\.([A-Za-z0-9_-]+)\}")
_REPOSITORY_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    repository_id: str
    name: str
    summary: str
    root: Path


@dataclass(frozen=True, slots=True)
class PackageRepositorySource:
    alias: str
    source_type: str
    root: Path
    url: str | None = None
    ref: str | None = None
    subdir: str | None = None
    trusted: bool = False
    commit: str | None = None


@dataclass(frozen=True, slots=True)
class PackageRepositoryStatus:
    alias: str
    source_type: str
    trusted: bool
    configured: dict[str, Any]
    root: Path | None = None
    repository_id: str | None = None
    name: str | None = None
    summary: str | None = None
    package_count: int = 0
    ref: str | None = None
    commit: str | None = None
    ready: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PackageOption:
    option_id: str
    option_type: str
    prompt: str
    description: str = ""
    default: Any = None
    has_default: bool = False
    required: bool = False
    choices: list[str] = field(default_factory=list)
    choice_descriptions: dict[str, str] = field(default_factory=dict)
    secret: bool = False


@dataclass(frozen=True, slots=True)
class ConditionalConfig:
    when: dict[str, Any]
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PackageComponent:
    component_id: str
    kind: str
    source: str
    target: str | None = None
    target_core_id: str | None = None
    pipeline: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] | None = None
    when: dict[str, Any] = field(default_factory=dict)
    config_when: list[ConditionalConfig] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PackageInfo:
    package_id: str
    name: str
    summary: str
    tags: list[str]
    components: list[PackageComponent]
    options: list[PackageOption] = field(default_factory=list)
    manual_dependencies: list[str] = field(default_factory=list)
    repository_alias: str = ""
    repository_id: str = ""
    repository_type: str = ""
    repository_ref: str | None = None
    repository_commit: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.repository_alias}/{self.package_id}" if self.repository_alias else self.package_id


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    package_id: str
    repository_alias: str
    repository_id: str
    repository_type: str
    repository_ref: str | None
    repository_commit: str | None
    tags: list[str]
    components: list[dict[str, Any]]
    installed_at: str
    warnings: list[str]
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageListResult:
    repositories: list[PackageRepositoryStatus]
    packages: list[PackageInfo]
    installed: list[InstalledPackage]
    tags: list[str]


@dataclass(frozen=True, slots=True)
class PackageOperationPreview:
    action: str
    core_id: str
    package_id: str
    package_ref: str
    repository_alias: str | None
    repository_id: str | None
    repository_type: str | None
    repository_ref: str | None
    repository_commit: str | None
    components: list[dict[str, Any]]
    warnings: list[str]
    registry_path: Path
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageOperationResult:
    action: str
    core_id: str
    package_id: str
    package_ref: str
    repository_alias: str | None
    repository_id: str | None
    repository_type: str | None
    repository_ref: str | None
    repository_commit: str | None
    components: list[dict[str, Any]]
    warnings: list[str]
    registry_path: Path
    options: dict[str, Any] = field(default_factory=dict)


def default_package_repository_root(override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    checkout_repository = Path(__file__).resolve().parents[1] / "package-repository"
    if checkout_repository.exists():
        return checkout_repository
    return Path(str(files("demiurge.resources").joinpath("package-repository")))


class PackageRepository:
    def __init__(
        self,
        *,
        root: Path,
        repository: RepositoryInfo,
        packages: dict[str, PackageInfo],
        source: PackageRepositorySource,
    ) -> None:
        self.root = root
        self.repository = repository
        self.packages = packages
        self.source = source

    @classmethod
    def load(cls, root: Path, *, source: PackageRepositorySource | None = None) -> "PackageRepository":
        root = root.expanduser().resolve()
        source = source or PackageRepositorySource(alias="", source_type="path", root=root, trusted=True)
        repository_path = root / "repository.yaml"
        if not repository_path.exists():
            raise PackageRepositoryError(f"repository.yaml not found: {repository_path}")
        raw_repository = _read_yaml_mapping(repository_path)
        repository_id = _required_str(raw_repository, "id", path=repository_path)
        repository = RepositoryInfo(
            repository_id=repository_id,
            name=str(raw_repository.get("name") or repository_id),
            summary=str(raw_repository.get("summary") or ""),
            root=root,
        )
        packages = cls._load_packages(root, repository=repository, source=source)
        for package in packages.values():
            for component in package.components:
                cls._validate_component_tree(root, component, cls._component_source_path(root, component))
        return cls(root=root, repository=repository, packages=packages, source=source)

    @staticmethod
    def _load_packages(root: Path, *, repository: RepositoryInfo, source: PackageRepositorySource) -> dict[str, PackageInfo]:
        packages_root = root / "packages"
        result: dict[str, PackageInfo] = {}
        if not packages_root.exists():
            return result
        for path in sorted(packages_root.glob("*.yaml"), key=lambda item: item.name):
            raw = _read_yaml_mapping(path)
            package_id = _required_str(raw, "id", path=path)
            if package_id in result:
                raise PackageRepositoryError(f"duplicate package id: {package_id}")
            tags = raw.get("tags") or []
            if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
                raise PackageRepositoryError(f"package tags must be a list of strings: {path}")
            manual_dependencies = raw.get("manual_dependencies") or []
            if not isinstance(manual_dependencies, list) or any(not isinstance(item, str) for item in manual_dependencies):
                raise PackageRepositoryError(f"package manual_dependencies must be a list of strings: {path}")
            raw_components = raw.get("components") or []
            if not isinstance(raw_components, list) or any(not isinstance(item, Mapping) for item in raw_components):
                raise PackageRepositoryError(f"package components must be a list of objects: {path}")
            components = [PackageRepository._parse_component(item, package_id=package_id, path=path) for item in raw_components]
            PackageRepository._reject_duplicate_component_ids(package_id, components)
            options = PackageRepository._parse_options(raw.get("options"), path=path, package_id=package_id)
            PackageRepository._validate_component_conditions(package_id, components=components, options=options)
            result[package_id] = PackageInfo(
                package_id=package_id,
                name=str(raw.get("name") or package_id),
                summary=str(raw.get("summary") or ""),
                tags=list(tags),
                components=components,
                options=options,
                manual_dependencies=list(manual_dependencies),
                repository_alias=source.alias,
                repository_id=repository.repository_id,
                repository_type=source.source_type,
                repository_ref=source.ref,
                repository_commit=source.commit,
            )
        return result

    @staticmethod
    def _parse_component(raw: Mapping[str, Any], *, package_id: str, path: Path) -> PackageComponent:
        component_id = str(raw.get("id") or raw.get("source") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        source = str(raw.get("source") or "").strip()
        if kind not in COMPONENT_KINDS:
            raise PackageRepositoryError(f"invalid component kind in package {package_id}: {kind}")
        if not component_id or not source:
            raise PackageRepositoryError(f"package {package_id} has a component without id/source")
        pipeline = raw.get("pipeline") or {}
        if not isinstance(pipeline, Mapping):
            raise PackageRepositoryError(f"component {component_id} pipeline must be a mapping: {path}")
        config = raw.get("config")
        if config is not None and not isinstance(config, Mapping):
            raise PackageRepositoryError(f"component {component_id} config must be a mapping: {path}")
        config_when = PackageRepository._parse_config_when(raw.get("config_when"), package_id=package_id, path=path)
        return PackageComponent(
            component_id=component_id,
            kind=kind,
            source=source,
            target=str(raw["target"]) if raw.get("target") is not None else None,
            target_core_id=str(raw["target_core_id"]) if raw.get("target_core_id") is not None else None,
            pipeline=dict(pipeline),
            config=dict(config) if isinstance(config, Mapping) else None,
            when=PackageRepository._parse_condition(raw.get("when"), package_id=package_id, path=path),
            config_when=config_when,
        )

    @staticmethod
    def _parse_config_when(raw: Any, *, package_id: str, path: Path) -> list[ConditionalConfig]:
        if raw is None:
            return []
        if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
            raise PackageRepositoryError(f"package {package_id} config_when must be a list of objects: {path}")
        result: list[ConditionalConfig] = []
        for item in raw:
            config = item.get("config")
            if not isinstance(config, Mapping):
                raise PackageRepositoryError(f"package {package_id} config_when entries require config mapping: {path}")
            result.append(
                ConditionalConfig(
                    when=PackageRepository._parse_condition(item.get("when"), package_id=package_id, path=path),
                    config=dict(config),
                )
            )
        return result

    @staticmethod
    def _parse_condition(raw: Any, *, package_id: str, path: Path) -> dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, Mapping) or any(not isinstance(key, str) or not key.strip() for key in raw):
            raise PackageRepositoryError(f"package {package_id} when must be a mapping of option ids: {path}")
        return {str(key): copy.deepcopy(value) for key, value in raw.items()}

    @staticmethod
    def _parse_options(raw_options: Any, *, path: Path, package_id: str) -> list[PackageOption]:
        if raw_options is None:
            return []
        if not isinstance(raw_options, list) or any(not isinstance(item, Mapping) for item in raw_options):
            raise PackageRepositoryError(f"package options must be a list of objects: {path}")
        result: list[PackageOption] = []
        seen: set[str] = set()
        for item in raw_options:
            option_id = str(item.get("id") or "").strip()
            if not option_id:
                raise PackageRepositoryError(f"package {package_id} has an option without id")
            if option_id in seen:
                raise PackageRepositoryError(f"package {package_id} has duplicate option id: {option_id}")
            seen.add(option_id)
            option_type = str(item.get("type") or "string").strip()
            if option_type not in OPTION_TYPES:
                raise PackageRepositoryError(f"package {package_id} option {option_id} has invalid type: {option_type}")
            choices, choice_descriptions = PackageRepository._parse_option_choices(
                item.get("choices") or [],
                package_id=package_id,
                option_id=option_id,
                path=path,
            )
            if option_type == "choice" and not choices:
                raise PackageRepositoryError(f"package {package_id} option {option_id} choice options require choices")
            has_default = "default" in item
            default = copy.deepcopy(item.get("default"))
            if option_type == "choice" and has_default and default not in {None, ""} and default not in choices:
                raise PackageRepositoryError(f"package {package_id} option {option_id} default is not in choices: {path}")
            result.append(
                PackageOption(
                    option_id=option_id,
                    option_type=option_type,
                    prompt=str(item.get("prompt") or option_id),
                    description=_optional_str(item.get("description"), field_name=f"option {option_id} description", path=path),
                    default=default,
                    has_default=has_default,
                    required=bool(item.get("required", False)),
                    choices=list(choices),
                    choice_descriptions=choice_descriptions,
                    secret=bool(item.get("secret", option_type == "secret")) or option_type == "secret",
                )
            )
        return result

    @staticmethod
    def _parse_option_choices(raw_choices: Any, *, package_id: str, option_id: str, path: Path) -> tuple[list[str], dict[str, str]]:
        if not isinstance(raw_choices, list):
            raise PackageRepositoryError(f"package {package_id} option {option_id} choices must be a list: {path}")
        choices: list[str] = []
        descriptions: dict[str, str] = {}
        seen: set[str] = set()
        for raw_choice in raw_choices:
            if isinstance(raw_choice, str):
                value = raw_choice
                description = ""
            elif isinstance(raw_choice, Mapping):
                value = str(raw_choice.get("value") or "").strip()
                if not value:
                    raise PackageRepositoryError(f"package {package_id} option {option_id} choice requires value: {path}")
                description = _optional_str(
                    raw_choice.get("description"),
                    field_name=f"option {option_id} choice {value} description",
                    path=path,
                )
            else:
                raise PackageRepositoryError(f"package {package_id} option {option_id} choices must be strings or objects: {path}")
            if value in seen:
                raise PackageRepositoryError(f"package {package_id} option {option_id} has duplicate choice: {value}")
            seen.add(value)
            choices.append(value)
            if description:
                descriptions[value] = description
        return choices, descriptions

    @staticmethod
    def _reject_duplicate_component_ids(package_id: str, components: list[PackageComponent]) -> None:
        seen: set[str] = set()
        for component in components:
            if component.component_id in seen:
                raise PackageRepositoryError(f"package {package_id} has duplicate component id: {component.component_id}")
            seen.add(component.component_id)

    @staticmethod
    def _validate_component_conditions(
        package_id: str,
        *,
        components: list[PackageComponent],
        options: list[PackageOption],
    ) -> None:
        option_ids = {option.option_id for option in options}
        for component in components:
            for option_id in component.when:
                if option_id not in option_ids:
                    raise PackageRepositoryError(f"package {package_id} component {component.component_id} references unknown option: {option_id}")
            for conditional in component.config_when:
                for option_id in conditional.when:
                    if option_id not in option_ids:
                        raise PackageRepositoryError(f"package {package_id} component {component.component_id} config_when references unknown option: {option_id}")

    @staticmethod
    def _component_source_path(root: Path, component: PackageComponent) -> Path:
        source = Path(component.source)
        if source.is_absolute() or ".." in source.parts:
            raise PackageRepositoryError(f"component source must stay inside the repository: {component.source}")
        path = root / component.kind / source
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                raise PackageRepositoryError(f"component source cannot be a symlink: {current}")
        if not path.exists():
            raise PackageRepositoryError(f"component source not found: {path}")
        return require_relative_path(path, root)

    def component_source_path(self, component: PackageComponent) -> Path:
        return self._component_source_path(self.root, component)

    @staticmethod
    def _validate_component_tree(root: Path, component: PackageComponent, source_path: Path) -> None:
        if component.kind in MANIFEST_FILE_KINDS:
            if not source_path.is_file():
                raise PackageRepositoryError(f"{component.kind} component source must be a YAML file: {source_path}")
            if source_path.suffix.lower() not in YAML_SUFFIXES:
                raise PackageRepositoryError(f"{component.kind} component source must be a YAML file: {source_path}")
            require_relative_path(source_path, root)
            return
        if source_path.is_symlink():
            raise PackageRepositoryError(f"component source cannot be a symlink: {source_path}")
        if source_path.is_file():
            require_relative_path(source_path, root)
            return
        if not source_path.is_dir():
            raise PackageRepositoryError(f"component source must be a directory or file: {source_path}")
        for path in source_path.rglob("*"):
            if path.is_symlink():
                raise PackageRepositoryError(f"component source cannot contain symlinks: {path}")
            require_relative_path(path, root)


class PackageRepositoryCollection:
    def __init__(self, *, repositories: dict[str, PackageRepository], statuses: list[PackageRepositoryStatus]) -> None:
        self.repositories = repositories
        self.statuses = statuses

    @property
    def packages(self) -> dict[str, PackageInfo]:
        result: dict[str, PackageInfo] = {}
        for package in self.all_packages():
            result[package.ref] = package
        return result

    def all_packages(self) -> list[PackageInfo]:
        packages: list[PackageInfo] = []
        for alias in sorted(self.repositories):
            packages.extend(self.repositories[alias].packages.values())
        return sorted(packages, key=lambda package: (package.package_id, package.repository_alias))

    def resolve_package_ref(self, package_ref: str) -> PackageInfo:
        package_ref = package_ref.strip()
        if not package_ref:
            raise PackageOperationError("package id is required")
        if "/" in package_ref:
            alias, _, package_id = package_ref.partition("/")
            repository = self.repositories.get(alias)
            if repository is None:
                raise PackageOperationError(f"unknown package repository: {alias}")
            package = repository.packages.get(package_id)
            if package is None:
                raise PackageOperationError(f"unknown package: {package_ref}")
            return package
        matches = [package for package in self.all_packages() if package.package_id == package_ref]
        if not matches:
            raise PackageOperationError(f"unknown package: {package_ref}")
        if len(matches) > 1:
            refs = ", ".join(package.ref for package in matches)
            raise PackageOperationError(f"ambiguous package '{package_ref}'; use one of: {refs}")
        return matches[0]


def default_package_repository_configs() -> dict[str, dict[str, str]]:
    return {"builtin": {"type": "builtin"}}


def package_repository_cache_root(home: Path) -> Path:
    return home.expanduser().resolve() / "package-repositories"


def inspect_package_repository_candidate(*, home: Path, config: Mapping[str, Any]) -> PackageRepositoryStatus:
    candidate_config = dict(config)
    source_type = str(candidate_config.get("type") or "").strip()
    probe_alias = "probe-" + hashlib.sha256(repr(sorted(candidate_config.items())).encode("utf-8")).hexdigest()[:12]
    trusted_config = {**candidate_config, "trusted": True}
    try:
        return sync_package_repository(home=home, alias=probe_alias, config=trusted_config)
    finally:
        if source_type == "git":
            shutil.rmtree(package_repository_cache_root(home) / probe_alias, ignore_errors=True)


def load_package_repository_collection(
    *,
    home: Path,
    repository_configs: Mapping[str, Any] | None,
    repository_alias: str | None = None,
) -> PackageRepositoryCollection:
    configs = _normalize_repository_configs(repository_configs)
    if repository_alias and repository_alias not in configs:
        raise PackageRepositoryError(f"unknown package repository: {repository_alias}")
    repositories: dict[str, PackageRepository] = {}
    statuses: list[PackageRepositoryStatus] = []
    for alias, config in configs.items():
        if repository_alias and alias != repository_alias:
            continue
        status = package_repository_status(home=home, alias=alias, config=config)
        statuses.append(status)
        if not status.ready:
            raise PackageRepositoryError(status.error or f"package repository is not ready: {alias}")
        source = _source_from_repository_config(home=home, alias=alias, config=config, strict=True)
        repositories[alias] = PackageRepository.load(source.root, source=source)
    return PackageRepositoryCollection(repositories=repositories, statuses=statuses)


def list_package_repository_statuses(*, home: Path, repository_configs: Mapping[str, Any] | None) -> list[PackageRepositoryStatus]:
    return [
        package_repository_status(home=home, alias=alias, config=config)
        for alias, config in _normalize_repository_configs(repository_configs).items()
    ]


def package_repository_status(*, home: Path, alias: str, config: Mapping[str, Any]) -> PackageRepositoryStatus:
    try:
        source = _source_from_repository_config(home=home, alias=alias, config=config, strict=False)
        if source.source_type != "builtin" and not source.trusted:
            return PackageRepositoryStatus(
                alias=alias,
                source_type=source.source_type,
                trusted=False,
                configured=dict(config),
                root=source.root,
                ref=source.ref,
                commit=source.commit,
                error="external package repository is not trusted",
            )
        repository = PackageRepository.load(source.root, source=source)
        return PackageRepositoryStatus(
            alias=alias,
            source_type=source.source_type,
            trusted=source.trusted,
            configured=dict(config),
            root=source.root,
            repository_id=repository.repository.repository_id,
            name=repository.repository.name,
            summary=repository.repository.summary,
            package_count=len(repository.packages),
            ref=source.ref,
            commit=source.commit,
            ready=True,
        )
    except (OSError, PackageRepositoryError, subprocess.CalledProcessError) as exc:
        source_type = str(config.get("type") or "path")
        return PackageRepositoryStatus(
            alias=alias,
            source_type=source_type,
            trusted=bool(config.get("trusted")) or source_type == "builtin",
            configured=dict(config),
            ref=_optional_config_str(config.get("ref")),
            error=str(exc),
        )


def sync_package_repository(*, home: Path, alias: str, config: Mapping[str, Any]) -> PackageRepositoryStatus:
    source_type = str(config.get("type") or "").strip()
    if source_type == "git":
        if not bool(config.get("trusted")):
            raise PackageRepositoryError(f"external package repository is not trusted: {alias}")
        url = _required_config_str(config.get("url"), f"packages.repositories.{alias}.url")
        cache_path = package_repository_cache_root(home) / alias
        ensure_dir(cache_path.parent)
        if cache_path.exists():
            if not (cache_path / ".git").exists():
                raise PackageRepositoryError(f"git package repository cache is not a git checkout: {cache_path}")
            _run_git(["fetch", "--all", "--tags", "--prune"], cwd=cache_path)
        else:
            _run_git(["clone", url, str(cache_path)], cwd=None)
        ref = _optional_config_str(config.get("ref"))
        if ref:
            fetch_result = _run_git(["fetch", "origin", ref], cwd=cache_path, check=False)
            if fetch_result.returncode == 0:
                _run_git(["checkout", "--detach", "FETCH_HEAD"], cwd=cache_path)
            else:
                _run_git(["checkout", "--detach", ref], cwd=cache_path)
        else:
            _run_git(["pull", "--ff-only"], cwd=cache_path)
    elif source_type in {"builtin", "path"}:
        pass
    else:
        raise PackageRepositoryError(f"invalid package repository type for {alias}: {source_type}")
    status = package_repository_status(home=home, alias=alias, config=config)
    if not status.ready:
        raise PackageRepositoryError(status.error or f"package repository is not ready: {alias}")
    return status


def installed_repository_dependents(version_store: VersionStore, repository_alias: str) -> list[str]:
    dependents: list[str] = []
    for core_id in version_store.list_core_ids():
        path = version_store.active_core_path(core_id) / "packages.yaml"
        if not path.exists():
            continue
        raw = _read_yaml_mapping(path)
        installed = raw.get("installed") or []
        if not isinstance(installed, list):
            continue
        for item in installed:
            if isinstance(item, Mapping) and str(item.get("repository_alias") or "") == repository_alias:
                dependents.append(f"{core_id}:{item.get('package_id')}")
    return sorted(dependents)


def _normalize_repository_configs(repository_configs: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    raw_configs = repository_configs or default_package_repository_configs()
    for alias, raw in raw_configs.items():
        _validate_repository_alias(str(alias))
        if hasattr(raw, "model_dump"):
            value = raw.model_dump(mode="python", exclude_none=True)
        elif isinstance(raw, Mapping):
            value = dict(raw)
        else:
            raise PackageRepositoryError(f"invalid package repository config for {alias}")
        configs[str(alias)] = value
    if "builtin" not in configs:
        configs = {"builtin": {"type": "builtin"}, **configs}
    return dict(sorted(configs.items()))


def _source_from_repository_config(
    *,
    home: Path,
    alias: str,
    config: Mapping[str, Any],
    strict: bool,
) -> PackageRepositorySource:
    _validate_repository_alias(alias)
    source_type = str(config.get("type") or "").strip()
    if source_type not in {"builtin", "path", "git"}:
        raise PackageRepositoryError(f"invalid package repository type for {alias}: {source_type}")
    subdir = _optional_config_str(config.get("subdir"))
    trusted = source_type == "builtin" or bool(config.get("trusted"))
    ref = _optional_config_str(config.get("ref"))
    if source_type == "builtin":
        root = default_package_repository_root()
        if subdir:
            raise PackageRepositoryError("builtin package repository cannot set subdir")
        if ref:
            raise PackageRepositoryError("builtin package repository cannot set ref")
        return PackageRepositorySource(alias=alias, source_type=source_type, root=root, trusted=True)
    if strict and not trusted:
        raise PackageRepositoryError(f"external package repository is not trusted: {alias}")
    if source_type == "path":
        raw_path = _required_config_str(config.get("path"), f"packages.repositories.{alias}.path")
        root = Path(raw_path).expanduser()
        if not root.is_absolute():
            root = (home / root).resolve()
        else:
            root = root.resolve()
        if subdir:
            root = _append_safe_subdir(root, subdir)
        return PackageRepositorySource(alias=alias, source_type=source_type, root=root, subdir=subdir, trusted=trusted)
    url = _required_config_str(config.get("url"), f"packages.repositories.{alias}.url")
    cache_path = package_repository_cache_root(home) / alias
    root = cache_path
    if subdir:
        root = _append_safe_subdir(root, subdir)
    commit = _git_commit(cache_path) if (cache_path / ".git").exists() else None
    return PackageRepositorySource(
        alias=alias,
        source_type=source_type,
        root=root,
        url=url,
        ref=ref,
        subdir=subdir,
        trusted=trusted,
        commit=commit,
    )


def _append_safe_subdir(root: Path, subdir: str) -> Path:
    path = Path(subdir)
    if path.is_absolute() or ".." in path.parts:
        raise PackageRepositoryError(f"package repository subdir must stay inside the source: {subdir}")
    return require_relative_path(root / path, root)


def _run_git(command: list[str], *, cwd: Path | None, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *command],
            cwd=cwd,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise PackageRepositoryError("required command not found: git") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        raise PackageRepositoryError(f"git {' '.join(command)} failed: {message}") from exc


def _git_commit(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    result = _run_git(["rev-parse", "HEAD"], cwd=path)
    return result.stdout.strip() or None


def _validate_repository_alias(alias: str) -> None:
    if not _REPOSITORY_ALIAS.fullmatch(alias) or alias in {".", ".."}:
        raise PackageRepositoryError(f"invalid package repository alias: {alias}")


def _required_config_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackageRepositoryError(f"{field_name} is required")
    return value.strip()


def _optional_config_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    return value.strip() or None


class PackageManager:
    def __init__(self, *, version_store: VersionStore, repository: PackageRepository | PackageRepositoryCollection) -> None:
        self.version_store = version_store
        if isinstance(repository, PackageRepositoryCollection):
            self.repositories = repository
        else:
            self.repositories = PackageRepositoryCollection(
                repositories={repository.source.alias or repository.repository.repository_id: repository},
                statuses=[
                    PackageRepositoryStatus(
                        alias=repository.source.alias or repository.repository.repository_id,
                        source_type=repository.source.source_type,
                        trusted=repository.source.trusted,
                        configured={"type": repository.source.source_type},
                        root=repository.root,
                        repository_id=repository.repository.repository_id,
                        name=repository.repository.name,
                        summary=repository.repository.summary,
                        package_count=len(repository.packages),
                        ref=repository.source.ref,
                        commit=repository.source.commit,
                        ready=True,
                    )
                ],
            )

    def list(
        self,
        *,
        core_id: str | None = None,
        tag: str | None = None,
        repository_alias: str | None = None,
    ) -> PackageListResult:
        installed: list[InstalledPackage] = []
        if core_id:
            installed = self._load_installed(self.version_store.active_core_path(core_id))
        if repository_alias and repository_alias not in self.repositories.repositories:
            raise PackageOperationError(f"unknown package repository: {repository_alias}")
        packages = self.repositories.all_packages()
        if repository_alias:
            packages = [package for package in packages if package.repository_alias == repository_alias]
        if tag:
            packages = [package for package in packages if tag in package.tags]
        return PackageListResult(
            repositories=self.repositories.statuses,
            packages=packages,
            installed=installed,
            tags=sorted({tag for package in self.repositories.all_packages() for tag in package.tags}),
        )

    def preview_install(
        self,
        *,
        core_id: str,
        package_id: str,
        option_answers: Mapping[str, Any] | None = None,
    ) -> PackageOperationPreview:
        core_path = self._require_active_core(core_id)
        package = self._require_package(package_id)
        resolved_options = self.resolve_options(package_id=package.ref, option_answers=option_answers)
        installed = self._load_installed(core_path)
        if any(item.package_id == package.package_id for item in installed):
            raise PackageOperationError(f"package already installed for {core_id}: {package.package_id}")
        planned = self._build_install_operations(core_path, package, installed, resolved_options)
        warnings = self._package_warnings(package)
        return PackageOperationPreview(
            action="install",
            core_id=core_id,
            package_id=package.package_id,
            package_ref=package.ref,
            repository_alias=package.repository_alias,
            repository_id=package.repository_id,
            repository_type=package.repository_type,
            repository_ref=package.repository_ref,
            repository_commit=package.repository_commit,
            components=[self._operation_preview(operation) for operation in planned],
            warnings=warnings,
            registry_path=self._registry_path(core_path),
            options=self._redact_options(package, resolved_options),
        )

    def install(
        self,
        *,
        core_id: str,
        package_id: str,
        option_answers: Mapping[str, Any] | None = None,
    ) -> PackageOperationResult:
        core_path = self._require_active_core(core_id)
        package = self._require_package(package_id)
        installed = self._load_installed(core_path)
        if any(item.package_id == package.package_id for item in installed):
            raise PackageOperationError(f"package already installed for {core_id}: {package.package_id}")
        resolved_options = self.resolve_options(package_id=package.ref, option_answers=option_answers)
        planned = self._build_install_operations(core_path, package, installed, resolved_options)
        installed_components: list[dict[str, Any]] = []
        warnings = self._package_warnings(package)
        try:
            for operation in planned:
                installed_components.append(self._install_component(core_path, operation))
            record = InstalledPackage(
                package_id=package.package_id,
                repository_alias=package.repository_alias,
                repository_id=package.repository_id,
                repository_type=package.repository_type,
                repository_ref=package.repository_ref,
                repository_commit=package.repository_commit,
                tags=list(package.tags),
                components=installed_components,
                installed_at=utc_id("pkg_"),
                warnings=warnings,
                options=self._redact_options(package, resolved_options),
            )
            self._write_installed(core_path, [*installed, record])
        except Exception:
            for component in reversed(installed_components):
                if not component.get("reused"):
                    self._remove_component(core_path, component, remaining=installed, ignore_missing=True)
            raise
        return PackageOperationResult(
            action="install",
            core_id=core_id,
            package_id=package.package_id,
            package_ref=package.ref,
            repository_alias=package.repository_alias,
            repository_id=package.repository_id,
            repository_type=package.repository_type,
            repository_ref=package.repository_ref,
            repository_commit=package.repository_commit,
            components=installed_components,
            warnings=warnings,
            registry_path=self._registry_path(core_path),
            options=self._redact_options(package, resolved_options),
        )

    def preview_uninstall(self, *, core_id: str, package_id: str) -> PackageOperationPreview:
        core_path = self._require_active_core(core_id)
        installed = self._load_installed(core_path)
        record = self._find_installed_record(installed, package_id)
        if record is None:
            raise PackageOperationError(f"package is not installed for {core_id}: {package_id}")
        remaining = [item for item in installed if item is not record]
        components = [self._uninstall_component_preview(component, remaining=remaining) for component in record.components]
        return PackageOperationPreview(
            action="uninstall",
            core_id=core_id,
            package_id=record.package_id,
            package_ref=self._installed_package_ref(record),
            repository_alias=record.repository_alias,
            repository_id=record.repository_id,
            repository_type=record.repository_type,
            repository_ref=record.repository_ref,
            repository_commit=record.repository_commit,
            components=components,
            warnings=[],
            registry_path=self._registry_path(core_path),
            options=dict(record.options),
        )

    def uninstall(self, *, core_id: str, package_id: str) -> PackageOperationResult:
        core_path = self._require_active_core(core_id)
        installed = self._load_installed(core_path)
        record = self._find_installed_record(installed, package_id)
        if record is None:
            raise PackageOperationError(f"package is not installed for {core_id}: {package_id}")
        remaining = [item for item in installed if item is not record]
        components = [self._uninstall_component_preview(component, remaining=remaining) for component in record.components]
        warnings: list[str] = []
        for component in reversed(record.components):
            warnings.extend(self._remove_component(core_path, component, remaining=remaining, ignore_missing=True))
        self._write_installed(core_path, remaining)
        return PackageOperationResult(
            action="uninstall",
            core_id=core_id,
            package_id=record.package_id,
            package_ref=self._installed_package_ref(record),
            repository_alias=record.repository_alias,
            repository_id=record.repository_id,
            repository_type=record.repository_type,
            repository_ref=record.repository_ref,
            repository_commit=record.repository_commit,
            components=components,
            warnings=warnings,
            registry_path=self._registry_path(core_path),
            options=dict(record.options),
        )

    def resolve_options(
        self,
        *,
        package_id: str,
        option_answers: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        package = self._require_package(package_id)
        answers = dict(option_answers or {})
        known = {option.option_id for option in package.options}
        unknown = sorted(set(answers) - known)
        if unknown:
            raise PackageOperationError(f"unknown option(s) for package {package_id}: {', '.join(unknown)}")
        resolved: dict[str, Any] = {}
        for option in package.options:
            provided = option.option_id in answers
            value = answers[option.option_id] if provided else copy.deepcopy(option.default if option.has_default else None)
            normalized = self._normalize_option_value(package, option, value, provided=provided)
            if option.required and self._is_missing_option_value(normalized):
                if not provided:
                    raise PackageOperationError(
                        f"package {package_id} requires option '{option.option_id}'; "
                        "run `demiurge package` for interactive installation"
                    )
                raise PackageOperationError(f"package {package_id} option '{option.option_id}' is required")
            resolved[option.option_id] = normalized
        return resolved

    def _build_install_operations(
        self,
        core_path: Path,
        package: PackageInfo,
        installed: list[InstalledPackage],
        resolved_options: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        planned = [
            self._plan_component(core_path, package, component, resolved_options)
            for component in package.components
            if self._condition_matches(component.when, resolved_options)
        ]
        planned = [operation for operation in planned if operation is not None]
        self._validate_install_plan(core_path, planned, installed=installed)
        return planned

    def _plan_component(
        self,
        core_path: Path,
        package: PackageInfo,
        component: PackageComponent,
        resolved_options: Mapping[str, Any],
    ) -> dict[str, Any]:
        repository = self._repository_for_package(package)
        source_path = repository.component_source_path(component)
        source_key = f"{component.kind}/{component.source}"
        config = self._render_component_config(component, resolved_options)
        if component.kind in MANIFEST_FILE_KINDS:
            return self._plan_manifest_file_component(core_path, package, component, source_path, source_key, config)
        if component.kind in CORE_LOCAL_KINDS:
            target_rel = component.target or f"{DEFAULT_TARGET_ROOTS[component.kind]}/{Path(component.source).name}"
            target_path = self._relative_target(core_path, target_rel)
            operation = {
                "kind": component.kind,
                "component_id": component.component_id,
                "package_id": package.package_id,
                "repository_alias": package.repository_alias,
                "source": source_key,
                "source_path": str(source_path),
                "target": target_path.relative_to(core_path).as_posix(),
                "target_path": str(target_path),
                "config": config,
                "reused": False,
            }
            if component.kind in PIPELINE_KINDS:
                operation["slot_id"] = target_path.name
                operation["pipeline"] = dict(component.pipeline or {"group": "serial"})
            return operation
        target_core_id = component.target_core_id or component.component_id
        self._validate_core_id(target_core_id)
        target_path = self.version_store.active_core_path(target_core_id)
        return {
            "kind": "core",
            "component_id": component.component_id,
            "package_id": package.package_id,
            "repository_alias": package.repository_alias,
            "source": source_key,
            "source_path": str(source_path),
            "target_core_id": target_core_id,
            "target": str(target_path),
            "target_path": str(target_path),
            "reused": False,
        }

    def _plan_manifest_file_component(
        self,
        core_path: Path,
        package: PackageInfo,
        component: PackageComponent,
        source_path: Path,
        source_key: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        target_root = self._manifest_target_root(core_path, component.kind)
        target_root_rel = target_root.relative_to(core_path).as_posix()
        target_rel = component.target or f"{target_root_rel}/{Path(component.source).name}"
        target_path = self._relative_target(core_path, target_rel)
        self._validate_manifest_target_path(
            core_path,
            kind=component.kind,
            target_root=target_root,
            target_path=target_path,
            target_rel=target_rel,
        )
        manifest = self._render_manifest_file(component.kind, source_path, config)
        return {
            "kind": component.kind,
            "component_id": component.component_id,
            "package_id": package.package_id,
            "repository_alias": package.repository_alias,
            "source": source_key,
            "source_path": str(source_path),
            "target": target_path.relative_to(core_path).as_posix(),
            "target_path": str(target_path),
            "manifest": manifest,
            "manifest_id": target_path.stem,
            "manifest_root": target_root_rel,
            "reused": False,
        }

    def _manifest_target_root(self, core_path: Path, kind: str) -> Path:
        slot_name = MANIFEST_SLOT_NAMES[kind]
        try:
            raw = _read_yaml_mapping(core_path / "agent.yaml")
        except PackageRepositoryError as exc:
            raise PackageOperationError(str(exc)) from exc
        raw_slots = raw.get("slots") or {}
        if raw_slots and not isinstance(raw_slots, Mapping):
            raise PackageOperationError("agent.yaml slots must be a mapping")
        configured = raw_slots.get(slot_name) if isinstance(raw_slots, Mapping) else None
        if configured is None or not str(configured).strip():
            raw_runtime = raw.get("runtime") or {}
            if raw_runtime and not isinstance(raw_runtime, Mapping):
                raise PackageOperationError("agent.yaml runtime must be a mapping")
            surface_root = str(raw_runtime.get("surface_root") or "agent").strip() if isinstance(raw_runtime, Mapping) else "agent"
            configured = f"{surface_root or 'agent'}/{slot_name}"
        if not isinstance(configured, str):
            raise PackageOperationError(f"agent.yaml slots.{slot_name} must be a string")
        return self._relative_target(core_path, configured)

    def _validate_manifest_target_path(
        self,
        core_path: Path,
        *,
        kind: str,
        target_root: Path,
        target_path: Path,
        target_rel: str,
    ) -> None:
        if target_path.suffix.lower() not in YAML_SUFFIXES:
            raise PackageOperationError(f"{kind} target must be a YAML file: {target_rel}")
        try:
            target_path.relative_to(target_root)
        except ValueError as exc:
            root_rel = target_root.relative_to(core_path).as_posix()
            raise PackageOperationError(f"{kind} target must stay inside {root_rel}: {target_rel}") from exc
        if target_path.parent != target_root:
            root_rel = target_root.relative_to(core_path).as_posix()
            raise PackageOperationError(f"{kind} target must be directly inside {root_rel}: {target_rel}")

    def _render_manifest_file(self, kind: str, source_path: Path, config: dict[str, Any] | None) -> dict[str, Any]:
        try:
            raw = _read_yaml_mapping(source_path)
        except PackageRepositoryError as exc:
            raise PackageOperationError(str(exc)) from exc
        if config:
            raw = self._merge_config(raw, config)
        try:
            if kind == "mcp":
                manifest = McpServerManifestInfo.model_validate(raw)
            elif kind == "schedule":
                manifest = ScheduleManifestInfo.model_validate(raw)
            else:
                raise PackageOperationError(f"unsupported manifest component kind: {kind}")
        except ValidationError as exc:
            raise PackageOperationError(f"invalid {kind} manifest: {exc}") from exc
        return manifest.model_dump(mode="python", exclude_none=True)

    def _validate_install_plan(
        self,
        core_path: Path,
        planned: list[dict[str, Any]],
        *,
        installed: list[InstalledPackage],
    ) -> None:
        seen_targets: set[str] = set()
        seen_manifest_ids: set[str] = set()
        for operation in planned:
            target_key = self._target_key(operation)
            if target_key in seen_targets:
                raise PackageOperationError(f"package contains duplicate target: {operation.get('target')}")
            seen_targets.add(target_key)
            target_path = Path(str(operation["target_path"]))
            existing = self._find_existing_component(operation, installed)
            if target_path.exists():
                if existing is None:
                    raise PackageOperationError(f"target already exists: {self._display_target(core_path, operation)}")
                operation["reused"] = True
                operation["reused_by"] = existing.get("package_id")
                continue
            if operation["kind"] in MANIFEST_FILE_KINDS:
                manifest_key = self._manifest_key(operation)
                if manifest_key in seen_manifest_ids:
                    raise PackageOperationError(f"package contains duplicate {operation['kind']} id: {operation.get('manifest_id')}")
                seen_manifest_ids.add(manifest_key)
                conflict = self._manifest_sibling_conflict_path(operation)
                if conflict is not None:
                    raise PackageOperationError(f"{operation['kind']} id already exists: {conflict.name}")
            if operation["kind"] in PIPELINE_KINDS:
                self._validate_pipeline_insert(core_path, operation)

    def _manifest_key(self, operation: Mapping[str, Any]) -> str:
        return f"{operation.get('kind')}:{operation.get('manifest_root')}:{operation.get('manifest_id')}"

    def _manifest_sibling_conflict_path(self, operation: Mapping[str, Any]) -> Path | None:
        root = Path(str(operation["target_path"])).parent
        target_path = Path(str(operation["target_path"]))
        manifest_id = str(operation.get("manifest_id") or target_path.stem)
        for suffix in sorted(YAML_SUFFIXES):
            candidate = root / f"{manifest_id}{suffix}"
            if candidate == target_path:
                continue
            if candidate.exists():
                return candidate
        return None

    def _install_component(self, core_path: Path, operation: dict[str, Any]) -> dict[str, Any]:
        if operation.get("reused"):
            return self._component_record(operation)
        source_path = Path(str(operation["source_path"]))
        target_path = Path(str(operation["target_path"]))
        if operation["kind"] in MANIFEST_FILE_KINDS:
            ensure_dir(target_path.parent)
            target_path.write_text(
                yaml.safe_dump(operation["manifest"], sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            return self._component_record(operation)
        if operation["kind"] in CORE_LOCAL_KINDS:
            self._copy_component_source(source_path, target_path)
            config = operation.get("config")
            if isinstance(config, dict):
                if not target_path.is_dir():
                    raise PackageOperationError(f"component config requires a directory target: {operation['target']}")
                (target_path / "config.yaml").write_text(
                    yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
            if operation["kind"] in PIPELINE_KINDS:
                self._insert_pipeline_slot(core_path, operation)
            return self._component_record(operation)
        shutil.copytree(source_path, target_path, ignore=_COPYTREE_IGNORE)
        self._rewrite_core_id(target_path / "agent.yaml", str(operation["target_core_id"]))
        return self._component_record(operation)

    def _remove_component(
        self,
        core_path: Path,
        component: Mapping[str, Any],
        *,
        remaining: list[InstalledPackage],
        ignore_missing: bool,
    ) -> list[str]:
        warnings: list[str] = []
        if self._is_component_referenced(component, remaining):
            warnings.append(f"kept shared target: {component.get('target') or component.get('target_core_id')}")
            return warnings
        kind = str(component.get("kind") or "")
        if kind in CORE_TARGET_KINDS:
            target = str(component.get("target") or "")
            if target:
                target_path = self._relative_target(core_path, target)
                if target_path.exists():
                    if target_path.is_dir():
                        shutil.rmtree(target_path)
                    else:
                        target_path.unlink()
                elif not ignore_missing:
                    raise PackageOperationError(f"target not found: {target}")
                else:
                    warnings.append(f"target already missing: {target}")
            if kind in PIPELINE_KINDS:
                self._remove_pipeline_slot(core_path, kind, str(component.get("slot_id") or Path(target).name))
            return warnings
        if kind == "core":
            target_core_id = str(component.get("target_core_id") or "")
            if target_core_id:
                self._validate_core_id(target_core_id)
                target_path = self.version_store.active_core_path(target_core_id)
                if target_path.exists():
                    shutil.rmtree(target_path)
                else:
                    warnings.append(f"target core already missing: {target_core_id}")
                pointer = self.version_store.registry_root / f"{target_core_id}.json"
                if pointer.exists():
                    pointer.unlink()
            return warnings
        warnings.append(f"unknown installed component kind: {kind}")
        return warnings

    def _uninstall_component_preview(self, component: Mapping[str, Any], *, remaining: list[InstalledPackage]) -> dict[str, Any]:
        preview = dict(component)
        preview["remove"] = not self._is_component_referenced(component, remaining)
        return preview

    def _copy_component_source(self, source_path: Path, target_path: Path) -> None:
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, ignore=_COPYTREE_IGNORE)
            return
        ensure_dir(target_path.parent)
        shutil.copy2(source_path, target_path)

    def _component_record(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        keep = {
            "kind",
            "component_id",
            "package_id",
            "repository_alias",
            "source",
            "target",
            "target_core_id",
            "slot_id",
            "pipeline",
            "manifest_id",
            "manifest_root",
            "reused",
            "reused_by",
        }
        return {key: value for key, value in operation.items() if key in keep and value is not None}

    def _operation_preview(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        preview = self._component_record(operation)
        if operation.get("kind") in CORE_LOCAL_KINDS and isinstance(operation.get("config"), dict):
            preview["config_path"] = "config.yaml"
        return preview

    def _render_component_config(
        self,
        component: PackageComponent,
        resolved_options: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        config = copy.deepcopy(component.config) if isinstance(component.config, Mapping) else None
        for conditional in component.config_when:
            if not self._condition_matches(conditional.when, resolved_options):
                continue
            if config is None:
                config = {}
            config = self._merge_config(config, conditional.config)
        if config is None:
            return None
        return self._render_config_value(config, resolved_options)

    def _render_config_value(self, value: Any, resolved_options: Mapping[str, Any]) -> Any:
        if isinstance(value, Mapping):
            return {str(key): self._render_config_value(item, resolved_options) for key, item in value.items()}
        if isinstance(value, list):
            return [self._render_config_value(item, resolved_options) for item in value]
        if isinstance(value, str):
            exact = _OPTION_REF.match(value)
            if exact:
                return resolved_options.get(exact.group(1))
            return _OPTION_REF_ANYWHERE.sub(lambda match: str(resolved_options.get(match.group(1)) or ""), value)
        return value

    def _merge_config(self, base: dict[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(base)
        for key, value in update.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), dict):
                result[str(key)] = self._merge_config(result[str(key)], value)
            else:
                result[str(key)] = copy.deepcopy(value)
        return result

    def _condition_matches(self, condition: Mapping[str, Any], resolved_options: Mapping[str, Any]) -> bool:
        return all(resolved_options.get(key) == expected for key, expected in condition.items())

    def _find_existing_component(
        self,
        operation: Mapping[str, Any],
        installed: list[InstalledPackage],
    ) -> dict[str, Any] | None:
        target_key = self._target_key(operation)
        source = str(operation.get("source") or "")
        repository_alias = str(operation.get("repository_alias") or "")
        for package in installed:
            for component in package.components:
                if (
                    self._target_key(component) == target_key
                    and str(component.get("source") or "") == source
                    and str(component.get("repository_alias") or package.repository_alias) == repository_alias
                ):
                    return {**component, "package_id": package.package_id}
        return None

    def _is_component_referenced(self, component: Mapping[str, Any], installed: list[InstalledPackage]) -> bool:
        target_key = self._target_key(component)
        source = str(component.get("source") or "")
        repository_alias = str(component.get("repository_alias") or "")
        for package in installed:
            for other in package.components:
                if (
                    self._target_key(other) == target_key
                    and str(other.get("source") or "") == source
                    and str(other.get("repository_alias") or package.repository_alias) == repository_alias
                ):
                    return True
        return False

    def _target_key(self, component: Mapping[str, Any]) -> str:
        kind = str(component.get("kind") or "")
        if kind == "core":
            return f"core:{component.get('target_core_id') or component.get('target')}"
        return f"{kind}:{component.get('target')}"

    def _display_target(self, core_path: Path, operation: Mapping[str, Any]) -> str:
        if operation.get("kind") == "core":
            return str(operation.get("target_core_id"))
        target = Path(str(operation.get("target_path") or ""))
        try:
            return target.relative_to(core_path).as_posix()
        except ValueError:
            return str(operation.get("target") or target)

    def _validate_pipeline_insert(self, core_path: Path, operation: Mapping[str, Any]) -> None:
        kind = str(operation["kind"])
        slot_id = str(operation["slot_id"])
        config = operation.get("pipeline") if isinstance(operation.get("pipeline"), Mapping) else {}
        self._validate_pipeline_config(kind, config)
        pipeline = self._read_pipeline(core_path, kind)
        if slot_id in set(pipeline.get("serial") or []) | set(pipeline.get("parallel") or []):
            raise PackageOperationError(f"{kind} pipeline already contains slot: {slot_id}")

    def _insert_pipeline_slot(self, core_path: Path, operation: Mapping[str, Any]) -> None:
        kind = str(operation["kind"])
        slot_id = str(operation["slot_id"])
        config = operation.get("pipeline") if isinstance(operation.get("pipeline"), Mapping) else {}
        self._validate_pipeline_config(kind, config)
        group = str(config.get("group") or "serial")
        pipeline = self._read_pipeline(core_path, kind)
        values = list(pipeline.get(group) or [])
        after = config.get("after")
        before = config.get("before")
        if after and after in values:
            values.insert(values.index(after) + 1, slot_id)
        elif before and before in values:
            values.insert(values.index(before), slot_id)
        else:
            values.append(slot_id)
        pipeline[group] = values
        self._write_pipeline(core_path, kind, pipeline)

    def _remove_pipeline_slot(self, core_path: Path, kind: str, slot_id: str) -> None:
        pipeline = self._read_pipeline(core_path, kind)
        changed = False
        for group in ("serial", "parallel"):
            values = list(pipeline.get(group) or [])
            next_values = [item for item in values if item != slot_id]
            if next_values != values:
                pipeline[group] = next_values
                changed = True
        if changed:
            self._write_pipeline(core_path, kind, pipeline)

    def _read_pipeline(self, core_path: Path, kind: str) -> dict[str, list[str]]:
        path = core_path / "agent" / kind / "pipeline.yaml"
        raw = _read_yaml_mapping(path)
        groups = self._pipeline_groups(kind)
        unknown_keys = sorted(set(raw) - set(groups))
        if unknown_keys:
            raise PackageOperationError(f"invalid {kind} pipeline key(s): {', '.join(unknown_keys)}")
        result: dict[str, list[str]] = {}
        for group in groups:
            values = raw.get(group) or []
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise PackageOperationError(f"invalid {kind} pipeline {group}: expected list of slot ids")
            result[group] = list(values)
        return result

    def _write_pipeline(self, core_path: Path, kind: str, pipeline: Mapping[str, list[str]]) -> None:
        path = core_path / "agent" / kind / "pipeline.yaml"
        data = {"serial": pipeline.get("serial") or []}
        if "parallel" in self._pipeline_groups(kind):
            data["parallel"] = pipeline.get("parallel") or []
        path.write_text(
            yaml.safe_dump(
                data,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def _pipeline_groups(self, kind: str) -> tuple[str, ...]:
        return ("serial",) if kind == "bootstrap" else ("serial", "parallel")

    def _validate_pipeline_config(self, kind: str, config: Mapping[str, Any]) -> None:
        group = str(config.get("group") or "serial")
        if group not in self._pipeline_groups(kind):
            raise PackageOperationError(f"invalid {kind} pipeline group: {group}")

    def _normalize_option_value(
        self,
        package: PackageInfo,
        option: PackageOption,
        value: Any,
        *,
        provided: bool,
    ) -> Any:
        if value is None:
            return None
        if option.option_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "yes", "y", "1", "on"}:
                    return True
                if normalized in {"false", "no", "n", "0", "off"}:
                    return False
            raise PackageOperationError(f"package {package.package_id} option '{option.option_id}' expects a bool")
        if option.option_type == "choice":
            if not isinstance(value, str):
                raise PackageOperationError(f"package {package.package_id} option '{option.option_id}' expects a choice string")
            if value not in option.choices:
                raise PackageOperationError(
                    f"package {package.package_id} option '{option.option_id}' must be one of: {', '.join(option.choices)}"
                )
            return value
        if option.option_type in {"string", "path", "secret"}:
            if isinstance(value, (dict, list, tuple, set)):
                raise PackageOperationError(f"package {package.package_id} option '{option.option_id}' expects a scalar value")
            text = str(value)
            if provided and option.required and not text:
                return None
            return text
        raise PackageOperationError(f"package {package.package_id} option '{option.option_id}' has unsupported type")

    def _redact_options(self, package: PackageInfo, resolved_options: Mapping[str, Any]) -> dict[str, Any]:
        options_by_id = {option.option_id: option for option in package.options}
        redacted: dict[str, Any] = {}
        for option_id, value in resolved_options.items():
            option = options_by_id.get(option_id)
            if option and option.secret and not self._is_missing_option_value(value):
                redacted[option_id] = REDACTED_SECRET
            else:
                redacted[option_id] = value
        return redacted

    def _is_missing_option_value(self, value: Any) -> bool:
        return value is None or value == ""

    def _load_installed(self, core_path: Path) -> list[InstalledPackage]:
        path = self._registry_path(core_path)
        if not path.exists():
            return []
        raw = _read_yaml_mapping(path)
        values = raw.get("installed") or []
        if not isinstance(values, list) or any(not isinstance(item, Mapping) for item in values):
            raise PackageOperationError(f"invalid packages.yaml installed list: {path}")
        installed: list[InstalledPackage] = []
        for item in values:
            installed.append(
                InstalledPackage(
                    package_id=str(item.get("package_id") or ""),
                    repository_alias=str(item.get("repository_alias") or ""),
                    repository_id=str(item.get("repository_id") or ""),
                    repository_type=str(item.get("repository_type") or ""),
                    repository_ref=_optional_config_str(item.get("repository_ref")),
                    repository_commit=_optional_config_str(item.get("repository_commit")),
                    tags=[str(value) for value in item.get("tags") or []],
                    components=[dict(value) for value in item.get("components") or [] if isinstance(value, Mapping)],
                    installed_at=str(item.get("installed_at") or ""),
                    warnings=[str(value) for value in item.get("warnings") or []],
                    options=dict(item.get("options") or {}) if isinstance(item.get("options") or {}, Mapping) else {},
                )
            )
        return installed

    def _write_installed(self, core_path: Path, installed: list[InstalledPackage]) -> None:
        path = self._registry_path(core_path)
        if not installed:
            if path.exists():
                path.unlink()
            return
        ensure_dir(path.parent)
        data = {
            "schema_version": 3,
            "installed": [
                {
                    "package_id": item.package_id,
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
                for item in installed
            ],
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def _require_active_core(self, core_id: str) -> Path:
        self._validate_core_id(core_id)
        core_path = self.version_store.active_core_path(core_id)
        if not core_path.exists():
            raise PackageOperationError(f"active core not found: {core_id}")
        return core_path

    def _require_package(self, package_id: str) -> PackageInfo:
        return self.repositories.resolve_package_ref(package_id)

    def _repository_for_package(self, package: PackageInfo) -> PackageRepository:
        repository = self.repositories.repositories.get(package.repository_alias)
        if repository is None:
            raise PackageOperationError(f"package repository is not loaded: {package.repository_alias}")
        return repository

    def _package_warnings(self, package: PackageInfo) -> list[str]:
        return [f"manual dependency review required: {item}" for item in package.manual_dependencies]

    def _find_installed_record(self, installed: list[InstalledPackage], package_ref: str) -> InstalledPackage | None:
        if "/" in package_ref:
            alias, _, package_id = package_ref.partition("/")
            return next(
                (
                    item
                    for item in installed
                    if item.repository_alias == alias and item.package_id == package_id
                ),
                None,
            )
        return next((item for item in installed if item.package_id == package_ref), None)

    def _installed_package_ref(self, package: InstalledPackage) -> str:
        return f"{package.repository_alias}/{package.package_id}" if package.repository_alias else package.package_id

    def _registry_path(self, core_path: Path) -> Path:
        return core_path / "packages.yaml"

    def _relative_target(self, core_path: Path, relative: str) -> Path:
        target = Path(relative)
        if target.is_absolute() or ".." in target.parts:
            raise PackageOperationError(f"target must stay inside the target core: {relative}")
        return require_relative_path(core_path / target, core_path)

    def _rewrite_core_id(self, manifest_path: Path, core_id: str) -> None:
        raw = _read_yaml_mapping(manifest_path)
        raw.setdefault("agent", {})
        raw["agent"]["id"] = core_id
        manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    def _validate_core_id(self, value: str) -> None:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise PackageOperationError(f"invalid core id: {value}")


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PackageRepositoryError(f"invalid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackageRepositoryError(f"expected mapping YAML: {path}")
    return dict(raw)


def _required_str(raw: Mapping[str, Any], key: str, *, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PackageRepositoryError(f"{key} is required in {path}")
    return value.strip()


def _optional_str(value: Any, *, field_name: str, path: Path) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PackageRepositoryError(f"{field_name} must be a string: {path}")
    return value.strip()
