from __future__ import annotations

import copy
import re
import shutil
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml

from demiurge.storage import VersionStore
from demiurge.util import ensure_dir, require_relative_path, utc_id


class PackageCatalogError(ValueError):
    pass


class PackageOperationError(RuntimeError):
    pass


REDACTED_SECRET = "<redacted>"
OPTION_TYPES = {"string", "bool", "choice", "path", "secret"}
COMPONENT_KINDS = {"input", "output", "tool", "skill", "lib", "core"}
CORE_LOCAL_KINDS = {"input", "output", "tool", "skill", "lib"}
PIPELINE_KINDS = {"input", "output"}
DEFAULT_TARGET_ROOTS = {
    "input": "agent/input",
    "output": "agent/output",
    "tool": "agent/tools",
    "skill": "agent/skills",
    "lib": "agent/lib",
}
_OPTION_REF = re.compile(r"^\$\{options\.([A-Za-z0-9_-]+)\}$")
_OPTION_REF_ANYWHERE = re.compile(r"\$\{options\.([A-Za-z0-9_-]+)\}")


@dataclass(frozen=True, slots=True)
class CatalogInfo:
    catalog_id: str
    name: str
    summary: str
    root: Path


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


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    package_id: str
    catalog_id: str
    tags: list[str]
    components: list[dict[str, Any]]
    installed_at: str
    warnings: list[str]
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageListResult:
    catalog: CatalogInfo
    packages: list[PackageInfo]
    installed: list[InstalledPackage]
    tags: list[str]


@dataclass(frozen=True, slots=True)
class PackageOperationPreview:
    action: str
    core_id: str
    package_id: str
    components: list[dict[str, Any]]
    warnings: list[str]
    registry_path: Path
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageOperationResult:
    action: str
    core_id: str
    package_id: str
    components: list[dict[str, Any]]
    warnings: list[str]
    registry_path: Path
    options: dict[str, Any] = field(default_factory=dict)


def default_catalog_root(override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    checkout_catalog = Path(__file__).resolve().parents[1] / "agent-catalog"
    if checkout_catalog.exists():
        return checkout_catalog
    return Path(str(files("demiurge.resources").joinpath("agent-catalog")))


class PackageCatalog:
    def __init__(self, *, root: Path, catalog: CatalogInfo, packages: dict[str, PackageInfo]) -> None:
        self.root = root
        self.catalog = catalog
        self.packages = packages

    @classmethod
    def load(cls, root: Path) -> "PackageCatalog":
        root = root.expanduser().resolve()
        catalog_path = root / "catalog.yaml"
        if not catalog_path.exists():
            raise PackageCatalogError(f"catalog.yaml not found: {catalog_path}")
        raw_catalog = _read_yaml_mapping(catalog_path)
        catalog_id = _required_str(raw_catalog, "id", path=catalog_path)
        catalog = CatalogInfo(
            catalog_id=catalog_id,
            name=str(raw_catalog.get("name") or catalog_id),
            summary=str(raw_catalog.get("summary") or ""),
            root=root,
        )
        packages = cls._load_packages(root)
        for package in packages.values():
            for component in package.components:
                cls._validate_component_tree(root, cls._component_source_path(root, component))
        return cls(root=root, catalog=catalog, packages=packages)

    @staticmethod
    def _load_packages(root: Path) -> dict[str, PackageInfo]:
        packages_root = root / "packages"
        result: dict[str, PackageInfo] = {}
        if not packages_root.exists():
            return result
        for path in sorted(packages_root.glob("*.yaml"), key=lambda item: item.name):
            raw = _read_yaml_mapping(path)
            package_id = _required_str(raw, "id", path=path)
            if package_id in result:
                raise PackageCatalogError(f"duplicate package id: {package_id}")
            tags = raw.get("tags") or []
            if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
                raise PackageCatalogError(f"package tags must be a list of strings: {path}")
            raw_components = raw.get("components") or []
            if not isinstance(raw_components, list) or any(not isinstance(item, Mapping) for item in raw_components):
                raise PackageCatalogError(f"package components must be a list of objects: {path}")
            components = [PackageCatalog._parse_component(item, package_id=package_id, path=path) for item in raw_components]
            PackageCatalog._reject_duplicate_component_ids(package_id, components)
            options = PackageCatalog._parse_options(raw.get("options"), path=path, package_id=package_id)
            PackageCatalog._validate_component_conditions(package_id, components=components, options=options)
            result[package_id] = PackageInfo(
                package_id=package_id,
                name=str(raw.get("name") or package_id),
                summary=str(raw.get("summary") or ""),
                tags=list(tags),
                components=components,
                options=options,
            )
        return result

    @staticmethod
    def _parse_component(raw: Mapping[str, Any], *, package_id: str, path: Path) -> PackageComponent:
        component_id = str(raw.get("id") or raw.get("source") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        source = str(raw.get("source") or "").strip()
        if kind not in COMPONENT_KINDS:
            raise PackageCatalogError(f"invalid component kind in package {package_id}: {kind}")
        if not component_id or not source:
            raise PackageCatalogError(f"package {package_id} has a component without id/source")
        pipeline = raw.get("pipeline") or {}
        if not isinstance(pipeline, Mapping):
            raise PackageCatalogError(f"component {component_id} pipeline must be a mapping: {path}")
        config = raw.get("config")
        if config is not None and not isinstance(config, Mapping):
            raise PackageCatalogError(f"component {component_id} config must be a mapping: {path}")
        config_when = PackageCatalog._parse_config_when(raw.get("config_when"), package_id=package_id, path=path)
        return PackageComponent(
            component_id=component_id,
            kind=kind,
            source=source,
            target=str(raw["target"]) if raw.get("target") is not None else None,
            target_core_id=str(raw["target_core_id"]) if raw.get("target_core_id") is not None else None,
            pipeline=dict(pipeline),
            config=dict(config) if isinstance(config, Mapping) else None,
            when=PackageCatalog._parse_condition(raw.get("when"), package_id=package_id, path=path),
            config_when=config_when,
        )

    @staticmethod
    def _parse_config_when(raw: Any, *, package_id: str, path: Path) -> list[ConditionalConfig]:
        if raw is None:
            return []
        if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
            raise PackageCatalogError(f"package {package_id} config_when must be a list of objects: {path}")
        result: list[ConditionalConfig] = []
        for item in raw:
            config = item.get("config")
            if not isinstance(config, Mapping):
                raise PackageCatalogError(f"package {package_id} config_when entries require config mapping: {path}")
            result.append(
                ConditionalConfig(
                    when=PackageCatalog._parse_condition(item.get("when"), package_id=package_id, path=path),
                    config=dict(config),
                )
            )
        return result

    @staticmethod
    def _parse_condition(raw: Any, *, package_id: str, path: Path) -> dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, Mapping) or any(not isinstance(key, str) or not key.strip() for key in raw):
            raise PackageCatalogError(f"package {package_id} when must be a mapping of option ids: {path}")
        return {str(key): copy.deepcopy(value) for key, value in raw.items()}

    @staticmethod
    def _parse_options(raw_options: Any, *, path: Path, package_id: str) -> list[PackageOption]:
        if raw_options is None:
            return []
        if not isinstance(raw_options, list) or any(not isinstance(item, Mapping) for item in raw_options):
            raise PackageCatalogError(f"package options must be a list of objects: {path}")
        result: list[PackageOption] = []
        seen: set[str] = set()
        for item in raw_options:
            option_id = str(item.get("id") or "").strip()
            if not option_id:
                raise PackageCatalogError(f"package {package_id} has an option without id")
            if option_id in seen:
                raise PackageCatalogError(f"package {package_id} has duplicate option id: {option_id}")
            seen.add(option_id)
            option_type = str(item.get("type") or "string").strip()
            if option_type not in OPTION_TYPES:
                raise PackageCatalogError(f"package {package_id} option {option_id} has invalid type: {option_type}")
            choices, choice_descriptions = PackageCatalog._parse_option_choices(
                item.get("choices") or [],
                package_id=package_id,
                option_id=option_id,
                path=path,
            )
            if option_type == "choice" and not choices:
                raise PackageCatalogError(f"package {package_id} option {option_id} choice options require choices")
            has_default = "default" in item
            default = copy.deepcopy(item.get("default"))
            if option_type == "choice" and has_default and default not in {None, ""} and default not in choices:
                raise PackageCatalogError(f"package {package_id} option {option_id} default is not in choices: {path}")
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
            raise PackageCatalogError(f"package {package_id} option {option_id} choices must be a list: {path}")
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
                    raise PackageCatalogError(f"package {package_id} option {option_id} choice requires value: {path}")
                description = _optional_str(
                    raw_choice.get("description"),
                    field_name=f"option {option_id} choice {value} description",
                    path=path,
                )
            else:
                raise PackageCatalogError(f"package {package_id} option {option_id} choices must be strings or objects: {path}")
            if value in seen:
                raise PackageCatalogError(f"package {package_id} option {option_id} has duplicate choice: {value}")
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
                raise PackageCatalogError(f"package {package_id} has duplicate component id: {component.component_id}")
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
                    raise PackageCatalogError(f"package {package_id} component {component.component_id} references unknown option: {option_id}")
            for conditional in component.config_when:
                for option_id in conditional.when:
                    if option_id not in option_ids:
                        raise PackageCatalogError(f"package {package_id} component {component.component_id} config_when references unknown option: {option_id}")

    @staticmethod
    def _component_source_path(root: Path, component: PackageComponent) -> Path:
        source = Path(component.source)
        if source.is_absolute() or ".." in source.parts:
            raise PackageCatalogError(f"component source must stay inside the catalog: {component.source}")
        path = root / component.kind / source
        if not path.exists():
            raise PackageCatalogError(f"component source not found: {path}")
        return require_relative_path(path, root)

    def component_source_path(self, component: PackageComponent) -> Path:
        return self._component_source_path(self.root, component)

    @staticmethod
    def _validate_component_tree(root: Path, source_path: Path) -> None:
        if source_path.is_symlink():
            raise PackageCatalogError(f"component source cannot be a symlink: {source_path}")
        if source_path.is_file():
            require_relative_path(source_path, root)
            return
        if not source_path.is_dir():
            raise PackageCatalogError(f"component source must be a directory or file: {source_path}")
        for path in source_path.rglob("*"):
            if path.is_symlink():
                raise PackageCatalogError(f"component source cannot contain symlinks: {path}")
            require_relative_path(path, root)


class PackageManager:
    def __init__(self, *, version_store: VersionStore, catalog: PackageCatalog) -> None:
        self.version_store = version_store
        self.catalog = catalog

    def list(self, *, core_id: str | None = None, tag: str | None = None) -> PackageListResult:
        installed: list[InstalledPackage] = []
        if core_id:
            installed = self._load_installed(self.version_store.active_core_path(core_id))
        packages = sorted(self.catalog.packages.values(), key=lambda item: item.package_id)
        if tag:
            packages = [package for package in packages if tag in package.tags]
        return PackageListResult(
            catalog=self.catalog.catalog,
            packages=packages,
            installed=installed,
            tags=sorted({tag for package in self.catalog.packages.values() for tag in package.tags}),
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
        resolved_options = self.resolve_options(package_id=package_id, option_answers=option_answers)
        installed = self._load_installed(core_path)
        if any(item.package_id == package_id for item in installed):
            raise PackageOperationError(f"package already installed for {core_id}: {package_id}")
        planned = self._build_install_operations(core_path, package, installed, resolved_options)
        return PackageOperationPreview(
            action="install",
            core_id=core_id,
            package_id=package_id,
            components=[self._operation_preview(operation) for operation in planned],
            warnings=[],
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
        if any(item.package_id == package_id for item in installed):
            raise PackageOperationError(f"package already installed for {core_id}: {package_id}")
        resolved_options = self.resolve_options(package_id=package_id, option_answers=option_answers)
        planned = self._build_install_operations(core_path, package, installed, resolved_options)
        installed_components: list[dict[str, Any]] = []
        try:
            for operation in planned:
                installed_components.append(self._install_component(core_path, operation))
            record = InstalledPackage(
                package_id=package.package_id,
                catalog_id=self.catalog.catalog.catalog_id,
                tags=list(package.tags),
                components=installed_components,
                installed_at=utc_id("pkg_"),
                warnings=[],
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
            package_id=package_id,
            components=installed_components,
            warnings=[],
            registry_path=self._registry_path(core_path),
            options=self._redact_options(package, resolved_options),
        )

    def preview_uninstall(self, *, core_id: str, package_id: str) -> PackageOperationPreview:
        core_path = self._require_active_core(core_id)
        installed = self._load_installed(core_path)
        record = next((item for item in installed if item.package_id == package_id), None)
        if record is None:
            raise PackageOperationError(f"package is not installed for {core_id}: {package_id}")
        remaining = [item for item in installed if item.package_id != package_id]
        components = [self._uninstall_component_preview(component, remaining=remaining) for component in record.components]
        return PackageOperationPreview(
            action="uninstall",
            core_id=core_id,
            package_id=package_id,
            components=components,
            warnings=[],
            registry_path=self._registry_path(core_path),
            options=dict(record.options),
        )

    def uninstall(self, *, core_id: str, package_id: str) -> PackageOperationResult:
        core_path = self._require_active_core(core_id)
        installed = self._load_installed(core_path)
        record = next((item for item in installed if item.package_id == package_id), None)
        if record is None:
            raise PackageOperationError(f"package is not installed for {core_id}: {package_id}")
        remaining = [item for item in installed if item.package_id != package_id]
        warnings: list[str] = []
        for component in reversed(record.components):
            warnings.extend(self._remove_component(core_path, component, remaining=remaining, ignore_missing=True))
        self._write_installed(core_path, remaining)
        return PackageOperationResult(
            action="uninstall",
            core_id=core_id,
            package_id=package_id,
            components=record.components,
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
        source_path = self.catalog.component_source_path(component)
        source_key = f"{component.kind}/{component.source}"
        config = self._render_component_config(component, resolved_options)
        if component.kind in CORE_LOCAL_KINDS:
            target_rel = component.target or f"{DEFAULT_TARGET_ROOTS[component.kind]}/{Path(component.source).name}"
            target_path = self._relative_target(core_path, target_rel)
            operation = {
                "kind": component.kind,
                "component_id": component.component_id,
                "package_id": package.package_id,
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
            "source": source_key,
            "source_path": str(source_path),
            "target_core_id": target_core_id,
            "target": str(target_path),
            "target_path": str(target_path),
            "reused": False,
        }

    def _validate_install_plan(
        self,
        core_path: Path,
        planned: list[dict[str, Any]],
        *,
        installed: list[InstalledPackage],
    ) -> None:
        seen_targets: set[str] = set()
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
            if operation["kind"] in PIPELINE_KINDS:
                self._validate_pipeline_insert(core_path, operation)

    def _install_component(self, core_path: Path, operation: dict[str, Any]) -> dict[str, Any]:
        if operation.get("reused"):
            return self._component_record(operation)
        source_path = Path(str(operation["source_path"]))
        target_path = Path(str(operation["target_path"]))
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
        shutil.copytree(source_path, target_path)
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
        if kind in CORE_LOCAL_KINDS:
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
            shutil.copytree(source_path, target_path)
            return
        ensure_dir(target_path.parent)
        shutil.copy2(source_path, target_path)

    def _component_record(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        keep = {
            "kind",
            "component_id",
            "package_id",
            "source",
            "target",
            "target_core_id",
            "slot_id",
            "pipeline",
            "reused",
            "reused_by",
        }
        return {key: value for key, value in operation.items() if key in keep and value is not None}

    def _operation_preview(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        preview = self._component_record(operation)
        if isinstance(operation.get("config"), dict):
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
        for package in installed:
            for component in package.components:
                if self._target_key(component) == target_key and str(component.get("source") or "") == source:
                    return {**component, "package_id": package.package_id}
        return None

    def _is_component_referenced(self, component: Mapping[str, Any], installed: list[InstalledPackage]) -> bool:
        target_key = self._target_key(component)
        source = str(component.get("source") or "")
        for package in installed:
            for other in package.components:
                if self._target_key(other) == target_key and str(other.get("source") or "") == source:
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
        pipeline = self._read_pipeline(core_path, kind)
        if slot_id in set(pipeline.get("serial") or []) | set(pipeline.get("parallel") or []):
            raise PackageOperationError(f"{kind} pipeline already contains slot: {slot_id}")

    def _insert_pipeline_slot(self, core_path: Path, operation: Mapping[str, Any]) -> None:
        kind = str(operation["kind"])
        slot_id = str(operation["slot_id"])
        config = operation.get("pipeline") if isinstance(operation.get("pipeline"), Mapping) else {}
        group = str(config.get("group") or "serial")
        if group not in {"serial", "parallel"}:
            raise PackageOperationError(f"invalid pipeline group: {group}")
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
        result: dict[str, list[str]] = {}
        for group in ("serial", "parallel"):
            values = raw.get(group) or []
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise PackageOperationError(f"invalid {kind} pipeline {group}: expected list of slot ids")
            result[group] = list(values)
        return result

    def _write_pipeline(self, core_path: Path, kind: str, pipeline: Mapping[str, list[str]]) -> None:
        path = core_path / "agent" / kind / "pipeline.yaml"
        path.write_text(
            yaml.safe_dump(
                {"serial": pipeline.get("serial") or [], "parallel": pipeline.get("parallel") or []},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

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
                    catalog_id=str(item.get("catalog_id") or ""),
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
            "schema_version": 2,
            "installed": [
                {
                    "package_id": item.package_id,
                    "catalog_id": item.catalog_id,
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
        package = self.catalog.packages.get(package_id)
        if package is None:
            raise PackageOperationError(f"unknown package: {package_id}")
        return package

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
        raise PackageCatalogError(f"invalid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackageCatalogError(f"expected mapping YAML: {path}")
    return dict(raw)


def _required_str(raw: Mapping[str, Any], key: str, *, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PackageCatalogError(f"{key} is required in {path}")
    return value.strip()


def _optional_str(value: Any, *, field_name: str, path: Path) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PackageCatalogError(f"{field_name} must be a string: {path}")
    return value.strip()
