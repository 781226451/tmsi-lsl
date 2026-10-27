"""Qt models backing the channel table.

``ChannelListModel`` holds one row per device channel and owns the gate state
("选通"); ``ChannelFilterModel`` provides the search / type filter used by the
QML view without disturbing the underlying order or selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    Property,
    QSortFilterProxyModel,
    Qt,
    Signal,
    Slot,
)

from ..channels import ChannelInfo, select_reference_and_data

__all__ = [
    "ChannelListModel",
    "ChannelFilterModel",
    "ChannelMatrixModel",
    "MANDATORY_TYPES",
]

#: Firmware refuses to disable these, so the UI must not pretend otherwise.
MANDATORY_TYPES = frozenset({"STATUS", "COUNTER"})

#: Electrode-grid labels such as ``R3C7`` (row 3, column 7).
_MATRIX_LABEL = re.compile(r"^R(\d+)\s*C(\d+)$", re.IGNORECASE)


@dataclass
class ChannelRow:
    channel: ChannelInfo
    selected: bool

    @property
    def mandatory(self) -> bool:
        """True when the firmware would refuse to turn this channel off.

        Status/counter channels cannot be disabled, but only while the device
        actually has them enabled; an already-disabled counter is just a normal
        opt-in channel.
        """
        return self.channel.channel_type in MANDATORY_TYPES and self.channel.is_active


class ChannelListModel(QAbstractListModel):
    """Rows of channels with a per-row ``selected`` flag."""

    NumberRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    TypeRole = Qt.ItemDataRole.UserRole + 3
    UnitRole = Qt.ItemDataRole.UserRole + 4
    RateRole = Qt.ItemDataRole.UserRole + 5
    ActiveRole = Qt.ItemDataRole.UserRole + 6
    MandatoryRole = Qt.ItemDataRole.UserRole + 7
    SelectedRole = Qt.ItemDataRole.UserRole + 8
    ChannelIndexRole = Qt.ItemDataRole.UserRole + 9

    countChanged = Signal()
    selectionChanged = Signal()
    typesChanged = Signal()
    lockedChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[ChannelRow] = []
        self._channels: list[ChannelInfo] = []
        self._row_of_channel: dict[int, int] = {}
        self._types: list[str] = []
        self._locked = False

    # ------------------------------------------------------------------ roles

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 - Qt API
        return {
            self.NumberRole: QByteArray(b"number"),
            self.NameRole: QByteArray(b"name"),
            self.TypeRole: QByteArray(b"type"),
            self.UnitRole: QByteArray(b"unit"),
            self.RateRole: QByteArray(b"rate"),
            self.ActiveRole: QByteArray(b"active"),
            self.MandatoryRole: QByteArray(b"mandatory"),
            self.SelectedRole: QByteArray(b"selected"),
            self.ChannelIndexRole: QByteArray(b"channelIndex"),
        }

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        channel = row.channel
        if role == Qt.ItemDataRole.DisplayRole or role == self.NameRole:
            return channel.name
        if role == self.NumberRole:
            return channel.number
        if role == self.TypeRole:
            return channel.channel_type
        if role == self.UnitRole:
            return channel.unit
        if role == self.RateRole:
            return None if channel.sample_rate is None else float(channel.sample_rate)
        if role == self.ActiveRole:
            return bool(channel.is_active)
        if role == self.MandatoryRole:
            return row.mandatory
        if role == self.SelectedRole:
            return row.selected
        if role == self.ChannelIndexRole:
            return channel.index
        return None

    # ------------------------------------------------------------- properties

    def _get_count(self) -> int:
        return len(self._rows)

    count = Property(int, _get_count, notify=countChanged)

    def _get_selected_count(self) -> int:
        return sum(1 for row in self._rows if row.selected)

    selectedCount = Property(int, _get_selected_count, notify=selectionChanged)

    def _get_types(self) -> list[str]:
        return list(self._types)

    types = Property("QStringList", _get_types, notify=typesChanged)

    def _get_locked(self) -> bool:
        return self._locked

    locked = Property(bool, _get_locked, notify=lockedChanged)

    @Slot(bool)
    def set_locked(self, locked: bool) -> None:  # noqa: N802 - QML API
        """Freeze the selection (used while the device is sampling)."""
        locked = bool(locked)
        if locked == self._locked:
            return
        self._locked = locked
        self.lockedChanged.emit()

    # ------------------------------------------------------------------- load

    @property
    def channels(self) -> list[ChannelInfo]:
        """The loaded channels (plain Python, used by the controller)."""
        return list(self._channels)

    def load(self, channels: Sequence[ChannelInfo], selected: Iterable[int] = ()) -> None:
        """Replace the contents with ``channels``, pre-selecting ``selected``."""
        self.beginResetModel()
        chosen = {int(index) for index in selected}
        self._channels = list(channels)
        self._rows = [
            ChannelRow(channel=channel, selected=channel.index in chosen)
            for channel in self._channels
        ]
        self._row_of_channel = {row.channel.index: position for position, row in enumerate(self._rows)}
        # Mandatory channels cannot be disabled: keep them selected.
        for row in self._rows:
            if row.mandatory:
                row.selected = True
        self._types = sorted({channel.channel_type for channel in self._channels})
        self.endResetModel()

        self.countChanged.emit()
        self.typesChanged.emit()
        self.selectionChanged.emit()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._channels = []
        self._row_of_channel = {}
        self._types = []
        self.endResetModel()
        self.countChanged.emit()
        self.typesChanged.emit()
        self.selectionChanged.emit()

    # -------------------------------------------------------------- selection

    def _set_row_selected(self, position: int, selected: bool) -> None:
        row = self._rows[position]
        if row.selected == selected or (row.mandatory and not selected):
            return
        self._rows[position] = replace(row, selected=selected)
        index = self.index(position, 0)
        self.dataChanged.emit(index, index, [self.SelectedRole])

    @Slot(int, bool)
    def setSelected(self, channel_index: int, selected: bool) -> None:  # noqa: N802 - QML API
        """Select/unselect one channel by its device index."""
        if self._locked:
            return
        position = self._row_of_channel.get(int(channel_index))
        if position is None:
            return
        before = self._get_selected_count()
        self._set_row_selected(position, bool(selected))
        if self._get_selected_count() != before:
            self.selectionChanged.emit()

    @Slot(int, result=bool)
    def isSelected(self, channel_index: int) -> bool:  # noqa: N802 - QML API
        position = self._row_of_channel.get(int(channel_index))
        return bool(position is not None and self._rows[position].selected)

    def set_selected_indices(self, indices: Iterable[int]) -> None:
        """Replace the whole selection (used when loading settings)."""
        wanted = {int(index) for index in indices}
        changed = False
        for position, row in enumerate(self._rows):
            target = row.mandatory or row.channel.index in wanted
            if row.selected != target:
                self._rows[position] = replace(row, selected=target)
                changed = True
        if changed:
            first, last = 0, max(len(self._rows) - 1, 0)
            if self._rows:
                self.dataChanged.emit(self.index(first, 0), self.index(last, 0), [self.SelectedRole])
            self.selectionChanged.emit()

    @Slot()
    def selectAll(self) -> None:  # noqa: N802 - QML API
        if self._locked:
            return
        self.set_selected_indices(row.channel.index for row in self._rows)

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802 - QML API
        if self._locked:
            return
        self.set_selected_indices(())

    @Slot()
    def invertSelection(self) -> None:  # noqa: N802 - QML API
        if self._locked:
            return
        self.set_selected_indices(
            row.channel.index for row in self._rows if not row.selected
        )

    @Slot(str)
    def selectType(self, channel_type: str) -> None:  # noqa: N802 - QML API
        """Select every channel of ``channel_type`` in addition to the current set."""
        if self._locked:
            return
        wanted = [row.channel.index for row in self._rows if row.selected]
        wanted.extend(
            row.channel.index
            for row in self._rows
            if row.channel.channel_type == channel_type.upper()
        )
        self.set_selected_indices(wanted)

    @Slot(int)
    def selectReferencePlus(self, data_channels: int) -> None:  # noqa: N802 - QML API
        """Gate in the reference channel plus the first ``data_channels`` UNI channels."""
        if self._locked:
            return
        indices, _warnings = select_reference_and_data(self._channels, int(data_channels))
        self.set_selected_indices(indices)

    @Slot(str)
    def clearType(self, channel_type: str) -> None:  # noqa: N802 - QML API
        if self._locked:
            return
        self.set_selected_indices(
            row.channel.index
            for row in self._rows
            if row.selected and row.channel.channel_type != channel_type.upper()
        )

    @Slot(result="QVariantList")
    def selectedIndices(self) -> list[int]:  # noqa: N802 - QML API
        return [row.channel.index for row in self._rows if row.selected]

    @Slot(result="QVariantList")
    def selectedNames(self) -> list[str]:  # noqa: N802 - QML API
        return [row.channel.name for row in self._rows if row.selected]

    @Slot(result=str)
    def selectedSummary(self) -> str:  # noqa: N802 - QML API
        selected = [row for row in self._rows if row.selected]
        if not selected:
            return "未选通任何通道"
        if len(selected) <= 3:
            names = "、".join(row.channel.name for row in selected)
        else:
            names = f"{selected[0].channel.name} … {selected[-1].channel.name}"

        forced = [row.channel.name for row in selected if row.mandatory]
        summary = f"已选通 {len(selected)} 个通道（{names}）"
        if forced:
            summary += f"，其中 {len(forced)} 个固件固定不可关闭：{'、'.join(forced)}"
        return summary


class ChannelFilterModel(QSortFilterProxyModel):
    """Case-insensitive name search plus a channel-type filter."""

    filterTextChanged = Signal()
    typeFilterChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDynamicSortFilter(True)
        self._text = ""
        self._type = ""

    # --------------------------------------------------------------- filtering

    def _accepts(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return False
        if self._type:
            channel_type = model.data(model.index(source_row, 0), ChannelListModel.TypeRole)
            if str(channel_type).upper() != self._type.upper():
                return False
        if self._text:
            name = model.data(model.index(source_row, 0), ChannelListModel.NameRole)
            if self._text not in str(name).casefold():
                return False
        return True

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802 - Qt API
        return self._accepts(source_row, source_parent)

    def _invalidate(self) -> None:
        """Re-run the filter, using the non-deprecated Qt 6.9+ API when present."""
        begin = getattr(self, "beginFilterChange", None)
        end = getattr(self, "endFilterChange", None)
        if begin is not None and end is not None:
            begin()
            end(QSortFilterProxyModel.Direction.Rows)
            return
        invalidate_rows = getattr(self, "invalidateRowsFilter", None)
        if invalidate_rows is not None:  # pragma: no cover - older Qt
            invalidate_rows()
        else:  # pragma: no cover - older Qt
            self.invalidateFilter()

    # -------------------------------------------------------------- properties

    def _get_filter_text(self) -> str:
        return self._text

    def _set_filter_text(self, value: str) -> None:
        value = (value or "").casefold()
        if value == self._text:
            return
        self._text = value
        self._invalidate()
        self.filterTextChanged.emit()

    filterText = Property(str, _get_filter_text, _set_filter_text, notify=filterTextChanged)

    def _get_type_filter(self) -> str:
        return self._type

    def _set_type_filter(self, value: str) -> None:
        value = (value or "").upper()
        if value == self._type:
            return
        self._type = value
        self._invalidate()
        self.typeFilterChanged.emit()

    typeFilter = Property(str, _get_type_filter, _set_type_filter, notify=typeFilterChanged)


@dataclass
class MatrixCell:
    """One tile of the electrode grid."""

    row: int  # 1-based, matches the "R" in the channel name
    col: int  # 1-based, matches the "C"
    label: str
    channel_index: int  # -1 when the device has no channel at this position
    selected: bool


class ChannelMatrixModel(QAbstractListModel):
    """Electrode-grid view of channels named ``R<row>C<col>``.

    The model is read-only with respect to the device: it mirrors the selection
    held by :class:`ChannelListModel` (the single source of truth) and forwards
    clicks back to it.
    """

    RowRole = Qt.ItemDataRole.UserRole + 1
    ColRole = Qt.ItemDataRole.UserRole + 2
    LabelRole = Qt.ItemDataRole.UserRole + 3
    ChannelIndexRole = Qt.ItemDataRole.UserRole + 4
    SelectedRole = Qt.ItemDataRole.UserRole + 5
    PresentRole = Qt.ItemDataRole.UserRole + 6
    InRegionRole = Qt.ItemDataRole.UserRole + 7

    rowsChanged = Signal()
    columnsChanged = Signal()

    def __init__(self, source: "ChannelListModel", parent=None) -> None:
        super().__init__(parent)
        self._source = source
        self._cells: list[MatrixCell] = []
        self._rows = 0
        self._columns = 0
        self._region: tuple[int, int, int, int] | None = None

        source.modelReset.connect(self.rebuild)
        source.selectionChanged.connect(self.refresh_selection)
        self.rebuild()

    # ------------------------------------------------------------------ roles

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 - Qt API
        return {
            self.RowRole: QByteArray(b"row"),
            self.ColRole: QByteArray(b"col"),
            self.LabelRole: QByteArray(b"label"),
            self.ChannelIndexRole: QByteArray(b"channelIndex"),
            self.SelectedRole: QByteArray(b"selected"),
            self.PresentRole: QByteArray(b"present"),
            self.InRegionRole: QByteArray(b"inRegion"),
        }

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        if parent.isValid():
            return 0
        return len(self._cells)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._cells):
            return None
        cell = self._cells[index.row()]
        if role == Qt.ItemDataRole.DisplayRole or role == self.LabelRole:
            return cell.label
        if role == self.RowRole:
            return cell.row
        if role == self.ColRole:
            return cell.col
        if role == self.ChannelIndexRole:
            return cell.channel_index
        if role == self.SelectedRole:
            return cell.selected
        if role == self.PresentRole:
            return cell.channel_index >= 0
        if role == self.InRegionRole:
            return self._in_region(cell)
        return None

    # ------------------------------------------------------------- properties

    def _get_rows(self) -> int:
        return self._rows

    rows = Property(int, _get_rows, notify=rowsChanged)

    def _get_columns(self) -> int:
        return self._columns

    columns = Property(int, _get_columns, notify=columnsChanged)

    @property
    def cells(self) -> list[MatrixCell]:
        return list(self._cells)

    # ------------------------------------------------------------------ build

    @Slot()
    def rebuild(self) -> None:
        """Re-derive the grid from the source model's channels."""
        positioned: dict[tuple[int, int], ChannelInfo] = {}
        for channel in self._source.channels:
            match = _MATRIX_LABEL.match(channel.name.strip())
            if match is None:
                continue
            positioned[(int(match.group(1)), int(match.group(2)))] = channel

        rows = max((row for row, _ in positioned), default=0)
        columns = max((col for _, col in positioned), default=0)

        cells: list[MatrixCell] = []
        if rows and columns:
            for row in range(1, rows + 1):
                for col in range(1, columns + 1):
                    channel = positioned.get((row, col))
                    cells.append(
                        MatrixCell(
                            row=row,
                            col=col,
                            label=f"R{row}C{col}",
                            channel_index=-1 if channel is None else channel.index,
                            selected=False if channel is None else self._source.isSelected(channel.index),
                        )
                    )

        self.beginResetModel()
        self._cells = cells
        self._rows = rows
        self._columns = columns
        self._region = None
        self.endResetModel()

        self.rowsChanged.emit()
        self.columnsChanged.emit()

    @Slot()
    def refresh_selection(self) -> None:
        """Copy the current selection of the source model into the grid."""
        if not self._cells:
            return
        changed = False
        for position, cell in enumerate(self._cells):
            if cell.channel_index < 0:
                continue
            selected = self._source.isSelected(cell.channel_index)
            if selected != cell.selected:
                self._cells[position] = replace(cell, selected=selected)
                changed = True
        if changed:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._cells) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [self.SelectedRole])

    # --------------------------------------------------------------- commands

    @Slot(int, int)
    def toggle(self, row: int, col: int) -> None:  # noqa: N802 - QML API
        """Toggle one tile, by 1-based row/column."""
        if self._source.locked:
            return
        for cell in self._cells:
            if cell.row == int(row) and cell.col == int(col):
                if cell.channel_index >= 0:
                    self._source.setSelected(cell.channel_index, not cell.selected)
                return

    @Slot(int, int, result=bool)
    def isPresent(self, row: int, col: int) -> bool:  # noqa: N802 - QML API
        return any(
            cell.row == int(row) and cell.col == int(col) and cell.channel_index >= 0
            for cell in self._cells
        )

    # ------------------------------------------------------- drag selection

    def _in_region(self, cell: MatrixCell) -> bool:
        if self._region is None:
            return False
        top, left, bottom, right = self._normalised_region()
        return top <= cell.row <= bottom and left <= cell.col <= right

    def _normalised_region(self) -> tuple[int, int, int, int]:
        """``(top, left, bottom, right)`` regardless of the drag direction."""
        assert self._region is not None
        row_a, col_a, row_b, col_b = self._region
        return (
            min(row_a, row_b),
            min(col_a, col_b),
            max(row_a, row_b),
            max(col_a, col_b),
        )

    def _emit_region_changed(self) -> None:
        if not self._cells:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._cells) - 1, 0),
            [self.InRegionRole],
        )

    @Slot(int, int)
    def beginRegion(self, row: int, col: int) -> None:  # noqa: N802 - QML API
        """Start a rubber-band selection anchored at (row, col)."""
        row = max(1, min(self._rows, int(row)))
        col = max(1, min(self._columns, int(col)))
        self._region = (row, col, row, col)
        self._emit_region_changed()

    @Slot(int, int)
    def extendRegion(self, row: int, col: int) -> None:  # noqa: N802 - QML API
        """Move the free corner of the rubber-band selection."""
        if self._region is None:
            return
        anchor_row, anchor_col, _, _ = self._region
        row = max(1, min(self._rows, int(row)))
        col = max(1, min(self._columns, int(col)))
        if self._region == (anchor_row, anchor_col, row, col):
            return
        self._region = (anchor_row, anchor_col, row, col)
        self._emit_region_changed()

    @Slot()
    def clearRegion(self) -> None:  # noqa: N802 - QML API
        """Drop the rubber band without changing the selection."""
        if self._region is None:
            return
        self._region = None
        self._emit_region_changed()

    @Slot(result=bool)
    def hasRegion(self) -> bool:  # noqa: N802 - QML API
        return self._region is not None

    @Slot(bool)
    def commitRegion(self, add: bool = False) -> None:  # noqa: N802 - QML API
        """Apply the rubber band to the channel selection.

        The grid only covers the channels it can position, so the update is
        scoped: channels outside the grid (CREF, STATUS, COUNTER, ...) keep
        whatever state they had.

        :param add: union with the current selection instead of replacing it.
        """
        if self._region is None:
            return
        top, left, bottom, right = self._normalised_region()
        self._region = None
        self._emit_region_changed()
        if self._source.locked:
            return

        rectangle = {
            cell.channel_index
            for cell in self._cells
            if cell.channel_index >= 0
            and top <= cell.row <= bottom
            and left <= cell.col <= right
        }
        if not rectangle:
            return

        grid_channels = {cell.channel_index for cell in self._cells if cell.channel_index >= 0}
        current = set(self._source.selectedIndices())
        if add:
            wanted = current | rectangle
        else:
            wanted = (current - grid_channels) | rectangle
        self._source.set_selected_indices(sorted(wanted))

    @Slot(result=int)
    def regionSize(self) -> int:  # noqa: N802 - QML API
        """Number of grid cells currently covered by the rubber band."""
        if self._region is None:
            return 0
        top, left, bottom, right = self._normalised_region()
        return (bottom - top + 1) * (right - left + 1)
