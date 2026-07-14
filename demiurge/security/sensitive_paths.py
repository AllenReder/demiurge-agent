from __future__ import annotations


CREDENTIAL_DIRECTORY_NAMES = frozenset({".ssh", ".aws", ".kube"})
CREDENTIAL_FILE_NAMES = frozenset({".netrc", ".npmrc", ".pgpass", ".pypirc"})


__all__ = ["CREDENTIAL_DIRECTORY_NAMES", "CREDENTIAL_FILE_NAMES"]
