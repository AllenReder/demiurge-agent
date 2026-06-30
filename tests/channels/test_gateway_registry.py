import shutil

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.channels.gateway import GatewayConfigError, build_enabled_gateway_channels
from demiurge.channels.webhook import WebhookInteractionBridge
from demiurge.core import CoreLoader, WebhookChannelConfig


class FakeVersionStore:
    def __init__(self, path):
        self.path = path

    def active_core_path(self, core_id):
        return self.path


class FakeRunner:
    core_id = "assistant"

    async def run_turn(self, *args, **kwargs):
        raise AssertionError("runner should not be called while building channels")


def _app_for_core(path):
    return type(
        "FakeApp",
        (),
        {
            "core_loader": CoreLoader(),
            "version_store": FakeVersionStore(path),
            "runner": FakeRunner(),
            "tool_display": "summary",
            "channel_busy_mode": "queue",
        },
    )()


def _copy_assistant(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    return target


def test_core_manifest_accepts_webhook_channel_config(tmp_path):
    target = _copy_assistant(tmp_path)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("channels", {})["webhook"] = {
        "enabled": True,
        "allow_unauthenticated": True,
        "path": "demiurge",
    }
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)

    assert isinstance(core.manifest.channels["webhook"], WebhookChannelConfig)
    assert core.manifest.channels["webhook"].path == "/demiurge"


def test_gateway_builds_registered_non_telegram_channel(tmp_path):
    target = _copy_assistant(tmp_path)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("channels", {})["webhook"] = {"enabled": True, "allow_unauthenticated": True}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    channels = build_enabled_gateway_channels(_app_for_core(target))

    assert [channel.name for channel in channels] == ["webhook"]
    assert isinstance(channels[0].bridge, WebhookInteractionBridge)
    assert channels[0].bridge.default_busy_mode == "queue"


def test_gateway_rejects_enabled_unknown_channel(tmp_path):
    target = _copy_assistant(tmp_path)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("channels", {})["unknown_chat"] = {"enabled": True}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(GatewayConfigError, match="unsupported enabled gateway channel"):
        build_enabled_gateway_channels(_app_for_core(target))
