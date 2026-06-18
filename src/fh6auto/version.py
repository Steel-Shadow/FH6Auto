from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from packaging.version import InvalidVersion, Version


PACKAGE_NAME = "fh6auto"
UNKNOWN_VERSION = "0.0.0"


def get_project_version() -> str:
    """Read the package version managed by uv via pyproject metadata."""
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def is_newer_version(candidate: str, current: str | None = None) -> bool:
    try:
        return Version(candidate) > Version(current or CURRENT_VERSION)
    except InvalidVersion:
        return False


CURRENT_VERSION = get_project_version()
