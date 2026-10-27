"""Qt Quick application entry point.

    uv run tmsi-lsl-gui                 # use ./settings.toml
    uv run tmsi-lsl-gui --settings lab.toml
    uv run tmsi-lsl-gui --self-test     # headless smoke test (offscreen Qt)

The window is defined in ``qml/Main.qml``; the Python side provides three
context objects: ``app`` (controller), ``channelModel`` and ``channelFilter``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

__all__ = ["main", "build_parser"]

QML_DIR = Path(__file__).resolve().parent / "qml"
MAIN_QML = QML_DIR / "Main.qml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmsi-lsl-gui",
        description=(
            "Qt Quick front-end: gate SAGA channels, pick the sample rate, "
            "save/load the configuration as TOML and stream to LSL."
        ),
    )
    parser.add_argument(
        "--settings",
        metavar="PATH",
        default=None,
        help="settings file to load at start and save to (default: ./settings.toml)",
    )
    parser.add_argument(
        "--style",
        default=os.environ.get("TMSI_LSL_QML_STYLE", "Basic"),
        help="Qt Quick Controls style (default: Basic)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="load the QML offscreen, run a simulated stream, then exit",
    )
    return parser


def _wait_for(app, predicate, timeout: float) -> bool:
    """Pump the Qt event loop until ``predicate`` is true or ``timeout`` elapses."""
    from PySide6.QtCore import QEventLoop  # noqa: PLC0415

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _destroy_window(engine) -> None:
    """Destroy the QML tree *before* the Python context objects go away.

    Otherwise the last binding evaluation happens when ``app`` is already a
    dangling context property, and Qt logs a wall of "Cannot read property of
    null" warnings on exit.
    """
    from PySide6.QtCore import QCoreApplication, QEvent  # noqa: PLC0415

    for root_object in engine.rootObjects():
        root_object.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def _run_self_test(app, engine, controller) -> int:
    """Exercise the whole wiring without a display or a real device."""
    problems: list[str] = []

    controller.connectSimulator()
    if not _wait_for(app, lambda: controller.connected, 20.0):
        problems.append(f"simulated device did not connect ({controller.statusText})")

    frames = 0
    if not problems:
        controller.startStream()
        if not _wait_for(app, lambda: controller.streaming, 20.0):
            problems.append(f"stream did not start ({controller.statusText})")
        elif not _wait_for(app, lambda: controller.framesReceived > 0, 20.0):
            problems.append("no samples reached the LSL outlet")
        else:
            frames = controller.framesReceived

        controller.stopStream()
        _wait_for(app, lambda: not controller.streaming, 15.0)
        controller.disconnectDevice()
        _wait_for(app, lambda: not controller.connected, 15.0)

    _destroy_window(engine)
    controller.shutdown()

    if problems:
        for problem in problems:
            print(f"self-test: {problem}", file=sys.stderr)
        return 1

    print(
        "self-test: ok "
        f"(channels={controller.channel_model.count}, "
        f"selected={controller.selectedCount}, frames={frames})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # Imports stay local so `--help` works on a machine without the GUI stack.
    from PySide6.QtCore import QUrl  # noqa: PLC0415
    from PySide6.QtGui import QGuiApplication  # noqa: PLC0415
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: PLC0415
    from PySide6.QtQuickControls2 import QQuickStyle  # noqa: PLC0415

    from .controller import AppController  # noqa: PLC0415

    QQuickStyle.setStyle(args.style)

    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("tmsi-lsl")
    app.setApplicationDisplayName("TMSi SAGA → LSL")
    app.setOrganizationName("tmsi-lsl")

    controller = AppController(args.settings)

    engine = QQmlApplicationEngine()
    qml_warnings: list[str] = []
    engine.warnings.connect(
        lambda errors: qml_warnings.extend(str(error.toString()) for error in errors)
    )

    context = engine.rootContext()
    context.setContextProperty("app", controller)
    context.setContextProperty("channelModel", controller.channel_model)
    context.setContextProperty("channelFilter", controller.channel_filter)
    context.setContextProperty("channelMatrix", controller.channel_matrix)

    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    if not engine.rootObjects():
        for warning in qml_warnings:
            print(warning, file=sys.stderr)
        controller.shutdown()
        return 1

    app.aboutToQuit.connect(lambda: (_destroy_window(engine), controller.shutdown()))

    exit_code = _run_self_test(app, engine, controller) if args.self_test else app.exec()

    if qml_warnings:
        for warning in qml_warnings:
            print(f"QML: {warning}", file=sys.stderr)
        if exit_code == 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
