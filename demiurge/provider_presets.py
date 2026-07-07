from __future__ import annotations

from dataclasses import dataclass

from demiurge.providers.profiles import BUILTIN_PROVIDER_PROFILES, ProviderRuntimeProfile, get_builtin_provider_profile


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    preset_id: str
    label: str
    suggested_model: str | None

    @property
    def runtime_profile(self) -> ProviderRuntimeProfile:
        profile = get_builtin_provider_profile(self.preset_id)
        if profile is None:
            raise KeyError(self.preset_id)
        return profile


BUILTIN_PROVIDER_PRESETS: tuple[ProviderPreset, ...] = tuple(
    ProviderPreset(
        preset_id=profile.provider_id,
        label=profile.display_name,
        suggested_model=profile.suggested_model,
    )
    for profile in BUILTIN_PROVIDER_PROFILES
)

BUILTIN_PROVIDER_PRESETS_BY_ID = {preset.preset_id: preset for preset in BUILTIN_PROVIDER_PRESETS}


def get_provider_preset(preset_id: str) -> ProviderPreset | None:
    return BUILTIN_PROVIDER_PRESETS_BY_ID.get(preset_id)
