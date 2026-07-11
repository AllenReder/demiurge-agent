from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from demiurge.core import LoadedCore


class CapabilityDenied(PermissionError):
    pass


@dataclass(slots=True)
class CapabilityFacade:
    core: LoadedCore
    audit: list[dict[str, Any]] = field(default_factory=list)

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
        caps = self.core.raw_manifest.get("capabilities", {}) or {}
        if slot_path:
            slot_caps = (caps.get("slots", {}) or {}).get(slot_path, {}) or {}
            if capability in slot_caps:
                return True
            for prefix in self._prefixes(capability):
                if prefix in slot_caps:
                    return True
            for slot in self.core.bootstrap_slots + self.core.input_slots + self.core.output_slots + self.core.tool_slots:
                if slot.relative_path != slot_path:
                    continue
                if include_component_manifest:
                    if capability in slot.capabilities:
                        return True
                    if any(prefix in slot.capabilities for prefix in self._prefixes(capability)):
                        return True
        defaults = caps.get("defaults", {}) or {}
        if capability in defaults:
            return True
        return any(prefix in defaults for prefix in self._prefixes(capability))

    def _prefixes(self, capability: str) -> list[str]:
        parts = capability.split(":")
        prefixes = []
        while len(parts) > 1:
            parts.pop()
            prefixes.append(":".join(parts) + ":*")
        return prefixes
