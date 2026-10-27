"""Render the Qt Quick UI to a PNG without opening a real window.

Useful for documentation and for eyeballing layout changes on a headless
machine:

    uv run python scripts/gui_screenshot.py --out docs/gui.png
    uv run python scripts/gui_screenshot.py --simulate --out docs/gui-streaming.png
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
# The offscreen platform has no bundled fonts; point it at the system ones so
# the capture is not rendered as empty boxes.
_system_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
if _system_fonts.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_system_fonts))

from PySide6.QtCore import QEventLoop, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

from tmsi_lsl.gui.app import MAIN_QML  # noqa: E402
from tmsi_lsl.gui.controller import AppController  # noqa: E402


def pump(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def wait_for(app, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/gui.png", help="destination PNG")
    parser.add_argument("--settings", default=None, help="settings file to load")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="connect the simulated device and start streaming before capture",
    )
    parser.add_argument(
        "--device",
        action="store_true",
        help="connect the real SAGA device (channels are only read unless --stream is given)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="start streaming before capture (with --device this writes the device config)",
    )
    parser.add_argument(
        "--view",
        choices=("list", "matrix"),
        default="list",
        help="which channel view to capture",
    )
    parser.add_argument(
        "--demo-grid",
        action="store_true",
        help="load synthetic R#C# channels (preview the matrix view without a device)",
    )
    parser.add_argument(
        "--region",
        metavar="R1,C1,R2,C2",
        help="preview a rubber-band selection over the matrix, e.g. 2,3,4,5",
    )
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=860)
    args = parser.parse_args()

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    app.setApplicationName("tmsi-lsl")

    controller = AppController(args.settings)
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda errors: [print(f"QML: {error.toString()}", file=sys.stderr) for error in errors]
    )
    context = engine.rootContext()
    context.setContextProperty("app", controller)
    context.setContextProperty("channelModel", controller.channel_model)
    context.setContextProperty("channelFilter", controller.channel_filter)
    context.setContextProperty("channelMatrix", controller.channel_matrix)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    if not engine.rootObjects():
        controller.shutdown()
        return 1

    window = engine.rootObjects()[0]
    window.resize(args.width, args.height)
    if args.view != "list":
        window.setProperty("channelView", args.view)

    if args.demo_grid:
        from tmsi_lsl.channels import ChannelInfo

        grid = [ChannelInfo(0, "CREF", "UNI", "uV", 0, 2000.0, True)]
        index = 1
        for row in range(1, 9):
            for col in range(1, 9):
                grid.append(ChannelInfo(index, f"R{row}C{col}", "UNI", "uV", 0, 2000.0, True))
                index += 1
        grid.append(ChannelInfo(index, "STATUS", "STATUS", "-", 0, 2000.0, True))
        controller.channel_model.load(grid, {channel.index for channel in grid})

    if args.region:
        try:
            row1, col1, row2, col2 = (int(part) for part in args.region.split(","))
        except ValueError:
            print("--region expects four integers, e.g. 2,3,4,5", file=sys.stderr)
            controller.shutdown()
            return 2
        controller.channel_matrix.beginRegion(row1, col1)
        controller.channel_matrix.extendRegion(row2, col2)
        pump(app, 0.2)

    pump(app, 0.5)

    if args.simulate:
        controller.connectSimulator()
        if not wait_for(app, lambda: controller.connected, 20.0):
            print(f"connect failed: {controller.statusText}", file=sys.stderr)
            controller.shutdown()
            return 1
        # Gate in a plausible subset so the screenshot shows both states.
        model = controller.channel_model
        keep = {channel.index for channel in model.channels[:9]}
        model.set_selected_indices(keep)
        pump(app, 0.3)
        controller.startStream()
        wait_for(app, lambda: controller.streaming, 20.0)
        pump(app, 2.2)  # let the throughput counter tick
    elif args.device:
        controller.connectHardware()
        if not wait_for(app, lambda: controller.connected, 60.0):
            print(f"connect failed: {controller.statusText}", file=sys.stderr)
            controller.shutdown()
            return 1
        if args.stream:
            controller.startStream()
            if not wait_for(app, lambda: controller.streaming, 60.0):
                print(f"stream failed: {controller.statusText}", file=sys.stderr)
                controller.shutdown()
                return 1
            pump(app, 2.2)
        else:
            pump(app, 0.6)

    image = window.grabWindow()
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination)):
        print(f"could not write {destination}", file=sys.stderr)
        controller.shutdown()
        return 1

    print(f"saved {destination} ({image.width()}x{image.height()})")
    if args.stream:
        print(
            f"stream: {controller.channel_model.selectedCount} channels, "
            f"{controller.framesReceived} frames, status={controller.statusText!r}"
        )
    controller.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
