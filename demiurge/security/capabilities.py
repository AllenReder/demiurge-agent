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
        return self._is_declared(capability, slot_path=slot_path)

    def require(self, capability: str, *, slot_path: str | None = None) -> None:
        allowed = self.can(capability, slot_path=slot_path)
        self.audit.append(
            {
                "capability": capability,
                "slot_path": slot_path,
                "allowed": allowed,
            }
        )
        if not allowed:
            raise CapabilityDenied(f"capability denied: {capability} for {slot_path or 'host'}")

    def _is_declared(self, capability: str, *, slot_path: str | None = None) -> bool:
        caps = self.core.raw_manifest.get("capabilities", {}) or {}
        if slot_path:
            slot_caps = (caps.get("slots", {}) or {}).get(slot_path, {}) or {}
            if capability in slot_caps:
                return True
            for prefix in self._prefixes(capability):
                if prefix in slot_caps:
                    return True
            for slot in self.core.input_slots + self.core.output_slots + self.core.tool_slots:
                if slot.relative_path == slot_path and capability in slot.capabilities:
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
