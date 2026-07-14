from __future__ import annotations


class BaselineContractFailure(AssertionError):
    """Expected failure of a not-yet-satisfied stress contract."""
