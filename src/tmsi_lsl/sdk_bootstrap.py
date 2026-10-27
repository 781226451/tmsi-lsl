"""Locate the vendored TMSi Python Interface and make it importable.

The TMSi SDK is distributed as a plain source tree (``TMSiSDK/``,
``TMSiFileFormats/``, ...) without packaging metadata, so it cannot be
installed with ``pip``/``uv``.  Instead we put its root directory on
``sys.path`` before importing ``TMSiSDK``.

Resolution order for the SDK root:

1. ``$TMSI_SDK_ROOT`` (explicit override, useful for exotic layouts),
2. every ancestor directory of this package + ``tmsi-python-interface-main``
   (the normal case: SDK vendored inside the project),
3. the current working directory + ``tmsi-python-interface-main``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "SDK_ENV_VAR",
    "SDK_DIRNAME",
    "SdkNotFoundError",
    "sdk_root",
    "ensure_sdk_importable",
    "dll_information",
]

SDK_ENV_VAR = "TMSI_SDK_ROOT"
SDK_DIRNAME = "tmsi-python-interface-main"

#: Marker file that proves a directory really is the SDK root.
_SDK_MARKER = Path("TMSiSDK") / "tmsi_sdk.py"


class SdkNotFoundError(RuntimeError):
    """Raised when the TMSi Python Interface cannot be located."""


def _candidates():
    """Yield candidate SDK roots, most specific first."""
    env_value = os.environ.get(SDK_ENV_VAR)
    if env_value:
        yield Path(env_value).expanduser()

    package_file = Path(__file__).resolve()
    for parent in package_file.parents:
        yield parent / SDK_DIRNAME

    yield Path.cwd() / SDK_DIRNAME


def sdk_root() -> Path:
    """Return the absolute path of the TMSi Python Interface root.

    :raises SdkNotFoundError: when no candidate directory contains
        ``TMSiSDK/tmsi_sdk.py``.
    """
    tried = []
    for candidate in _candidates():
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - unreadable path
            continue
        tried.append(str(resolved))
        if (resolved / _SDK_MARKER).is_file():
            return resolved

    raise SdkNotFoundError(
        "TMSi Python Interface not found. Looked in:\n  - "
        + "\n  - ".join(dict.fromkeys(tried))
        + f"\nSet {SDK_ENV_VAR} to the directory that contains the TMSiSDK folder."
    )


def ensure_sdk_importable() -> Path:
    """Prepend the SDK root to ``sys.path`` (idempotent) and return it."""
    root = sdk_root()
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return root


def dll_information() -> dict:
    """Report whether the SAGA driver DLL could be loaded.

    The SDK looks for ``TMSiSagaDeviceLib.dll`` in a fixed location
    (``C:\\Program Files\\TMSi\\Saga``), which is where the TMSi driver
    installer puts it.

    :return: dict with ``available``, ``path`` and ``locked`` keys.
    """
    ensure_sdk_importable()
    try:
        from TMSiSDK.device.devices.saga import saga_API
    except Exception as exc:  # pragma: no cover - broken SDK checkout
        return {"available": False, "path": None, "locked": None, "error": str(exc)}

    return {
        "available": bool(saga_API.SagaDllAvailable),
        "locked": bool(saga_API.SagaDllLocked),
        "path": getattr(saga_API, "so_name", None),
    }
