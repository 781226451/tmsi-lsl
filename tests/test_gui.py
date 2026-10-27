"""Qt model / controller tests (offscreen, no hardware)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from tmsi_lsl.channels import ChannelInfo  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])
    yield app


def wait_until(predicate, timeout: float = 20.0) -> bool:
    """Pump the Qt event loop until ``predicate`` holds (worker thread signals)."""
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


def make_channels(count: int = 6) -> list[ChannelInfo]:
    channels = [ChannelInfo(0, "CREF", "UNI", "uV", 0, 4000.0, True)]
    for number in range(1, count + 1):
        channels.append(
            ChannelInfo(number, f"UNI {number:02d}", "UNI", "uV", 0, 4000.0, True)
        )
    channels.append(
        ChannelInfo(len(channels), "TRIGGER", "STATUS", "-", 0, 4000.0, True)
    )
    return channels


# ------------------------------------------------------------------ the model


def test_model_load_exposes_roles(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel

    model = ChannelListModel()
    channels = make_channels()
    model.load(channels, {1, 2})

    assert model.rowCount() == len(channels)
    index = model.index(1, 0)  # row 1 == "UNI 01", which is channel number 2
    assert model.data(index, ChannelListModel.NumberRole) == 2
    assert model.data(index, ChannelListModel.NameRole) == "UNI 01"
    assert model.data(index, ChannelListModel.SelectedRole) is True
    assert model.data(model.index(3, 0), ChannelListModel.SelectedRole) is False
    assert model.data(index, ChannelListModel.RateRole) == 4000.0


def test_mandatory_channels_cannot_be_deselected(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel

    model = ChannelListModel()
    model.load(make_channels(), set())
    trigger_index = len(make_channels()) - 1

    assert model.data(model.index(trigger_index, 0), ChannelListModel.MandatoryRole) is True
    assert model.isSelected(trigger_index) is True  # forced on by load()

    model.setSelected(trigger_index, False)
    assert model.isSelected(trigger_index) is True


def test_locked_model_refuses_selection_changes(qt_app):
    """While sampling, every selection entry point must be inert."""
    from tmsi_lsl.gui.models import ChannelListModel

    model = ChannelListModel()
    model.load(make_channels(4), {1, 2})
    before = model.selectedIndices()

    model.set_locked(True)
    assert model.locked is True

    model.setSelected(4, True)
    model.selectAll()
    model.clearSelection()
    model.invertSelection()
    model.selectReferencePlus(64)

    assert model.selectedIndices() == before

    model.set_locked(False)
    assert model.locked is False
    model.setSelected(4, True)
    assert 4 in model.selectedIndices()


def test_selection_operations(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel

    model = ChannelListModel()
    model.load(make_channels(4), set())  # CREF, UNI 01..04, TRIGGER -> 6 rows

    model.clearSelection()
    assert model.selectedIndices() == [5]  # only the mandatory TRIGGER stays

    model.selectAll()
    assert model.selectedCount == 6

    model.invertSelection()
    assert model.selectedIndices() == [5]

    model.selectType("UNI")
    assert model.selectedNames() == ["CREF", "UNI 01", "UNI 02", "UNI 03", "UNI 04", "TRIGGER"]

    model.clearType("UNI")
    assert model.selectedNames() == ["TRIGGER"]


def test_selected_summary_text(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel

    model = ChannelListModel()
    model.load(make_channels(3), {1, 2})
    assert "3 个通道" in model.selectedSummary()


def test_filter_model_matches_name_and_type(qt_app):
    from tmsi_lsl.gui.models import ChannelFilterModel, ChannelListModel

    model = ChannelListModel()
    model.load(make_channels(3), set())
    proxy = ChannelFilterModel()
    proxy.setSourceModel(model)

    assert proxy.rowCount() == 5

    proxy.filterText = "uni 0"
    assert proxy.rowCount() == 3

    proxy.filterText = ""
    proxy.typeFilter = "STATUS"
    assert proxy.rowCount() == 1

    proxy.typeFilter = ""
    assert proxy.rowCount() == 5


# --------------------------------------------------------------- the controller


def test_controller_saves_and_reloads_selection(qt_app, tmp_path):
    from tmsi_lsl.gui.controller import AppController

    path = tmp_path / "settings.toml"
    controller = AppController(path)
    try:
        controller.channel_model.load(make_channels(4), set())
        controller.channel_model.set_selected_indices([1, 2])
        controller.baseRate = 4096
        controller.divider = 2
        controller.lslName = "Lab A"
        controller.saveSettings()

        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert 'names = ["UNI 01", "UNI 02", "TRIGGER"]' in text
        assert "indices = [1, 2, 5]" in text
        assert "base_rate = 4096" in text
        assert "divider = 2" in text
        assert 'name = "Lab A"' in text

        # A fresh controller must restore everything, including the gate.
        second = AppController(path)
        try:
            assert second.baseRate == 4096
            assert second.divider == 2
            assert second.effectiveRate == 2048
            assert second.lslName == "Lab A"

            second.channel_model.load(make_channels(4), set())
            second.loadSettingsFrom(str(path))
            assert second.channel_model.selectedNames() == ["UNI 01", "UNI 02", "TRIGGER"]
        finally:
            second.shutdown()
    finally:
        controller.shutdown()


def test_controller_reports_and_survives_bad_settings(qt_app, tmp_path):
    from tmsi_lsl.gui.controller import AppController

    path = tmp_path / "broken.toml"
    path.write_text("[sampling]\nbase_rate = 1234\n", encoding="utf-8")

    controller = AppController(path)
    try:
        assert controller.statusKind == "error"
        assert "base_rate" in controller.statusText
        assert controller.baseRate == 4000  # fell back to defaults
    finally:
        controller.shutdown()


def test_controller_save_as_and_load_from(qt_app, tmp_path):
    from tmsi_lsl.gui.controller import AppController

    controller = AppController(tmp_path / "default.toml")
    try:
        target = tmp_path / "custom.toml"
        controller.lslName = "Custom"
        controller.saveSettingsAs(str(target))
        assert target.is_file()
        assert controller.settingsPath.endswith("custom.toml")

        controller.lslName = "Changed"
        controller.loadSettingsFrom(str(target))
        assert controller.lslName == "Custom"
    finally:
        controller.shutdown()


def test_controller_guards_streaming_without_device(qt_app, tmp_path):
    from tmsi_lsl.gui.controller import AppController

    controller = AppController(tmp_path / "settings.toml")
    try:
        assert controller.connected is False
        controller.startStream()  # must not raise
        assert controller.streaming is False
    finally:
        controller.shutdown()


def test_loading_settings_is_blocked_while_streaming(qt_app, tmp_path):
    from tmsi_lsl.gui.controller import AppController

    alternative = tmp_path / "alternative.toml"
    alternative.write_text(
        '[sampling]\nbase_rate = 4096\ndivider = 8\n[lsl]\nname = "Alt"\n',
        encoding="utf-8",
    )

    controller = AppController(tmp_path / "current.toml")
    try:
        controller.connectSimulator()
        assert wait_until(lambda: controller.connected)

        controller.divider = 2
        controller.startStream()
        assert wait_until(lambda: controller.streaming)

        controller.loadSettingsFrom(str(alternative))
        # Nothing may change while sampling.
        assert controller.baseRate == 4000
        assert controller.divider == 2
        assert controller.lslName == "TMSi SAGA"
        assert controller.statusKind == "warn"
        assert "推流中不能加载设置" in controller.statusText

        controller.stopStream()
        assert wait_until(lambda: not controller.streaming)

        controller.loadSettingsFrom(str(alternative))
        assert controller.baseRate == 4096
        assert controller.divider == 8
        assert controller.lslName == "Alt"
    finally:
        controller.shutdown()


def test_starting_a_stream_applies_the_selected_sample_rate(qt_app, tmp_path):
    """4000 Hz + divider 8 must give 500 Hz on the device, not stay at 2000."""
    from tmsi_lsl.gui.controller import AppController

    controller = AppController(tmp_path / "settings.toml")
    try:
        controller.connectSimulator()
        assert wait_until(lambda: controller.connected), "simulator did not connect"
        assert controller.deviceSamplingRate == 4000

        controller.baseRate = 4000
        controller.divider = 8
        assert controller.effectiveRate == 500
        # Still the device's old rate until the configuration is applied.
        assert controller.deviceSamplingRate == 4000

        controller.startStream()
        assert wait_until(lambda: controller.streaming), "stream did not start"

        assert controller.deviceSamplingRate == 500
        assert "设备采样率 500 Hz" in controller.deviceText

        controller.stopStream()
        assert wait_until(lambda: not controller.streaming)
    finally:
        controller.shutdown()


def test_restart_stream_applies_a_new_rate_without_reconnecting(qt_app, tmp_path):
    """Changing the rate always costs a short break: stop, reconfigure, start."""
    from tmsi_lsl.gui.controller import AppController

    controller = AppController(tmp_path / "settings.toml")
    try:
        controller.connectSimulator()
        assert wait_until(lambda: controller.connected)

        controller.divider = 2
        controller.startStream()
        assert wait_until(lambda: controller.streaming)
        assert controller.deviceSamplingRate == 2000

        controller.divider = 8
        assert controller.deviceSamplingRate == 2000  # unchanged until applied

        controller.restartStream()
        assert wait_until(
            lambda: controller.streaming and controller.deviceSamplingRate == 500
        ), f"restart did not apply 500 Hz ({controller.statusText})"

        # Still connected, still the same controller/device object.
        assert controller.connected is True
        assert "设备采样率 500 Hz" in controller.deviceText

        controller.stopStream()
        assert wait_until(lambda: not controller.streaming)
    finally:
        controller.shutdown()


def test_restart_requires_an_active_stream(qt_app, tmp_path):
    from tmsi_lsl.gui.controller import AppController

    controller = AppController(tmp_path / "settings.toml")
    try:
        controller.connectSimulator()
        assert wait_until(lambda: controller.connected)
        controller.restartStream()  # not streaming -> must be a no-op
        assert controller.streaming is False
    finally:
        controller.shutdown()


# ------------------------------------------------------------- matrix view


def make_grid_channels(rows: int = 8, cols: int = 8) -> list[ChannelInfo]:
    """Channels named like the lab's montage: CREF + R1C8..R8C8 in device order."""
    channels = [ChannelInfo(0, "CREF", "UNI", "uV", 0, 4000.0, True)]
    index = 1
    # Deliberately column-major/odd order: the matrix must position by name.
    for col in range(cols, 0, -1):
        for row in range(1, rows + 1):
            channels.append(
                ChannelInfo(index, f"R{row}C{col}", "UNI", "uV", 0, 4000.0, True)
            )
            index += 1
    return channels


def test_matrix_model_positions_cells_by_name(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    model.load(make_grid_channels(), set())
    matrix = ChannelMatrixModel(model)

    assert matrix.rows == 8
    assert matrix.columns == 8
    assert matrix.rowCount() == 64

    first = matrix.index(0, 0)  # top-left must be R1C1
    assert matrix.data(first, ChannelMatrixModel.LabelRole) == "R1C1"
    assert matrix.data(first, ChannelMatrixModel.RowRole) == 1
    assert matrix.data(first, ChannelMatrixModel.ColRole) == 1

    eighth = matrix.index(7, 0)
    assert matrix.data(eighth, ChannelMatrixModel.LabelRole) == "R1C8"
    assert matrix.data(eighth, ChannelMatrixModel.SelectedRole) is False


def test_matrix_model_toggles_the_underlying_selection(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    channels = make_grid_channels()
    model.load(channels, set())
    matrix = ChannelMatrixModel(model)

    r3c5 = next(ch for ch in channels if ch.name == "R3C5")

    matrix.toggle(3, 5)
    assert model.isSelected(r3c5.index) is True
    assert matrix.data(matrix.index((3 - 1) * 8 + (5 - 1), 0), ChannelMatrixModel.SelectedRole) is True

    matrix.toggle(3, 5)
    assert model.isSelected(r3c5.index) is False


def test_matrix_model_follows_bulk_selection(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    model.load(make_grid_channels(), set())
    matrix = ChannelMatrixModel(model)

    model.selectAll()
    selected = [
        matrix.data(matrix.index(row, 0), ChannelMatrixModel.SelectedRole)
        for row in range(matrix.rowCount())
    ]
    assert all(selected)

    model.clearSelection()
    selected = [
        matrix.data(matrix.index(row, 0), ChannelMatrixModel.SelectedRole)
        for row in range(matrix.rowCount())
    ]
    assert not any(selected)


def test_matrix_model_marks_missing_positions(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    channels = [ch for ch in make_grid_channels() if ch.name != "R2C2"]
    model = ChannelListModel()
    model.load(channels, set())
    matrix = ChannelMatrixModel(model)

    assert matrix.rowCount() == 64  # the hole is still laid out
    hole = matrix.index((2 - 1) * 8 + (2 - 1), 0)
    assert matrix.data(hole, ChannelMatrixModel.PresentRole) is False
    assert matrix.data(hole, ChannelMatrixModel.ChannelIndexRole) == -1
    assert matrix.isPresent(2, 2) is False
    assert matrix.isPresent(1, 1) is True


def test_matrix_model_is_empty_without_grid_names(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    model.load(make_channels(4), set())  # UNI 01.. style names
    matrix = ChannelMatrixModel(model)
    assert matrix.rows == 0
    assert matrix.columns == 0
    assert matrix.rowCount() == 0


def test_region_preview_marks_the_rectangle(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    model.load(make_grid_channels(), set())
    matrix = ChannelMatrixModel(model)

    matrix.beginRegion(2, 3)
    matrix.extendRegion(4, 5)
    assert matrix.hasRegion() is True
    assert matrix.regionSize() == 9  # 3 rows x 3 columns

    def in_region(row: int, col: int) -> bool:
        index = matrix.index((row - 1) * 8 + (col - 1), 0)
        return matrix.data(index, ChannelMatrixModel.InRegionRole)

    assert in_region(2, 3) is True
    assert in_region(4, 5) is True
    assert in_region(1, 1) is False
    assert in_region(5, 5) is False

    matrix.clearRegion()
    assert matrix.hasRegion() is False
    assert in_region(2, 3) is False


def test_region_is_normalised_for_backwards_drags(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    model.load(make_grid_channels(), set())
    matrix = ChannelMatrixModel(model)

    matrix.beginRegion(5, 5)
    matrix.extendRegion(3, 2)
    assert matrix.regionSize() == 3 * 4

    matrix.commitRegion(False)
    selected = set(model.selectedNames())
    assert "R3C2" in selected
    assert "R5C5" in selected
    assert len(selected) == 12


def test_region_commit_replaces_only_within_the_grid(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    channels = make_grid_channels()
    model.load(channels, set())
    matrix = ChannelMatrixModel(model)

    cref = next(ch for ch in channels if ch.name == "CREF")
    model.setSelected(cref.index, True)  # outside the grid, must survive
    model.selectAll()  # everything, including the whole grid

    matrix.beginRegion(1, 1)
    matrix.commitRegion(False)  # replace with a single cell

    assert set(model.selectedNames()) == {"CREF", "R1C1"}


def test_region_commit_can_add_to_the_selection(qt_app):
    from tmsi_lsl.gui.models import ChannelListModel, ChannelMatrixModel

    model = ChannelListModel()
    channels = make_grid_channels()
    model.load(channels, {next(ch.index for ch in channels if ch.name == "R1C1")})
    matrix = ChannelMatrixModel(model)

    matrix.beginRegion(8, 8)
    matrix.commitRegion(True)  # Ctrl-drag: union

    assert set(model.selectedNames()) == {"R1C1", "R8C8"}
    assert matrix.hasRegion() is False  # the band disappears on commit
