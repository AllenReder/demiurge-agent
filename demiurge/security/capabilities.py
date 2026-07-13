from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from demiurge.core import LoadedCore


class CapabilityDenied(PermissionError):
    pass


def _capability_prefixes(capability: str) -> tuple[str, ...]:
    parts = capability.split(":")
    prefixes: list[str] = []
    while len(parts) > 1:
        parts.pop()
        prefixes.append(":".join(parts) + ":*")
    return tuple(prefixes)


def _allows(capability: str, declarations: Iterable[str]) -> bool:
    declared = declarations if isinstance(declarations, (set, frozenset)) else set(declarations)
    return capability in declared or any(
        prefix in declared for prefix in _capability_prefixes(capability)
    )


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    defaults: frozenset[str]
    manifest_slots: tuple[tuple[str, frozenset[str]], ...]
    component_slots: tuple[tuple[str, frozenset[str]], ...]

    @classmethod
    def capture(cls, core: LoadedCore) -> "CapabilitySnapshot":
        declarations = core.raw_manifest.get("capabilities", {}) or {}
        raw_slots = declarations.get("slots", {}) or {}
        manifest_slots = tuple(
            sorted(
                (str(slot_path), cls._names(capabilities))
                for slot_path, capabilities in raw_slots.items()
            )
        )
        component_capabilities: dict[str, set[str]] = {}
        for slot in core.bootstrap_slots + core.input_slots + core.output_slots + core.tool_slots:
            component_capabilities.setdefault(slot.relative_path, set()).update(
                str(capability) for capability in slot.capabilities
            )
        return cls(
            defaults=cls._names(declarations.get("defaults", {}) or {}),
            manifest_slots=manifest_slots,
            component_slots=tuple(
                sorted(
                    (slot_path, frozenset(capabilities))
                    for slot_path, capabilities in component_capabilities.items()
                )
            ),
        )

    def allows(
        self,
        capability: str,
        *,
        slot_path: str | None,
        include_component_manifest: bool,
    ) -> bool:
        if slot_path:
            manifest = dict(self.manifest_slots).get(slot_path, frozenset())
            if self._matches(capability, manifest):
                return True
            if include_component_manifest:
                component = dict(self.component_slots).get(slot_path, frozenset())
                if self._matches(capability, component):
                    return True
        return self._matches(capability, self.defaults)

    @staticmethod
    def _names(value: Mapping[Any, Any] | Iterable[Any]) -> frozenset[str]:
        values = value.keys() if isinstance(value, Mapping) else value
        return frozenset(str(item) for item in values)

    @staticmethod
    def _matches(capability: str, declarations: frozenset[str]) -> bool:
        return _allows(capability, declarations)


@dataclass(slots=True)
class CapabilityFacade:
    core: LoadedCore
    audit: list[dict[str, Any]] = field(default_factory=list)
    snapshot: CapabilitySnapshot | None = None

    def can(self, capability: str, *, slot_path: str | None = None) -> bool:
        return self._is_declared(
            capability,
            slot_path=slot_path,
            include_component_manifest=True,
        )

    def require(self, capability: str, *, slot_path: str | None = None) -> None:
        self._require(
            capability,
            slot_path=slot_path,
            include_component_manifest=True,
        )

    def require_exact(self, capability: str) -> None:
        if self.snapshot is not None:
            allowed = capability in self.snapshot.defaults
        else:
            declarations = self.core.raw_manifest.get("capabilities", {}) or {}
            defaults = declarations.get("defaults", {}) or {}
            allowed = capability in defaults
        self.audit.append(
            {
                "capability": capability,
                "slot_path": None,
                "allowed": allowed,
                "exact": True,
            }
        )
        if not allowed:
            raise CapabilityDenied(
                f"exact capability denied: {capability} for host"
            )

    def _require_registry_capability(
        self,
        capability: str,
        *,
        slot_path: str,
    ) -> None:
        self._require(
            capability,
            slot_path=slot_path,
            include_component_manifest=False,
        )

    def _require(
        self,
        capability: str,
        *,
        slot_path: str | None,
        include_component_manifest: bool,
    ) -> None:
        allowed = self._is_declared(
            capability,
            slot_path=slot_path,
            include_component_manifest=include_component_manifest,
        )
        self.audit.append(
            {
                "capability": capability,
                "slot_path": slot_path,
                "allowed": allowed,
            }
        )
        if not allowed:
            raise CapabilityDenied(f"capability denied: {capability} for {slot_path or 'host'}")

    def _is_declared(
        self,
        capability: str,
        *,
        slot_path: str | None = None,
        include_component_manifest: bool = True,
    ) -> bool:
        if self.snapshot is not None:
            return self.snapshot.allows(
                capability,
                slot_path=slot_path,
                include_component_manifest=include_component_manifest,
            )
        caps = self.core.raw_manifest.get("capabilities", {}) or {}
        if slot_path:
            slot_caps = (caps.get("slots", {}) or {}).get(slot_path, {}) or {}
            if _allows(capability, slot_caps):
                return True
            for slot in self.core.bootstrap_slots + self.core.input_slots + self.core.output_slots + self.core.tool_slots:
                if slot.relative_path != slot_path:
                    continue
                if include_component_manifest:
                    if _allows(capability, slot.capabilities):
                        return True
        defaults = caps.get("defaults", {}) or {}
        return _allows(capability, defaults)
