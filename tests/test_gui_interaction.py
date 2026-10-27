"""Drive the real QML matrix with synthetic mouse events (offscreen).

This guards the rubber-band interaction itself rather than only the Python
model: press, drag across cells, release, and check what got gated in.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from tmsi_lsl.channels import ChannelInfo  # noqa: E402
from tmsi_lsl.gui.app import MAIN_QML  # noqa: E402

PITCH = 64  # cellSize 58 + gap 6, see Main.qml


def _grid_channels() -> list[ChannelInfo]:
    channels = [ChannelInfo(0, "CREF", "UNI", "uV", 0, 2000.0, True)]
    index = 1
    for row in range(1, 9):
        for col in range(1, 9):
            channels.append(ChannelInfo(index, f"R{row}C{col}", "UNI", "uV", 0, 2000.0, True))
            index += 1
    return channels


@pytest.fixture()
def gui(tmp_path):
    """A loaded QML window plus the controller behind it."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from tmsi_lsl.gui.controller import AppController

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication.instance() or QGuiApplication([])

    controller = AppController(tmp_path / "settings.toml")
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("app", controller)
    context.setContextProperty("channelModel", controller.channel_model)
    context.setContextProperty("channelFilter", controller.channel_filter)
    context.setContextProperty("channelMatrix", controller.channel_matrix)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    if not engine.rootObjects():
        controller.shutdown()
        pytest.fail("Main.qml failed to load")

    window = engine.rootObjects()[0]
    window.resize(1200, 860)

    controller.channel_model.load(_grid_channels(), set())
    app.processEvents()

    try:
        yield app, window, controller
    finally:
        controller.shutdown()
        window.deleteLater()
        app.processEvents()


def _cell_center(window, row: int, col: int):
    """Window coordinates (QPoint) of the centre of a grid cell."""
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtQuick import QQuickItem

    grid = window.findChild(QQuickItem, "matrixGrid")
    assert grid is not None, "matrixGrid item not found"
    # Centre of the cell: half a cell in, then row/column offsets by the pitch.
    scene = grid.mapToScene(QPointF(29 + (col - 1) * PITCH, 29 + (row - 1) * PITCH))
    return scene.toPoint(), grid


def _wait_until(predicate, timeout: float = 20.0) -> bool:
    import time

    from PySide6.QtCore import QEventLoop
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_sampling_controls_lock_only_while_busy_or_streaming(gui):
    """Editable while the device is open but idle; locked while sampling."""
    from PySide6.QtCore import QObject

    app, window, controller = gui
    base = window.findChild(QObject, "baseRateCombo")
    divider = window.findChild(QObject, "dividerCombo")
    assert base is not None and divider is not None

    app.processEvents()
    assert base.property("enabled") is True
    assert divider.property("enabled") is True

    controller.connectSimulator()
    # `busy` flips synchronously when the connection starts: locked right away.
    assert controller.busy is True
    assert controller.connected is False
    assert base.property("enabled") is False, "rate must be locked while connecting"
    assert divider.property("enabled") is False, "divider must be locked while connecting"

    assert _wait_until(lambda: controller.connected), "simulator did not connect"
    app.processEvents()
    # Open but idle == the firmware's Device_Open state: configuration is allowed.
    assert base.property("enabled") is True
    assert divider.property("enabled") is True

    controller.divider = 2
    controller.startStream()
    assert _wait_until(lambda: controller.streaming), "stream did not start"
    app.processEvents()
    # Sampling: the firmware refuses config writes, so the controls are locked.
    assert base.property("enabled") is False
    assert divider.property("enabled") is False

    controller.stopStream()
    assert _wait_until(lambda: not controller.streaming), "stop failed"
    app.processEvents()
    assert base.property("enabled") is True
    assert divider.property("enabled") is True

    controller.disconnectDevice()
    assert _wait_until(lambda: not controller.connected), "disconnect failed"
    app.processEvents()
    assert base.property("enabled") is True
    assert divider.property("enabled") is True


def test_channel_selection_locked_while_streaming(gui):
    """Gating must be read-only during sampling: buttons and grid both inert."""
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtTest import QTest

    app, window, controller = gui
    model = controller.channel_model

    controller.divider = 2
    controller.connectSimulator()
    assert _wait_until(lambda: controller.connected), "simulator did not connect"
    controller.startStream()
    assert _wait_until(lambda: controller.streaming), "stream did not start"
    app.processEvents()

    assert model.locked is True
    for name in (
        "selectAllButton",
        "clearSelectionButton",
        "invertSelectionButton",
        "ref64Button",
        "loadButton",
    ):
        button = window.findChild(QObject, name)
        assert button is not None, f"{name} not found"
        assert button.property("enabled") is False, f"{name} must be disabled while sampling"

    before = set(model.selectedNames())
    point, _grid = _cell_center(window, 1, 1)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    app.processEvents()
    assert set(model.selectedNames()) == before, "clicks must not change gating while sampling"

    controller.stopStream()
    assert _wait_until(lambda: not controller.streaming), "stop failed"
    app.processEvents()
    assert model.locked is False
    assert window.findChild(QObject, "selectAllButton").property("enabled") is True
    assert window.findChild(QObject, "loadButton").property("enabled") is True


def test_drag_selects_a_rectangle(gui):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    app, window, controller = gui
    start, _grid = _cell_center(window, 1, 1)
    end, _ = _cell_center(window, 2, 2)
    middle = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)

    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, middle)
    QTest.mouseMove(window, end)
    app.processEvents()
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    app.processEvents()

    assert set(controller.channel_model.selectedNames()) == {
        "R1C1",
        "R1C2",
        "R2C1",
        "R2C2",
    }
    assert controller.channel_matrix.hasRegion() is False


def test_click_toggles_a_single_cell(gui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, window, controller = gui
    point, _grid = _cell_center(window, 3, 4)

    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    app.processEvents()
    assert controller.channel_model.selectedNames() == ["R3C4"]

    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    app.processEvents()
    assert controller.channel_model.selectedNames() == []


def test_ctrl_drag_adds_to_the_selection(gui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, window, controller = gui
    first, _grid = _cell_center(window, 1, 1)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, first)
    app.processEvents()
    assert controller.channel_model.selectedNames() == ["R1C1"]

    start, _ = _cell_center(window, 3, 3)
    end, _ = _cell_center(window, 4, 4)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, start)
    QTest.mouseMove(window, end)
    app.processEvents()
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, end)
    app.processEvents()

    assert set(controller.channel_model.selectedNames()) == {
        "R1C1",
        "R3C3",
        "R3C4",
        "R4C3",
        "R4C4",
    }
