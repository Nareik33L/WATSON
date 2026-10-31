"""WATSON — local-first work memory."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("watson")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.1.0"

__all__ = ["__version__"]
