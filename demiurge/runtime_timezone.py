from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEMIURGE_TIMEZONE_ENV = "DEMIURGE_TIMEZONE"


@dataclass(frozen=True, slots=True)
class RuntimeTimezone:
    zone: tzinfo
    name: str
    source: str
    explicit: bool

    def local_now(self) -> datetime:
        return datetime.now(self.zone)

    def format_local(self, value: datetime) -> str:
        return value.astimezone(self.zone).isoformat()

    def metadata(self, *, now: datetime | None = None) -> dict[str, str | bool]:
        current = (now or self.local_now()).astimezone(self.zone)
        return {
            "runtime_timezone": self.name,
            "runtime_timezone_source": self.source,
            "runtime_timezone_explicit": self.explicit,
            "runtime_timezone_offset": current.strftime("%z"),
            "runtime_local_now": current.isoformat(),
        }

    def apply_subprocess_env(self, env: dict[str, str]) -> dict[str, str]:
        next_env = dict(env)
        next_env.pop(DEMIURGE_TIMEZONE_ENV, None)
        if self.explicit:
            next_env["TZ"] = self.name
        return next_env


def resolve_runtime_timezone(
    *,
    override: str | None = None,
    config_value: str | None = None,
    config_source: str = "config.yaml:runtime.timezone",
) -> RuntimeTimezone:
    override_value = _normalize_optional_timezone(override)
    if override_value:
        return _explicit_timezone(override_value, "cli")

    env_value = _normalize_optional_timezone(os.environ.get(DEMIURGE_TIMEZONE_ENV))
    if env_value:
        return _explicit_timezone(env_value, f"env:{DEMIURGE_TIMEZONE_ENV}")

    configured = _normalize_optional_timezone(config_value)
    if configured:
        return _explicit_timezone(configured, config_source)

    return _server_local_timezone()


def validate_timezone_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("timezone must not be empty")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {normalized}") from exc
    return normalized


def _explicit_timezone(value: str, source: str) -> RuntimeTimezone:
    name = validate_timezone_name(value)
    return RuntimeTimezone(zone=ZoneInfo(name), name=name, source=source, explicit=True)


def _server_local_timezone() -> RuntimeTimezone:
    local_name = _local_zoneinfo_name()
    if local_name:
        try:
            return RuntimeTimezone(
                zone=ZoneInfo(local_name),
                name=local_name,
                source="server-local",
                explicit=False,
            )
        except ZoneInfoNotFoundError:
            pass
    local_zone = datetime.now().astimezone().tzinfo
    if local_zone is None:
        local_zone = ZoneInfo("UTC")
    label = getattr(local_zone, "key", None) or str(local_zone) or "server-local"
    return RuntimeTimezone(zone=local_zone, name=label, source="server-local", explicit=False)


def _local_zoneinfo_name() -> str | None:
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            ZoneInfo(tz_env)
        except ZoneInfoNotFoundError:
            pass
        else:
            return tz_env

    localtime = Path("/etc/localtime")
    try:
        target = localtime.resolve(strict=True)
    except OSError:
        return None
    parts = target.parts
    marker = "zoneinfo"
    if marker not in parts:
        return None
    index = parts.index(marker)
    name = "/".join(parts[index + 1 :])
    return name or None


def _normalize_optional_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
