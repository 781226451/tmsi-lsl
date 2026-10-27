"""End-to-end GUI smoke test: the real QML window, offscreen, with the simulator."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TMSI_LSL_SKIP_GUI_TEST") == "1",
    reason="TMSI_LSL_SKIP_GUI_TEST=1",
)


def test_gui_self_test_passes(tmp_path):
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmsi_lsl.gui.app",
            "--self-test",
            "--settings",
            str(tmp_path / "settings.toml"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
    )

    assert completed.returncode == 0, (
        f"self-test failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert "self-test: ok" in completed.stdout
