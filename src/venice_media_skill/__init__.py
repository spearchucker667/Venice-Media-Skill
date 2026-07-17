"""Venice Media Skill Python bridge."""

from __future__ import annotations

import importlib.metadata

__all__ = ["__version__"]

try:
    __version__ = importlib.metadata.version(__package__ or "venice-media-skill")
except importlib.metadata.PackageNotFoundError:
    # Fallback for development without installation
    __version__ = "0.1.0"
