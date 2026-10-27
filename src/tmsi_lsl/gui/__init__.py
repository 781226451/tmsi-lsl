"""GUI package: Qt Quick front-end for the TMSi SAGA streamer."""

from __future__ import annotations

__all__ = ["main"]


def main(argv=None) -> int:  # noqa: D103 - thin lazy re-export
    from .app import main as _main

    return _main(argv)
