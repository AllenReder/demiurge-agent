from __future__ import annotations

import copy
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


@dataclass(frozen=True, slots=True)
class CatalogInfo:
    catalog_id: str
    name: str
    summary: str
    root: Path


@dataclass(frozen=True, slots=True)
class FeatureInfo:
    feature_id: str
    name: str
    summary: str
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PresetComponent:
    component_id: str
    kind: str
    source: str
    target: str | None = None
    target_core_id: str | None = None
    pipeline: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PresetOption:
    option_id: str
    option_type: str
    prompt: str
    default: Any = None
    has_default: bool = False
    required: bool = False
    choices: list[str] = field(default_factory=list)
    secret: bool = False


@dataclass(frozen=True, slots=True)
class PresetWrite:
    option_id: str
    component_id: str
    path: str


@dataclass(frozen=True, slots=True)
class PresetInfo:
    preset_id: str
    name: str
    summary: str
    feature_id: str
    tags: list[str]
    components: list[PresetComponent]
    options: list[PresetOption] = field(default_factory=list)
    writes: list[PresetWrite] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    preset_id: str
    catalog_id: str
    feature_id: str
    tags: list[str]
    components: list[dict[str, Any]]
    installed_at: str
    warnings: list[str]
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageListResult:
    catalog: CatalogInfo
    features: list[FeatureInfo]
    presets: list[PresetInfo]
    installed: list[InstalledPackage]


@dataclass(frozen=True, slots=True)
class PackageOperationResult:
    action: str
    core_id: str
    preset_id: str
    components: list[dict[str, Any]]
    warnings: list[str]
    registry_path: Path


def default_catalog_root(override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    checkout_catalog = Path(__file__).resolve().parents[1] / "agent-catalog"
    if checkout_catalog.exists():
        return checkout_catalog
    return Path(str(files("demiurge.resources").joinpath("agent-catalog")))


class PackageCatalog:
    def __init__(
        self,
        *,
        root: Path,
        catalog: CatalogInfo,
        features: dict[str, FeatureInfo],
        presets: dict[str, PresetInfo],
    ) -> None:
        self.root = root
        self.catalog = catalog
        self.features = features
        self.presets = presets

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
        features = cls._load_features(root)
        presets = cls._load_presets(root)
        for preset in presets.values():
            if preset.feature_id not in features:
                raise PackageCatalogError(f"preset {preset.preset_id} references unknown feature: {preset.feature_id}")
            for component in preset.components:
                cls._validate_component_tree(root, cls._component_source_path(root, component))
        return cls(root=root, catalog=catalog, features=features, presets=presets)

    @staticmethod
    def _load_features(root: Path) -> dict[str, FeatureInfo]:
        features_root = root / "features"
        result: dict[str, FeatureInfo] = {}
        if not features_root.exists():
            return result
        for path in sorted(features_root.glob("*.yaml"), key=lambda item: item.name):
            raw = _read_yaml_mapping(path)
            feature_id = _required_str(raw, "id", path=path)
            if feature_id in result:
                raise PackageCatalogError(f"duplicate feature id: {feature_id}")
            tags = raw.get("tags") or {}
            if not isinstance(tags, dict):
                raise PackageCatalogError(f"feature tags must be a mapping: {path}")
            result[feature_id] = FeatureInfo(
                feature_id=feature_id,
                name=str(raw.get("name") or feature_id),
                summary=str(raw.get("summary") or ""),
                tags=dict(tags),
            )
        return result

    @staticmethod
    def _load_presets(root: Path) -> dict[str, PresetInfo]:
        presets_root = root / "presets"
        result: dict[str, PresetInfo] = {}
        if not presets_root.exists():
            return result
        for path in sorted(presets_root.glob("*.yaml"), key=lambda item: item.name):
            raw = _read_yaml_mapping(path)
            preset_id = _required_str(raw, "id", path=path)
            if preset_id in result:
                raise PackageCatalogError(f"duplicate preset id: {preset_id}")
            raw_components = raw.get("components") or []
            if not isinstance(raw_components, list) or any(not isinstance(item, Mapping) for item in raw_components):
                raise PackageCatalogError(f"preset components must be a list of objects: {path}")
            tags = raw.get("tags") or []
            if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
                raise PackageCatalogError(f"preset tags must be a list of strings: {path}")
            components = [
                PresetComponent(
                    component_id=str(item.get("id") or item.get("source") or ""),
                    kind=str(item.get("kind") or ""),
                    source=str(item.get("source") or ""),
                    target=str(item["target"]) if item.get("target") is not None else None,
                    target_core_id=str(item["target_core_id"]) if item.get("target_core_id") is not None else None,
                    pipeline=dict(item.get("pipeline") or {}),
                    config=dict(item["config"]) if isinstance(item.get("config"), Mapping) else None,
                )
                for item in raw_components
            ]
            for component in components:
                if component.kind not in {"input", "output", "core"}:
                    raise PackageCatalogError(f"invalid component kind in preset {preset_id}: {component.kind}")
                if not component.component_id or not component.source:
                    raise PackageCatalogError(f"preset {preset_id} has a component without id/source")
            options = PackageCatalog._parse_preset_options(raw.get("options"), path=path, preset_id=preset_id)
            writes = PackageCatalog._parse_preset_writes(raw.get("writes"), path=path, preset_id=preset_id)
            PackageCatalog._validate_preset_options_and_writes(
                preset_id=preset_id,
                options=options,
                writes=writes,
                components=components,
                path=path,
            )
            feature_id = _required_str(raw, "feature", path=path)
            result[preset_id] = PresetInfo(
                preset_id=preset_id,
                name=str(raw.get("name") or preset_id),
                summary=str(raw.get("summary") or ""),
                feature_id=feature_id,
                tags=list(tags),
                components=components,
                options=options,
                writes=writes,
            )
        return result

    @staticmethod
    def _parse_preset_options(raw_options: Any, *, path: Path, preset_id: str) -> list[PresetOption]:
        if raw_options is None:
            return []
        if not isinstance(raw_options, list) or any(not isinstance(item, Mapping) for item in raw_options):
            raise PackageCatalogError(f"preset options must be a list of objects: {path}")
        result: list[PresetOption] = []
        seen: set[str] = set()
        for item in raw_options:
            option_id = str(item.get("id") or "").strip()
            if not option_id:
                raise PackageCatalogError(f"preset {preset_id} has an option without id")
            if option_id in seen:
                raise PackageCatalogError(f"preset {preset_id} has duplicate option id: {option_id}")
            seen.add(option_id)
            option_type = str(item.get("type") or "string").strip()
            if option_type not in OPTION_TYPES:
                raise PackageCatalogError(f"preset {preset_id} option {option_id} has invalid type: {option_type}")
            choices = item.get("choices") or []
            if not isinstance(choices, list) or any(not isinstance(choice, str) for choice in choices):
                raise PackageCatalogError(f"preset {preset_id} option {option_id} choices must be a list of strings")
            if option_type == "choice" and not choices:
                raise PackageCatalogError(f"preset {preset_id} option {option_id} choice options require choices")
            has_default = "default" in item
            default = copy.deepcopy(item.get("default"))
            required = bool(item.get("required", False))
            secret = bool(item.get("secret", option_type == "secret")) or option_type == "secret"
            prompt = str(item.get("prompt") or option_id)
            result.append(
                PresetOption(
                    option_id=option_id,
                    option_type=option_type,
                    prompt=prompt,
                    default=default,
                    has_default=has_default,
                    required=required,
                    choices=list(choices),
                    secret=secret,
                )
            )
        return result

    @staticmethod
    def _parse_preset_writes(raw_writes: Any, *, path: Path, preset_id: str) -> list[PresetWrite]:
        if raw_writes is None:
            return []
        result: list[PresetWrite] = []
        if isinstance(raw_writes, Mapping):
            items = []
            for option_id, spec in raw_writes.items():
                if not isinstance(spec, Mapping):
                    raise PackageCatalogError(f"preset writes mapping values must be objects: {path}")
                items.append({"option": option_id, **dict(spec)})
        elif isinstance(raw_writes, list):
            if any(not isinstance(item, Mapping) for item in raw_writes):
                raise PackageCatalogError(f"preset writes must be a mapping or list of objects: {path}")
            items = [dict(item) for item in raw_writes]
        else:
            raise PackageCatalogError(f"preset writes must be a mapping or list of objects: {path}")
        seen: set[tuple[str, str]] = set()
        for item in items:
            option_id = str(item.get("option") or "").strip()
            component_id = str(item.get("component") or "").strip()
            target_path = str(item.get("path") or "").strip()
            if not option_id or not component_id or not target_path:
                raise PackageCatalogError(f"preset {preset_id} write requires option, component, and path")
            PackageCatalog._validate_config_write_path(target_path, preset_id=preset_id)
            key = (component_id, target_path)
            if key in seen:
                raise PackageCatalogError(f"preset {preset_id} has duplicate write target: {component_id}:{target_path}")
            seen.add(key)
            result.append(PresetWrite(option_id=option_id, component_id=component_id, path=target_path))
        return result

    @staticmethod
    def _validate_preset_options_and_writes(
        *,
        preset_id: str,
        options: list[PresetOption],
        writes: list[PresetWrite],
        components: list[PresetComponent],
        path: Path,
    ) -> None:
        option_ids = {option.option_id for option in options}
        component_ids = {component.component_id for component in components}
        for write in writes:
            if write.option_id not in option_ids:
                raise PackageCatalogError(f"preset {preset_id} write references unknown option: {write.option_id}")
            if write.component_id not in component_ids:
                raise PackageCatalogError(f"preset {preset_id} write references unknown component: {write.component_id}")
        for option in options:
            if (
                option.option_type == "choice"
                and option.has_default
                and option.default is not None
                and option.default != ""
                and option.default not in option.choices
            ):
                raise PackageCatalogError(f"preset {preset_id} option {option.option_id} default is not in choices: {path}")

    @staticmethod
    def _validate_config_write_path(value: str, *, preset_id: str) -> None:
        parts = value.split(".")
        if len(parts) < 2 or parts[0] != "config" or any(not part or part in {".", ".."} for part in parts):
            raise PackageCatalogError(f"preset {preset_id} write path must target component config, for example config.api_key")

    @staticmethod
    def _component_source_path(root: Path, component: PresetComponent) -> Path:
        source = Path(component.source)
        if source.is_absolute() or ".." in source.parts:
            raise PackageCatalogError(f"component source must stay inside the catalog: {component.source}")
        path = root / "components" / component.kind / source
        if not path.exists():
            raise PackageCatalogError(f"component source not found: {path}")
        return require_relative_path(path, root)

    def component_source_path(self, component: PresetComponent) -> Path:
        return self._component_source_path(self.root, component)

    @staticmethod
    def _validate_component_tree(root: Path, source_path: Path) -> None:
        if source_path.is_symlink():
            raise PackageCatalogError(f"component source cannot be a symlink: {source_path}")
        if not source_path.is_dir():
            raise PackageCatalogError(f"component source must be a directory: {source_path}")
        for path in source_path.rglob("*"):
            if path.is_symlink():
                raise PackageCatalogError(f"component source cannot contain symlinks: {path}")
            require_relative_path(path, root)


class PackageManager:
    def __init__(self, *, version_store: VersionStore, catalog: PackageCatalog) -> None:
        self.version_store = version_store
        self.catalog = catalog

    def list(self, *, core_id: str | None = None) -> PackageListResult:
        installed: list[InstalledPackage] = []
        if core_id:
            installed = self._load_installed(self.version_store.active_core_path(core_id))
        return PackageListResult(
            catalog=self.catalog.catalog,
            features=sorted(self.catalog.features.values(), key=lambda item: item.feature_id),
            presets=sorted(self.catalog.presets.values(), key=lambda item: item.preset_id),
            installed=installed,
        )

    def install(
        self,
        *,
        core_id: str,
        preset_id: str,
        option_answers: Mapping[str, Any] | None = None,
    ) -> PackageOperationResult:
        core_path = self._require_active_core(core_id)
        preset = self._require_preset(preset_id)
        installed = self._load_installed(core_path)
        if any(item.preset_id == preset_id for item in installed):
            raise PackageOperationError(f"preset already installed for {core_id}: {preset_id}")
        warnings = self._tag_warnings(preset, installed)
        resolved_options = self.resolve_options(preset_id=preset_id, option_answers=option_answers)
        actual_configs = self._render_component_configs(preset, resolved_options)
        planned = [
            self._plan_component(
                core_path,
                component,
                config=actual_configs.get(component.component_id),
            )
            for component in preset.components
        ]
        self._validate_install_plan(core_path, planned)

        installed_components: list[dict[str, Any]] = []
        try:
            for operation in planned:
                installed_components.append(self._install_component(core_path, operation))
            record = InstalledPackage(
                preset_id=preset.preset_id,
                catalog_id=self.catalog.catalog.catalog_id,
                feature_id=preset.feature_id,
                tags=list(preset.tags),
                components=installed_components,
                installed_at=utc_id("pkg_"),
                warnings=warnings,
                options=self._redact_options(preset, resolved_options),
            )
            self._write_installed(core_path, [*installed, record])
        except Exception:
            for component in reversed(installed_components):
                self._remove_component(core_path, component, ignore_missing=True)
            raise

        return PackageOperationResult(
            action="install",
            core_id=core_id,
            preset_id=preset_id,
            components=installed_components,
            warnings=warnings,
            registry_path=self._registry_path(core_path),
        )

    def resolve_options(
        self,
        *,
        preset_id: str,
        option_answers: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        preset = self._require_preset(preset_id)
        answers = dict(option_answers or {})
        known = {option.option_id for option in preset.options}
        unknown = sorted(set(answers) - known)
        if unknown:
            raise PackageOperationError(f"unknown option(s) for preset {preset_id}: {', '.join(unknown)}")
        resolved: dict[str, Any] = {}
        for option in preset.options:
            provided = option.option_id in answers
            value = answers[option.option_id] if provided else copy.deepcopy(option.default if option.has_default else None)
            normalized = self._normalize_option_value(preset, option, value, provided=provided)
            if option.required and self._is_missing_option_value(normalized):
                if not provided:
                    raise PackageOperationError(
                        f"preset {preset_id} requires option '{option.option_id}'; "
                        "run `demiurge package` for interactive installation"
                    )
                raise PackageOperationError(f"preset {preset_id} option '{option.option_id}' is required")
            resolved[option.option_id] = normalized
        return resolved

    def install_warnings(self, *, core_id: str, preset_id: str) -> list[str]:
        core_path = self._require_active_core(core_id)
        preset = self._require_preset(preset_id)
        return self._tag_warnings(preset, self._load_installed(core_path))

    def uninstall(self, *, core_id: str, preset_id: str) -> PackageOperationResult:
        core_path = self._require_active_core(core_id)
        installed = self._load_installed(core_path)
        record = next((item for item in installed if item.preset_id == preset_id), None)
        if record is None:
            raise PackageOperationError(f"preset is not installed for {core_id}: {preset_id}")
        warnings: list[str] = []
        for component in reversed(record.components):
            warnings.extend(self._remove_component(core_path, component, ignore_missing=True))
        remaining = [item for item in installed if item.preset_id != preset_id]
        self._write_installed(core_path, remaining)
        return PackageOperationResult(
            action="uninstall",
            core_id=core_id,
            preset_id=preset_id,
            components=record.components,
            warnings=warnings,
            registry_path=self._registry_path(core_path),
        )

    def _require_active_core(self, core_id: str) -> Path:
        self._validate_core_id(core_id)
        core_path = self.version_store.active_core_path(core_id)
        if not core_path.exists():
            raise PackageOperationError(f"active core not found: {core_id}")
        return core_path

    def _require_preset(self, preset_id: str) -> PresetInfo:
        preset = self.catalog.presets.get(preset_id)
        if preset is None:
            raise PackageOperationError(f"unknown preset: {preset_id}")
        return preset

    def _plan_component(
        self,
        core_path: Path,
        component: PresetComponent,
        *,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source_path = self.catalog.component_source_path(component)
        if component.kind in {"input", "output"}:
            target_rel = component.target or f"agent/{component.kind}/{component.component_id}"
            target_path = self._relative_target(core_path, target_rel)
            return {
                "kind": component.kind,
                "component_id": component.component_id,
                "source": str(source_path),
                "target": target_path.relative_to(core_path).as_posix(),
                "slot_id": target_path.name,
                "pipeline": dict(component.pipeline or {"group": "serial"}),
                "config": config,
            }
        target_core_id = component.target_core_id or component.component_id
        self._validate_core_id(target_core_id)
        target_path = self.version_store.active_core_path(target_core_id)
        return {
            "kind": "core",
            "component_id": component.component_id,
            "source": str(source_path),
            "target_core_id": target_core_id,
            "target": str(target_path),
        }

    def _validate_install_plan(self, core_path: Path, planned: list[dict[str, Any]]) -> None:
        seen_targets: set[str] = set()
        for operation in planned:
            target_key = operation["target"]
            if target_key in seen_targets:
                raise PackageOperationError(f"preset contains duplicate target: {target_key}")
            seen_targets.add(target_key)
            if operation["kind"] in {"input", "output"}:
                target_path = self._relative_target(core_path, str(operation["target"]))
                if target_path.exists():
                    raise PackageOperationError(f"target already exists: {target_path.relative_to(core_path).as_posix()}")
                self._validate_pipeline_insert(core_path, operation)
                continue
            target_path = Path(str(operation["target"]))
            if target_path.exists():
                raise PackageOperationError(f"target core already exists: {operation['target_core_id']}")

    def _install_component(self, core_path: Path, operation: dict[str, Any]) -> dict[str, Any]:
        source_path = Path(str(operation["source"]))
        if operation["kind"] in {"input", "output"}:
            target_path = self._relative_target(core_path, str(operation["target"]))
            shutil.copytree(source_path, target_path)
            config = operation.get("config")
            if isinstance(config, dict):
                (target_path / "config.yaml").write_text(
                    yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
            self._insert_pipeline_slot(core_path, operation)
            result = {
                key: value
                for key, value in operation.items()
                if key in {"kind", "component_id", "target", "slot_id", "pipeline"}
            }
            return result
        target_core_id = str(operation["target_core_id"])
        target_path = self.version_store.active_core_path(target_core_id)
        shutil.copytree(source_path, target_path)
        self._rewrite_core_id(target_path / "agent.yaml", target_core_id)
        return {
            key: value
            for key, value in operation.items()
            if key in {"kind", "component_id", "target_core_id", "target"}
        }

    def _remove_component(self, core_path: Path, component: Mapping[str, Any], *, ignore_missing: bool) -> list[str]:
        warnings: list[str] = []
        kind = str(component.get("kind") or "")
        if kind in {"input", "output"}:
            target = str(component.get("target") or "")
            if target:
                target_path = self._relative_target(core_path, target)
                if target_path.exists():
                    shutil.rmtree(target_path)
                elif not ignore_missing:
                    raise PackageOperationError(f"target not found: {target}")
                else:
                    warnings.append(f"target already missing: {target}")
            slot_id = str(component.get("slot_id") or Path(target).name)
            self._remove_pipeline_slot(core_path, kind, slot_id)
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
        path.write_text(yaml.safe_dump({"serial": pipeline.get("serial") or [], "parallel": pipeline.get("parallel") or []}, sort_keys=False), encoding="utf-8")

    def _tag_warnings(self, preset: PresetInfo, installed: list[InstalledPackage]) -> list[str]:
        warnings: list[str] = []
        preset_tags = set(preset.tags)
        for item in installed:
            overlap = sorted(preset_tags & set(item.tags))
            if overlap:
                warnings.append(
                    f"preset {preset.preset_id} shares tag(s) {', '.join(overlap)} with installed preset {item.preset_id}"
                )
        return warnings

    def _normalize_option_value(
        self,
        preset: PresetInfo,
        option: PresetOption,
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
            raise PackageOperationError(f"preset {preset.preset_id} option '{option.option_id}' expects a bool")
        if option.option_type == "choice":
            if not isinstance(value, str):
                raise PackageOperationError(f"preset {preset.preset_id} option '{option.option_id}' expects a choice string")
            if value not in option.choices:
                raise PackageOperationError(
                    f"preset {preset.preset_id} option '{option.option_id}' must be one of: {', '.join(option.choices)}"
                )
            return value
        if option.option_type in {"string", "path", "secret"}:
            if isinstance(value, (dict, list, tuple, set)):
                raise PackageOperationError(f"preset {preset.preset_id} option '{option.option_id}' expects a scalar value")
            text = str(value)
            if provided and option.required and not text:
                return None
            return text
        raise PackageOperationError(f"preset {preset.preset_id} option '{option.option_id}' has unsupported type")

    def _render_component_configs(
        self,
        preset: PresetInfo,
        resolved_options: Mapping[str, Any],
    ) -> dict[str, dict[str, Any] | None]:
        actual = {
            component.component_id: copy.deepcopy(component.config) if isinstance(component.config, Mapping) else None
            for component in preset.components
        }
        for write in preset.writes:
            value = resolved_options.get(write.option_id)
            if actual.get(write.component_id) is None:
                actual[write.component_id] = {}
            self._write_config_value(actual[write.component_id], write.path, value)
        return actual

    def _redact_options(self, preset: PresetInfo, resolved_options: Mapping[str, Any]) -> dict[str, Any]:
        options_by_id = {option.option_id: option for option in preset.options}
        redacted: dict[str, Any] = {}
        for option_id, value in resolved_options.items():
            option = options_by_id.get(option_id)
            if option and option.secret and not self._is_missing_option_value(value):
                redacted[option_id] = REDACTED_SECRET
            else:
                redacted[option_id] = value
        return redacted

    def _write_config_value(self, config: dict[str, Any] | None, path: str, value: Any) -> None:
        if config is None:
            raise PackageOperationError(f"cannot write option into empty component config: {path}")
        parts = path.split(".")[1:]
        cursor: dict[str, Any] = config
        for part in parts[:-1]:
            next_value = cursor.get(part)
            if next_value is None:
                next_value = {}
                cursor[part] = next_value
            if not isinstance(next_value, dict):
                raise PackageOperationError(f"cannot write option through non-mapping config path: {path}")
            cursor = next_value
        cursor[parts[-1]] = value

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
                    preset_id=str(item.get("preset_id") or ""),
                    catalog_id=str(item.get("catalog_id") or ""),
                    feature_id=str(item.get("feature_id") or ""),
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
            "schema_version": 1,
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
                for item in installed
            ],
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

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
