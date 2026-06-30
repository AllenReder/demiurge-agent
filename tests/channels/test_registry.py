from demiurge.channels.registry import ChannelRegistration, get_channel_registration, register_channel, registered_channels


class CustomConfig:
    pass


def _build(_app, _config):
    raise AssertionError("not used")


def test_custom_registration_before_builtin_lookup_keeps_builtins():
    register_channel(ChannelRegistration(name="custom", config_type=CustomConfig, build_bridge=_build))

    channels = registered_channels()

    assert "custom" in channels
    assert get_channel_registration("telegram") is not None
    assert get_channel_registration("webhook") is not None
