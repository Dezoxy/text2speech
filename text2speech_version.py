from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

PROJECT_DIST_NAME = "text2speech"
FALLBACK_VERSION = "0.4.0"


def get_version() -> str:
    try:
        return version(PROJECT_DIST_NAME)
    except PackageNotFoundError:
        return FALLBACK_VERSION
