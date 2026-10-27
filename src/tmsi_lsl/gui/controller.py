"""QML-facing controller: settings, selection, device lifecycle, status.

The controller is the only object QML needs beyond the channel models.  It
keeps a validated :class:`~tmsi_lsl.settings.AppSettings` instance in sync with
the widgets, talks to the worker thread through queued signals, and turns
everything into property notifications that QML can bind to.
"""

from __future__ import annotations

import datetime as _datetime
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QMetaObject,
    QObject,
    QThread,
    QUrl,
    Qt,
    Signal,
    Slot,
)

from ..channels import ChannelInfo, resolve_selection
from ..settings import (
    AppSettings,
    BASE_RATES,
    DIVIDERS,
    DR_INTERFACES,
    DS_INTERFACES,
    SettingsError,
    default_settings_path,
)
from .. import __version__ as _VERSION
from .models import ChannelFilterModel, ChannelListModel, ChannelMatrixModel
from .session import DeviceSession

__all__ = ["AppController"]

_MAX_MESSAGES = 100

#: How the reference configuration is described in the UI.
_REFERENCE_DETAIL = {
    "common": "CREF 单独参考",
    "average": "公共平均参考",
}


def _reference_label(reference: dict | None) -> str:
    """Format ``{"reference": ..., "autoreference": ...}`` for the header."""
    if not reference:
        return ""
    method = str(reference.get("reference") or "").lower()
    if not method:
        return ""
    auto = str(reference.get("autoreference") or "").lower()
    if method == "common" and auto == "fixed":
        return "参考 common（CREF 单独参考，不自动切换）"
    detail = _REFERENCE_DETAIL.get(method, method)
    text = f"参考 {method}（{detail}）"
    if auto:
        text += f" · 自动回退 {auto}"
    return text


class AppController(QObject):
    """State + commands exposed to QML as the ``app`` context property."""

    # property notifications
    statusChanged = Signal()
    connectionChanged = Signal()
    streamingChanged = Signal()
    statsChanged = Signal()
    selectionChanged = Signal()
    messagesChanged = Signal()
    settingsChanged = Signal()
    busyChanged = Signal()

    # requests handed to the worker thread
    connectRequested = Signal(object)
    disconnectRequested = Signal()
    startRequested = Signal(object)
    stopRequested = Signal()
    restartRequested = Signal(object)

    def __init__(self, settings_path: str | Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._settings_path = Path(settings_path) if settings_path else default_settings_path()
        self._settings = AppSettings()

        self._status_text = "就绪"
        self._status_kind = "info"
        self._device_text = "未连接"
        self._reference_text = ""
        self._stats_text = "—"
        self._stream_info_text = ""
        self._connected = False
        self._streaming = False
        self._busy = False
        self._pending: str | None = None
        self._frames = 0
        self._device_sampling_rate = 0
        self._messages: list[dict] = []

        self._model = ChannelListModel(self)
        self._filter = ChannelFilterModel(self)
        self._filter.setSourceModel(self._model)
        self._model.selectionChanged.connect(self.selectionChanged)
        self._matrix = ChannelMatrixModel(self._model, self)

        # ---- worker thread -------------------------------------------------
        self._thread = QThread()
        self._thread.setObjectName("tmsi-device")
        self._session = DeviceSession()
        self._session.moveToThread(self._thread)

        self.connectRequested.connect(self._session.connect_device)
        self.disconnectRequested.connect(self._session.disconnect_device)
        self.startRequested.connect(self._session.start_stream)
        self.stopRequested.connect(self._session.stop_stream)
        self.restartRequested.connect(self._session.restart_stream)

        self._session.deviceConnected.connect(self._on_device_connected)
        self._session.deviceDisconnected.connect(self._on_device_disconnected)
        self._session.streamStarted.connect(self._on_stream_started)
        self._session.streamStopped.connect(self._on_stream_stopped)
        self._session.statsUpdated.connect(self._on_stats)
        self._session.errorOccurred.connect(self._on_error)

        self._thread.start()

        self._load_startup_settings()

    # ------------------------------------------------------------- models

    @property
    def channel_model(self) -> ChannelListModel:
        return self._model

    @property
    def channel_filter(self) -> ChannelFilterModel:
        return self._filter

    @property
    def channel_matrix(self) -> ChannelMatrixModel:
        return self._matrix

    @property
    def settings(self) -> AppSettings:
        return self._settings

    # --------------------------------------------------------- status text

    def _get_status_text(self) -> str:
        return self._status_text

    statusText = Property(str, _get_status_text, notify=statusChanged)

    def _get_status_kind(self) -> str:
        return self._status_kind

    statusKind = Property(str, _get_status_kind, notify=statusChanged)

    def _get_device_text(self) -> str:
        return self._device_text

    deviceText = Property(str, _get_device_text, notify=connectionChanged)

    def _get_reference_text(self) -> str:
        return self._reference_text

    referenceText = Property(str, _get_reference_text, notify=connectionChanged)

    def _get_connected(self) -> bool:
        return self._connected

    connected = Property(bool, _get_connected, notify=connectionChanged)

    def _get_streaming(self) -> bool:
        return self._streaming

    streaming = Property(bool, _get_streaming, notify=streamingChanged)

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=busyChanged)

    def _get_stats_text(self) -> str:
        return self._stats_text

    statsText = Property(str, _get_stats_text, notify=statsChanged)

    def _get_stream_info_text(self) -> str:
        return self._stream_info_text

    streamInfoText = Property(str, _get_stream_info_text, notify=streamingChanged)

    def _get_messages(self) -> list[dict]:
        return [dict(message) for message in self._messages]

    messages = Property("QVariantList", _get_messages, notify=messagesChanged)

    def _get_frames(self) -> int:
        return self._frames

    framesReceived = Property(int, _get_frames, notify=statsChanged)

    def _get_selected_count(self) -> int:
        return self._model.selectedCount

    selectedCount = Property(int, _get_selected_count, notify=selectionChanged)

    def _get_version(self) -> str:
        return _VERSION

    version = Property(str, _get_version, constant=True)

    def _get_selection_summary(self) -> str:
        return self._model.selectedSummary()

    selectionSummary = Property(str, _get_selection_summary, notify=selectionChanged)

    # ----------------------------------------------------------- settings

    def _get_base_rate(self) -> int:
        return self._settings.sampling.base_rate

    def _set_base_rate(self, value: int) -> None:
        value = int(value)
        if value not in BASE_RATES or value == self._settings.sampling.base_rate:
            return
        self._settings = self._settings.with_sampling(value, self._settings.sampling.divider)
        self.settingsChanged.emit()

    baseRate = Property(int, _get_base_rate, _set_base_rate, notify=settingsChanged)

    def _get_divider(self) -> int:
        return self._settings.sampling.divider

    def _set_divider(self, value: int) -> None:
        value = int(value)
        if value not in DIVIDERS or value == self._settings.sampling.divider:
            return
        self._settings = self._settings.with_sampling(self._settings.sampling.base_rate, value)
        self.settingsChanged.emit()

    divider = Property(int, _get_divider, _set_divider, notify=settingsChanged)

    def _get_effective_rate(self) -> int:
        return self._settings.sampling.effective_rate

    effectiveRate = Property(int, _get_effective_rate, notify=settingsChanged)

    def _get_device_rate(self) -> int:
        return self._device_sampling_rate

    deviceSamplingRate = Property(int, _get_device_rate, notify=connectionChanged)

    def _get_dr_interface(self) -> str:
        return self._settings.device.dr_interface

    def _set_dr_interface(self, value: str) -> None:
        if value not in DR_INTERFACES or value == self._settings.device.dr_interface:
            return
        self._settings = self._settings.with_device(dr_interface=value)
        self.settingsChanged.emit()

    drInterface = Property(str, _get_dr_interface, _set_dr_interface, notify=settingsChanged)

    def _get_ds_interface(self) -> str:
        return self._settings.device.ds_interface

    def _set_ds_interface(self, value: str) -> None:
        if value not in DS_INTERFACES or value == self._settings.device.ds_interface:
            return
        self._settings = self._settings.with_device(ds_interface=value)
        self.settingsChanged.emit()

    dsInterface = Property(str, _get_ds_interface, _set_ds_interface, notify=settingsChanged)

    def _get_lsl_name(self) -> str:
        return self._settings.lsl.name

    def _set_lsl_name(self, value: str) -> None:
        name = (value or "").strip()
        if not name or name == self._settings.lsl.name:
            return
        self._settings = self._settings.with_lsl(name=name)
        self.settingsChanged.emit()

    lslName = Property(str, _get_lsl_name, _set_lsl_name, notify=settingsChanged)

    def _get_lsl_offset(self) -> float:
        return self._settings.lsl.offset

    def _set_lsl_offset(self, value: float) -> None:
        try:
            offset = float(value)
        except (TypeError, ValueError):
            return
        if not 0.0 <= offset <= 5.0 or offset == self._settings.lsl.offset:
            return
        self._settings = self._settings.with_lsl(offset=offset)
        self.settingsChanged.emit()

    lslOffset = Property(float, _get_lsl_offset, _set_lsl_offset, notify=settingsChanged)

    def _get_settings_path(self) -> str:
        return str(self._settings_path)

    settingsPath = Property(str, _get_settings_path, notify=settingsChanged)

    # ------------------------------------------------------------- commands

    @Slot()
    def connectHardware(self) -> None:  # noqa: N802 - QML API
        self._connect("hardware")

    @Slot()
    def connectSimulator(self) -> None:  # noqa: N802 - QML API
        self._connect("simulate")

    def _connect(self, mode: str) -> None:
        if self._busy or self._connected:
            return
        self._pending = "connect"
        self._set_busy(True)
        self._set_status("正在连接设备…" if mode != "simulate" else "正在启动模拟设备…", "info")
        self.connectRequested.emit(
            {
                "mode": mode,
                "dr_interface": self._settings.device.dr_interface,
                "ds_interface": self._settings.device.ds_interface,
                "device_index": self._settings.device.device_index,
                "num_retries": 2,
                "sim_channels": 32,
            }
        )

    @Slot()
    def disconnectDevice(self) -> None:  # noqa: N802 - QML API
        if self._busy or not self._connected:
            return
        self._pending = "disconnect"
        self._set_busy(True)
        self._set_status("正在断开…", "info")
        self.disconnectRequested.emit()

    @Slot()
    def startStream(self) -> None:  # noqa: N802 - QML API
        if self._busy or not self._connected or self._streaming:
            return
        indices = self._model.selectedIndices()
        if not indices:
            self._set_status("请先选通至少一个通道", "warn")
            return
        self._pending = "start"
        self._set_busy(True)
        self._set_status("正在应用通道配置并启动推流…", "info")
        self.startRequested.emit(
            {
                "indices": indices,
                "base_rate": self._settings.sampling.base_rate,
                "divider": self._settings.sampling.divider,
                "lsl_name": self._settings.lsl.name,
                "lsl_offset": self._settings.lsl.offset,
            }
        )

    @Slot()
    def stopStream(self) -> None:  # noqa: N802 - QML API
        if not self._streaming:
            return
        self._pending = "stop"
        self._set_status("正在停止推流…", "info")
        self.stopRequested.emit()

    @Slot()
    def restartStream(self) -> None:  # noqa: N802 - QML API
        """Stop, apply the current sampling settings and start again.

        The SAGA firmware only accepts configuration writes while idle, and an
        LSL outlet's sample rate is fixed once created, so this necessarily
        breaks the stream for a moment and publishes a new one.
        """
        if self._busy or not self._connected or not self._streaming:
            return
        indices = self._model.selectedIndices()
        if not indices:
            self._set_status("请先选通至少一个通道", "warn")
            return
        self._pending = "restart"
        self._set_busy(True)
        self._set_status("正在按新配置重启推流（会有约 1 秒断流）…", "info")
        self.restartRequested.emit(
            {
                "indices": indices,
                "base_rate": self._settings.sampling.base_rate,
                "divider": self._settings.sampling.divider,
                "lsl_name": self._settings.lsl.name,
                "lsl_offset": self._settings.lsl.offset,
            }
        )

    # ------------------------------------------------------------- settings IO

    def _snapshot(self) -> AppSettings:
        """Current UI state as settings, including the channel selection."""
        settings = self._settings
        if self._model.count:
            settings = settings.with_channels(
                self._model.selectedNames(), self._model.selectedIndices()
            )
        return AppSettings(
            schema_version=settings.schema_version,
            device=settings.device,
            channels=settings.channels,
            sampling=settings.sampling,
            lsl=settings.lsl,
        )

    def _load_startup_settings(self) -> None:
        if not self._settings_path.is_file():
            self._add_message("info", f"未找到设置文件，使用默认值：{self._settings_path}")
            return
        try:
            settings = AppSettings.load(self._settings_path)
        except SettingsError as exc:
            self._set_status(f"设置文件无法读取：{exc}", "error")
            self._add_message("error", str(exc))
            return
        self._apply_settings(settings)
        self._add_message("ok", f"已加载设置：{self._settings_path}")

    def _apply_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        self.settingsChanged.emit()
        self.selectionChanged.emit()
        self._apply_selection_to_model()

    def _apply_selection_to_model(self) -> None:
        channels = self._model.channels
        if not channels:
            return
        if self._settings.channels.is_empty():
            return
        indices, warnings = resolve_selection(
            channels, self._settings.channels.names, self._settings.channels.indices
        )
        self._model.set_selected_indices(indices)
        for warning in warnings:
            self._add_message("warn", warning)
        self._add_message(
            "info", f"已按设置文件恢复选通：{len(indices)} 个通道"
        )

    @staticmethod
    def _as_path(target: str) -> Path:
        if target.startswith("file:"):
            return Path(QUrl(target).toLocalFile())
        return Path(target)

    @Slot()
    def saveSettings(self) -> None:  # noqa: N802 - QML API
        try:
            path = self._snapshot().save(self._settings_path)
        except SettingsError as exc:
            self._set_status(f"保存失败：{exc}", "error")
            self._add_message("error", f"保存失败：{exc}")
            return
        self._settings = self._snapshot()
        self.settingsChanged.emit()
        self._set_status(f"设置已保存：{path}", "ok")
        self._add_message("ok", f"设置已保存到 {path}")

    @Slot(str)
    def saveSettingsAs(self, target: str) -> None:  # noqa: N802 - QML API
        path = self._as_path(target)
        try:
            self._snapshot().save(path)
        except SettingsError as exc:
            self._set_status(f"保存失败：{exc}", "error")
            self._add_message("error", f"保存失败：{exc}")
            return
        self._settings_path = path
        self._settings = self._snapshot()
        self.settingsChanged.emit()
        self._set_status(f"设置已保存：{path}", "ok")
        self._add_message("ok", f"设置已另存为 {path}")

    @Slot(str)
    def loadSettingsFrom(self, target: str) -> None:  # noqa: N802 - QML API
        if self._streaming:
            # Loading would replace the gate and the sampling plan, which cannot
            # be applied while the device is sampling anyway.
            message = "推流中不能加载设置，请先停止推流"
            self._set_status(message, "warn")
            self._add_message("warn", message)
            return
        path = self._as_path(target)
        try:
            settings = AppSettings.load(path)
        except SettingsError as exc:
            self._set_status(f"加载失败：{exc}", "error")
            self._add_message("error", f"加载失败：{exc}")
            return
        self._settings_path = path
        self._apply_settings(settings)
        self._set_status(f"设置已加载：{path}", "ok")
        self._add_message("ok", f"已加载设置：{path}")

    # ---------------------------------------------------------------- messages

    def _add_message(self, kind: str, text: str) -> None:
        stamp = _datetime.datetime.now().strftime("%H:%M:%S")
        self._messages.append({"time": stamp, "kind": kind, "text": str(text)})
        if len(self._messages) > _MAX_MESSAGES:
            del self._messages[:-_MAX_MESSAGES]
        self.messagesChanged.emit()

    @Slot()
    def clearMessages(self) -> None:  # noqa: N802 - QML API
        self._messages.clear()
        self.messagesChanged.emit()

    # ----------------------------------------------------------- state helpers

    def _set_status(self, text: str, kind: str = "info") -> None:
        self._status_text = text
        self._status_kind = kind
        self.statusChanged.emit()

    def _set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.busyChanged.emit()

    # ------------------------------------------------------- worker callbacks

    def _on_device_connected(self, payload: dict) -> None:
        summary = payload.get("summary", {})
        channels = [
            ChannelInfo(
                index=int(item["index"]),
                name=str(item["name"]),
                channel_type=str(item["type"]),
                unit=str(item["unit"]),
                divider=int(item["divider"]),
                sample_rate=None if item.get("rate") is None else float(item["rate"]),
                is_active=bool(item.get("active", False)),
            )
            for item in payload.get("channels", [])
        ]

        self._model.load(channels, {channel.index for channel in channels if channel.is_active})
        self._apply_selection_to_model()

        # Without a saved configuration, start from what the device is doing
        # right now instead of silently proposing different settings.
        if self._settings.channels.is_empty():
            base_rate = int(summary.get("base_rate") or 0)
            sampling_rate = int(summary.get("sampling_rate") or 0)
            divider = 1
            if base_rate and 0 < sampling_rate <= base_rate:
                divider = max(1, round(base_rate / sampling_rate))
            if base_rate in BASE_RATES and divider in DIVIDERS:
                self._settings = self._settings.with_sampling(base_rate, divider)
                self.settingsChanged.emit()

        simulated = payload.get("mode") == "simulate"
        self._device_sampling_rate = int(summary.get("sampling_rate") or 0)
        self._device_text = (
            ("模拟设备" if simulated else str(summary.get("name", "SAGA")))
            + f" · SN {summary.get('serial')}"
            + f" · {summary.get('num_active')}/{summary.get('num_channels')} 通道"
            + f" · 设备采样率 {summary.get('sampling_rate')} Hz"
            + f" · {summary.get('dr_interface')}/{summary.get('ds_interface')}"
        )
        self._reference_text = _reference_label(payload.get("reference"))
        self._connected = True
        self._pending = None
        self._set_busy(False)
        self.connectionChanged.emit()
        self._set_status(f"已连接：{len(channels)} 个通道", "ok")
        self._add_message("ok", f"已连接 {'模拟设备' if simulated else summary.get('name')}")

    def _on_device_disconnected(self) -> None:
        self._connected = False
        self._streaming = False
        self._frames = 0
        self._device_sampling_rate = 0
        self._model.set_locked(False)
        self._device_text = "未连接"
        self._reference_text = ""
        self._stream_info_text = ""
        self._stats_text = "—"
        self._pending = None
        self._set_busy(False)
        self.connectionChanged.emit()
        self.streamingChanged.emit()
        self.statsChanged.emit()
        self._set_status("已断开连接（通道表保留，便于继续编辑并保存设置）", "info")

    def _on_stream_started(self, payload: dict) -> None:
        self._streaming = True
        self._frames = 0
        self._stats_text = "等待数据…"
        # The firmware refuses configuration writes while sampling, so the gate
        # must not be editable either.
        self._model.set_locked(True)
        self._stream_info_text = (
            f"LSL『{payload.get('name')}』· {payload.get('channel_count')} 通道 "
            f"@ {float(payload.get('sample_rate') or 0):g} Hz · "
            f"source id {payload.get('source_id')}"
        )

        # The device rate changed when the configuration was applied; refresh the
        # header so it stops advertising the value read at connect time.
        applied_rate = int(payload.get("device_sampling_rate") or 0)
        if applied_rate:
            previous = self._device_sampling_rate
            self._device_sampling_rate = applied_rate
            if previous and f"设备采样率 {previous} Hz" in self._device_text:
                self._device_text = self._device_text.replace(
                    f"设备采样率 {previous} Hz", f"设备采样率 {applied_rate} Hz"
                )
            active = payload.get("device_num_active")
            base_rate = payload.get("device_base_rate")
            if base_rate:
                self._add_message(
                    "info",
                    f"已应用采样率：基准 {base_rate} Hz → 设备当前 {applied_rate} Hz"
                    + (f"，{active} 个通道" if active else ""),
                )

        reference = str(payload.get("reference") or "")
        if reference:
            auto_reference = str(payload.get("auto_reference") or "")
            self._reference_text = _reference_label(
                {"reference": reference, "autoreference": auto_reference}
            )
            self._add_message("info", self._reference_text)
        self._pending = None
        self._set_busy(False)
        self.connectionChanged.emit()
        self.streamingChanged.emit()
        self.statsChanged.emit()
        self._set_status("推流中，LSL 流已发布", "ok")
        self._add_message("ok", self._stream_info_text)

    def _on_stream_stopped(self) -> None:
        self._streaming = False
        self._stats_text = "—"
        self._pending = None
        self._model.set_locked(False)
        self.streamingChanged.emit()
        self.statsChanged.emit()
        self._set_status("已停止推流", "info")
        self._add_message("info", "已停止推流")

    def _on_stats(self, payload: dict) -> None:
        self._frames = int(payload.get("frames", 0))
        rate = float(payload.get("rate", 0.0))
        since_last = payload.get("since_last")
        text = f"{self._frames:,} 帧 · {rate:,.0f} 帧/秒"
        if since_last is not None:
            text += f" · 最后一块 {since_last:.2f} s 前"
        self._stats_text = text
        self.statsChanged.emit()

    def _on_error(self, message: str) -> None:
        action, self._pending = self._pending, None
        self._set_busy(False)
        if action == "connect":
            self._connected = False
            self._device_text = "未连接"
            self._reference_text = ""
        if action in {"start", "stop", "disconnect", "restart"} or not self._connected:
            self._streaming = False
            self._model.set_locked(False)
        self.connectionChanged.emit()
        self.streamingChanged.emit()
        self._set_status(message, "error")
        self._add_message("error", message)

    # --------------------------------------------------------------- shutdown

    @Slot()
    def shutdown(self) -> None:
        """Stop the worker thread; safe to call more than once."""
        if not self._thread.isRunning():
            return
        try:
            QMetaObject.invokeMethod(
                self._session, "shutdown", Qt.ConnectionType.BlockingQueuedConnection
            )
        except Exception:  # noqa: BLE001 - best effort on teardown
            pass
        self._thread.quit()
        if not self._thread.wait(5000):  # pragma: no cover - hung driver
            self._thread.terminate()
            self._thread.wait(1000)
