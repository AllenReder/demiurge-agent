from __future__ import annotations

from demiurge import app as app_module
from demiurge.app import DemiurgeApp
from demiurge.runtime import scope as scope_module
from demiurge.runtime.store import RuntimeStore


def activate_test_operator_authority(store: RuntimeStore) -> DemiurgeApp:
    """Register a bounded white-box Host lifecycle for direct-store tests."""
    host = object.__new__(DemiurgeApp)
    host.runtime_store = store
    host._operator_authority = None
    host._closed = False
    app_module._ACTIVE_APP_LIFECYCLES[id(host)] = host
    host._operator_authority = scope_module._activate_operator_authority(store, host)
    store._test_operator_host = host
    return host
