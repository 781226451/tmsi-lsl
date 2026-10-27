"""tmsi-lsl: stream TMSi SAGA data to Lab Streaming Layer (LSL).

The package is a thin application layer on top of the `TMSi Python Interface`
(the vendored ``tmsi-python-interface-main`` tree).  It does not import the SDK
at package-import time, so ``import tmsi_lsl`` works on machines without the
TMSi driver installed.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
