// TMSi SAGA -> LSL front-end: channel gating, sample rate, settings file.
//
// Context objects provided by app.py:
//   app           - AppController (state, commands, settings path)
//   channelModel  - ChannelListModel (selection state)
//   channelFilter - ChannelFilterModel (search / type filter)

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    width: 1200
    height: 860
    minimumWidth: 1000
    minimumHeight: 640
    visible: true
    title: qsTr("TMSi SAGA → LSL · 通道选通与采样率")

    // Device interfaces rarely change; keep them out of the way by default.
    property bool advancedOpen: false

    // "list" = flat channel table, "matrix" = electrode grid (R#C# names).
    property string channelView: "list"
    // Set once the user picks a view explicitly, so auto-switching stops.
    property bool viewChosen: false

    // Electrode-style names (R1C1 ... R8C8) are much easier to inspect as a grid,
    // so switch to it automatically as soon as such channels show up.
    Connections {
        target: channelMatrix
        function onRowsChanged() {
            if (!root.viewChosen && channelMatrix.rows > 0)
                root.channelView = "matrix"
        }
    }

    // ------------------------------------------------------------- palette
    readonly property color bgColor: "#f4f6fb"
    readonly property color panelColor: "#ffffff"
    readonly property color borderColor: "#dde3ee"
    readonly property color textColor: "#1f2937"
    readonly property color mutedColor: "#6b7280"
    readonly property color accentColor: "#2563eb"
    readonly property color okColor: "#16a34a"
    readonly property color warnColor: "#d97706"
    readonly property color errorColor: "#dc2626"

    function statusColor(kind) {
        if (kind === "ok") return root.okColor
        if (kind === "warn") return root.warnColor
        if (kind === "error") return root.errorColor
        return root.mutedColor
    }

    function typeColor(name) {
        switch (String(name).toUpperCase()) {
        case "UNI": return "#dbeafe"
        case "BIP": return "#ede9fe"
        case "AUX": return "#ccfbf1"
        case "STATUS":
        case "COUNTER": return "#fef3c7"
        default: return "#e5e7eb"
        }
    }

    function typeTextColor(name) {
        switch (String(name).toUpperCase()) {
        case "UNI": return "#1d4ed8"
        case "BIP": return "#6d28d9"
        case "AUX": return "#0f766e"
        case "STATUS":
        case "COUNTER": return "#92400e"
        default: return "#374151"
        }
    }

    function rateText(value) {
        if (value === undefined || value === null) return "—"
        return Math.round(value) + " Hz"
    }

    // Explains the state of the sampling controls.
    function rateHintText() {
        if (app.busy && !app.connected)
            return qsTr("正在连接设备：连接完成前不能修改采样率与分频")
        if (app.streaming) {
            if (app.deviceSamplingRate > 0 && app.deviceSamplingRate !== app.effectiveRate)
                return qsTr("正在推流（设备 %1 Hz）；点下方『应用 %2 Hz 并重启推流』可切换")
                        .arg(app.deviceSamplingRate).arg(app.effectiveRate)
            return qsTr("推流中：采样率已锁定（停止推流后可修改）")
        }
        if (!app.connected)
            return qsTr("未连接：可以修改基准采样率与分频")
        if (app.deviceSamplingRate > 0 && app.deviceSamplingRate !== app.effectiveRate)
            return qsTr("已连接，可修改；设备当前 %1 Hz，点『应用选通并开始推流』后生效为 %2 Hz")
                    .arg(app.deviceSamplingRate).arg(app.effectiveRate)
        return qsTr("已连接：可修改，设备当前 %1 Hz；改完点『应用选通并开始推流』生效")
                .arg(app.deviceSamplingRate)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ------------------------------------------------------------ header
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 68
            color: root.panelColor

            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: root.borderColor
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                spacing: 12

                ColumnLayout {
                    spacing: 2
                    Label {
                        text: qsTr("TMSi SAGA → LSL")
                        font.pixelSize: 18
                        font.bold: true
                        color: root.textColor
                    }
                    Label {
                        text: app.deviceText
                        font.pixelSize: 12
                        color: root.mutedColor
                        elide: Text.ElideRight
                        Layout.maximumWidth: 620
                    }
                }

                Item { Layout.fillWidth: true }

                BusyIndicator {
                    running: app.busy
                    visible: app.busy
                    implicitWidth: 26
                    implicitHeight: 26
                }

                Button {
                    text: qsTr("连接设备")
                    enabled: !app.connected && !app.busy
                    onClicked: app.connectHardware()
                }
                Button {
                    text: qsTr("模拟设备")
                    enabled: !app.connected && !app.busy
                    onClicked: app.connectSimulator()
                }
                Button {
                    text: qsTr("断开")
                    enabled: app.connected && !app.busy
                    onClicked: app.disconnectDevice()
                }
            }
        }

        // -------------------------------------------------------------- body
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // ------------------------------------------------- channel table
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: root.bgColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: qsTr("通道选通")
                            font.pixelSize: 15
                            font.bold: true
                            color: root.textColor
                        }
                        Label {
                            visible: app.streaming
                            text: qsTr("推流中·已锁定")
                            font.pixelSize: 11
                            color: root.warnColor
                        }
                        Label {
                            text: app.selectionSummary
                            font.pixelSize: 12
                            color: root.mutedColor
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        RowLayout {
                            spacing: 2
                            Button {
                                text: qsTr("列表")
                                checkable: true
                                checked: root.channelView === "list"
                                highlighted: checked
                                onClicked: {
                                    root.viewChosen = true
                                    root.channelView = "list"
                                }
                            }
                            Button {
                                text: qsTr("矩阵")
                                checkable: true
                                checked: root.channelView === "matrix"
                                highlighted: checked
                                enabled: channelMatrix.rows > 0
                                onClicked: {
                                    root.viewChosen = true
                                    root.channelView = "matrix"
                                }
                            }
                        }
                        Label {
                            text: root.channelView === "matrix"
                                  ? qsTr("%1 × %2 网格").arg(channelMatrix.rows).arg(channelMatrix.columns)
                                  : qsTr("显示 %1 / %2").arg(channelList.count).arg(channelModel.count)
                            font.pixelSize: 12
                            color: root.mutedColor
                            elide: Text.ElideRight
                            Layout.preferredWidth: 96
                            horizontalAlignment: Text.AlignRight
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        TextField {
                            id: searchField
                            Layout.preferredWidth: 200
                            enabled: root.channelView === "list"
                            placeholderText: qsTr("按名称搜索…")
                            selectByMouse: true
                            onTextChanged: channelFilter.filterText = text
                        }

                        ComboBox {
                            id: typeCombo
                            Layout.preferredWidth: 130
                            enabled: root.channelView === "list"
                            model: [qsTr("全部类型")].concat(channelModel.types)
                            onActivated: channelFilter.typeFilter =
                                (currentIndex === 0 ? "" : currentText)
                        }

                        Item { Layout.fillWidth: true }

                        Button {
                            objectName: "selectAllButton"
                            text: qsTr("全选")
                            enabled: channelModel.count > 0 && !app.streaming
                            onClicked: channelModel.selectAll()
                        }
                        Button {
                            objectName: "clearSelectionButton"
                            text: qsTr("全不选")
                            enabled: channelModel.count > 0 && !app.streaming
                            onClicked: channelModel.clearSelection()
                        }
                        Button {
                            objectName: "invertSelectionButton"
                            text: qsTr("反选")
                            enabled: channelModel.count > 0 && !app.streaming
                            onClicked: channelModel.invertSelection()
                        }
                        Button {
                            objectName: "ref64Button"
                            text: qsTr("REF+64")
                            enabled: channelModel.count > 0 && !app.streaming
                            onClicked: channelModel.selectReferencePlus(64)
                        }
                    }

                    // table header
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 30
                        visible: root.channelView === "list"
                        color: root.panelColor
                        border.color: root.borderColor
                        border.width: 1
                        radius: 6

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 10
                            Label { text: qsTr("选通"); Layout.preferredWidth: 46; font.pixelSize: 11; color: root.mutedColor }
                            Label { text: qsTr("#"); Layout.preferredWidth: 34; font.pixelSize: 11; color: root.mutedColor }
                            Label { text: qsTr("名称"); Layout.preferredWidth: 150; font.pixelSize: 11; color: root.mutedColor }
                            Label { text: qsTr("类型"); Layout.preferredWidth: 86; font.pixelSize: 11; color: root.mutedColor }
                            Label { text: qsTr("单位"); Layout.preferredWidth: 70; font.pixelSize: 11; color: root.mutedColor }
                            Label { text: qsTr("采样率"); Layout.preferredWidth: 90; font.pixelSize: 11; color: root.mutedColor }
                            Label { text: qsTr("设备状态"); Layout.fillWidth: true; font.pixelSize: 11; color: root.mutedColor }
                        }
                    }

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: (root.channelView === "matrix" && channelMatrix.rows > 0) ? 1 : 0

                        // -------------------------------------------- list view
                        Item {
                            ListView {
                                id: channelList
                                anchors.fill: parent
                                clip: true
                                spacing: 1
                                model: channelFilter
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                                delegate: Rectangle {
                                    id: rowItem
                                    required property int index
                                    required property int number
                                    required property string name
                                    required property string type
                                    required property string unit
                                    required property var rate
                                    required property bool active
                                    required property bool mandatory
                                    required property bool selected
                                    required property int channelIndex

                                    width: channelList.width
                                    height: 40
                                    radius: 5
                                    color: selected ? "#e8f0fe"
                                         : (index % 2 === 0 ? "#ffffff" : "#fafbfe")
                                    border.color: selected ? "#c7dbff" : "transparent"
                                    border.width: 1
                                    opacity: app.connected ? 1.0 : 0.75

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 12
                                        spacing: 10

                                        Rectangle {
                                            Layout.preferredWidth: 20
                                            Layout.preferredHeight: 20
                                            Layout.alignment: Qt.AlignVCenter
                                            radius: 5
                                            color: rowItem.selected ? root.accentColor : "#ffffff"
                                            border.color: rowItem.selected
                                                          ? root.accentColor
                                                          : (rowItem.mandatory ? "#e5c07b" : "#c3cddf")
                                            border.width: 1.5
                                            Label {
                                                anchors.centerIn: parent
                                                text: "✓"
                                                visible: rowItem.selected
                                                color: "#ffffff"
                                                font.pixelSize: 12
                                                font.bold: true
                                            }
                                        }

                                        Label {
                                            Layout.preferredWidth: 34
                                            text: rowItem.number
                                            color: root.mutedColor
                                            font.pixelSize: 12
                                        }
                                        Label {
                                            Layout.preferredWidth: 150
                                            text: rowItem.name
                                            color: root.textColor
                                            font.pixelSize: 13
                                            elide: Text.ElideRight
                                        }
                                        Rectangle {
                                            Layout.preferredWidth: 86
                                            Layout.preferredHeight: 20
                                            radius: 10
                                            color: root.typeColor(rowItem.type)
                                            Label {
                                                anchors.centerIn: parent
                                                text: rowItem.type
                                                font.pixelSize: 10
                                                color: root.typeTextColor(rowItem.type)
                                            }
                                        }
                                        Label {
                                            Layout.preferredWidth: 70
                                            text: rowItem.unit ? rowItem.unit : "—"
                                            color: root.mutedColor
                                            font.pixelSize: 12
                                        }
                                        Label {
                                            Layout.preferredWidth: 90
                                            text: root.rateText(rowItem.rate)
                                            color: root.textColor
                                            font.pixelSize: 12
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: rowItem.mandatory ? qsTr("固件固定（不可关闭）")
                                                : (rowItem.active ? qsTr("设备已启用") : qsTr("设备未启用"))
                                            color: rowItem.mandatory ? root.warnColor : root.mutedColor
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                        }
                                    }

                                    HoverHandler {
                                        cursorShape: (rowItem.mandatory || app.streaming)
                                                     ? Qt.ArrowCursor : Qt.PointingHandCursor
                                    }
                                    TapHandler {
                                        enabled: !rowItem.mandatory && !app.streaming
                                        onTapped: channelModel.setSelected(rowItem.channelIndex,
                                                                           !rowItem.selected)
                                    }
                                }
                            }

                            Label {
                                anchors.centerIn: parent
                                visible: channelModel.count === 0
                                text: app.connected
                                      ? qsTr("设备没有报告任何通道")
                                      : qsTr("尚未连接设备\n点击右上角『连接设备』或『模拟设备』")
                                horizontalAlignment: Text.AlignHCenter
                                color: root.mutedColor
                                font.pixelSize: 13
                                lineHeight: 1.4
                            }
                        }

                        // ------------------------------------------ matrix view
                        Item {
                            id: matrixPage
                            readonly property int cellSize: 58
                            readonly property int gap: 6
                            readonly property int pitch: cellSize + gap
                            readonly property int rowHeaderWidth: 34

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.topMargin: 4
                                spacing: 6

                                RowLayout {
                                    Layout.alignment: Qt.AlignHCenter
                                    spacing: matrixPage.gap
                                    Item {
                                        Layout.preferredWidth: matrixPage.rowHeaderWidth
                                        Layout.preferredHeight: 1
                                    }
                                    Repeater {
                                        model: channelMatrix.columns
                                        Label {
                                            Layout.preferredWidth: matrixPage.cellSize
                                            text: "C" + (index + 1)
                                            horizontalAlignment: Text.AlignHCenter
                                            font.pixelSize: 11
                                            color: root.mutedColor
                                        }
                                    }
                                }

                                RowLayout {
                                    Layout.alignment: Qt.AlignHCenter
                                    Layout.fillHeight: true
                                    spacing: matrixPage.gap

                                    ColumnLayout {
                                        Layout.alignment: Qt.AlignTop
                                        spacing: matrixPage.gap
                                        Repeater {
                                            model: channelMatrix.rows
                                            Label {
                                                Layout.preferredWidth: matrixPage.rowHeaderWidth
                                                Layout.preferredHeight: matrixPage.cellSize
                                                text: "R" + (index + 1)
                                                horizontalAlignment: Text.AlignRight
                                                verticalAlignment: Text.AlignVCenter
                                                font.pixelSize: 11
                                                color: root.mutedColor
                                            }
                                        }
                                    }

                                    Item {
                                        id: matrixHost
                                        Layout.preferredWidth: channelMatrix.columns * matrixPage.pitch
                                        Layout.preferredHeight: channelMatrix.rows * matrixPage.pitch
                                        Layout.fillWidth: false
                                        Layout.fillHeight: false
                                        Layout.alignment: Qt.AlignTop

                                        GridView {
                                            id: matrixGrid
                                            objectName: "matrixGrid"
                                            anchors.fill: parent
                                            // Fixed width so the grid wraps exactly
                                            // after `columns` cells; drag-scrolling is
                                            // disabled so the mouse can rubber-band.
                                            interactive: false
                                            clip: true
                                            cellWidth: matrixPage.pitch
                                            cellHeight: matrixPage.pitch
                                            model: channelMatrix
                                            boundsBehavior: Flickable.StopAtBounds

                                            delegate: Rectangle {
                                                required property int row
                                                required property int col
                                                required property string label
                                                required property bool selected
                                                required property bool present
                                                required property bool inRegion

                                                width: matrixPage.cellSize
                                                height: matrixPage.cellSize
                                                radius: 6
                                                color: !present ? "#f2f4f9"
                                                       : (inRegion ? "#fde8c3"
                                                          : (selected ? "#dbeafe" : "#ffffff"))
                                                border.color: !present ? "#e7ebf3"
                                                              : (inRegion ? root.warnColor
                                                                 : (selected ? root.accentColor : "#cbd5e6"))
                                                border.width: (inRegion || selected) ? 2 : 1
                                                opacity: present ? 1.0 : 0.45

                                                Label {
                                                    anchors.centerIn: parent
                                                    text: label
                                                    font.pixelSize: 11
                                                    font.bold: selected || inRegion
                                                    color: selected ? "#1d4ed8"
                                                         : (inRegion ? "#92400e" : root.mutedColor)
                                                }
                                            }
                                        }

                                        // Rubber-band selection: press and drag to
                                        // pick a rectangle of electrodes.
                                        MouseArea {
                                            id: matrixMouse
                                            objectName: "matrixMouse"
                                            anchors.fill: parent
                                            acceptedButtons: Qt.LeftButton
                                            cursorShape: Qt.PointingHandCursor
                                            // No gating changes while sampling.
                                            enabled: !app.streaming

                                            property int pressRow: -1
                                            property int pressCol: -1
                                            property bool draggingRegion: false

                                            function cellAt(px, py) {
                                                var cx = Math.floor((px + matrixGrid.contentX)
                                                                    / matrixPage.pitch) + 1
                                                var cy = Math.floor((py + matrixGrid.contentY)
                                                                    / matrixPage.pitch) + 1
                                                return {
                                                    row: Math.max(1, Math.min(channelMatrix.rows, cy)),
                                                    col: Math.max(1, Math.min(channelMatrix.columns, cx))
                                                }
                                            }

                                            onPressed: function (mouse) {
                                                var cell = cellAt(mouse.x, mouse.y)
                                                pressRow = cell.row
                                                pressCol = cell.col
                                                draggingRegion = false
                                                channelMatrix.beginRegion(cell.row, cell.col)
                                            }

                                            onPositionChanged: function (mouse) {
                                                if (!pressed)
                                                    return
                                                var cell = cellAt(mouse.x, mouse.y)
                                                if (cell.row !== pressRow || cell.col !== pressCol) {
                                                    draggingRegion = true
                                                    channelMatrix.extendRegion(cell.row, cell.col)
                                                }
                                            }

                                            onReleased: function (mouse) {
                                                var additive = (mouse.modifiers & Qt.ControlModifier) !== 0
                                                if (draggingRegion) {
                                                    channelMatrix.commitRegion(additive)
                                                } else {
                                                    channelMatrix.clearRegion()
                                                    if (channelMatrix.isPresent(pressRow, pressCol))
                                                        channelMatrix.toggle(pressRow, pressCol)
                                                }
                                                draggingRegion = false
                                            }

                                            onCanceled: channelMatrix.clearRegion()

                                            onExited: {
                                                if (!pressed)
                                                    channelMatrix.clearRegion()
                                            }
                                        }
                                    }
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("%1 行 × %2 列 · 单击切换单个电极，按住左键拖拽框选一片（Ctrl 追加）")
                                            .arg(channelMatrix.rows).arg(channelMatrix.columns)
                                    color: root.mutedColor
                                    font.pixelSize: 11
                                    horizontalAlignment: Text.AlignHCenter
                                }
                            }
                        }
                    }
                }
            }

            // --------------------------------------------------- side panel
            Rectangle {
                Layout.preferredWidth: 372
                Layout.fillHeight: true
                color: root.panelColor

                Rectangle {
                    anchors.left: parent.left
                    width: 1
                    height: parent.height
                    color: root.borderColor
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    ScrollView {
                        id: sideScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                        ColumnLayout {
                            width: sideScroll.availableWidth
                            spacing: 12

                        // ---- sampling
                        Label {
                            text: qsTr("采样率")
                            font.pixelSize: 14
                            font.bold: true
                            color: root.textColor
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: qsTr("基准采样率")
                                Layout.preferredWidth: 110
                                color: root.mutedColor
                                font.pixelSize: 12
                            }
                            ComboBox {
                                id: baseRateCombo
                                objectName: "baseRateCombo"
                                Layout.fillWidth: true
                                // Editable while the device is open but idle; the
                                // firmware only refuses configuration writes during
                                // sampling, so that is when we lock.
                                enabled: !app.streaming && !app.busy
                                model: ["4000 Hz", "4096 Hz"]
                                currentIndex: app.baseRate === 4096 ? 1 : 0
                                onActivated: app.baseRate = (currentIndex === 0 ? 4000 : 4096)
                                Connections {
                                    target: app
                                    function onSettingsChanged() {
                                        baseRateCombo.currentIndex = app.baseRate === 4096 ? 1 : 0
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: qsTr("分频 divider")
                                Layout.preferredWidth: 110
                                color: root.mutedColor
                                font.pixelSize: 12
                            }
                            ComboBox {
                                id: dividerCombo
                                objectName: "dividerCombo"
                                Layout.fillWidth: true
                                enabled: !app.streaming && !app.busy
                                model: ["1", "2", "4", "8"]
                                currentIndex: Math.max(0, [1, 2, 4, 8].indexOf(app.divider))
                                onActivated: app.divider = [1, 2, 4, 8][currentIndex]
                                Connections {
                                    target: app
                                    function onSettingsChanged() {
                                        dividerCombo.currentIndex =
                                            Math.max(0, [1, 2, 4, 8].indexOf(app.divider))
                                    }
                                }
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 46
                            radius: 6
                            color: "#eef4ff"
                            border.color: "#d4e3ff"
                            border.width: 1
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 8

                                ColumnLayout {
                                    spacing: 0
                                    Label {
                                        text: qsTr("每通道采样率")
                                        color: "#1e3a8a"
                                        font.pixelSize: 12
                                    }
                                    Label {
                                        // Make it obvious that this is the plan,
                                        // not the device's current rate.
                                        text: app.connected ? qsTr("计划值（开始推流后生效）")
                                                            : qsTr("计划值")
                                        color: "#5b7bb5"
                                        font.pixelSize: 9
                                    }
                                }
                                Item { Layout.fillWidth: true }

                                Label {
                                    text: app.effectiveRate + " Hz"
                                    color: root.accentColor
                                    font.pixelSize: 15
                                    font.bold: true
                                }
                                Label {
                                    visible: app.connected && app.deviceSamplingRate > 0
                                    text: qsTr("设备 %1 Hz").arg(app.deviceSamplingRate)
                                    color: app.deviceSamplingRate !== app.effectiveRate
                                           ? root.warnColor : root.mutedColor
                                    font.pixelSize: 10
                                }
                            }
                        }

                        // The combos above are a *plan* and are locked once the
                        // device is open; this line explains the current state.
                        Label {
                            id: rateHint
                            Layout.fillWidth: true
                            visible: text !== ""
                            text: root.rateHintText()
                            color: (app.connected
                                    && app.deviceSamplingRate > 0
                                    && app.deviceSamplingRate !== app.effectiveRate)
                                   ? root.warnColor : root.mutedColor
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }

                        // ---- LSL
                        Label {
                            text: qsTr("LSL 输出")
                            font.pixelSize: 14
                            font.bold: true
                            color: root.textColor
                        }
                        Label { text: qsTr("流名称"); color: root.mutedColor; font.pixelSize: 12 }
                        TextField {
                            id: lslNameField
                            Layout.fillWidth: true
                            text: app.lslName
                            selectByMouse: true
                            onEditingFinished: app.lslName = text
                            Connections {
                                target: app
                                function onSettingsChanged() {
                                    if (!lslNameField.activeFocus)
                                        lslNameField.text = app.lslName
                                }
                            }
                        }
                        Label { text: qsTr("延迟补偿 offset（秒）"); color: root.mutedColor; font.pixelSize: 12 }
                        TextField {
                            id: lslOffsetField
                            Layout.fillWidth: true
                            text: app.lslOffset
                            selectByMouse: true
                            validator: DoubleValidator { bottom: 0.0; top: 5.0; decimals: 4 }
                            onEditingFinished: app.lslOffset = parseFloat(text)
                            Connections {
                                target: app
                                function onSettingsChanged() {
                                    if (!lslOffsetField.activeFocus)
                                        lslOffsetField.text = app.lslOffset
                                }
                            }
                        }

                        // ---- device interfaces (collapsed by default)
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: qsTr("设备接口")
                                font.pixelSize: 14
                                font.bold: true
                                color: root.textColor
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                visible: !root.advancedOpen
                                text: app.drInterface + " / " + app.dsInterface
                                color: root.mutedColor
                                font.pixelSize: 11
                            }
                            Button {
                                flat: true
                                text: root.advancedOpen ? qsTr("收起") : qsTr("展开")
                                onClicked: root.advancedOpen = !root.advancedOpen
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: root.advancedOpen
                            Label {
                                text: qsTr("记录仪接口")
                                Layout.preferredWidth: 110
                                color: root.mutedColor
                                font.pixelSize: 12
                            }
                            ComboBox {
                                id: drCombo
                                Layout.fillWidth: true
                                model: ["docked", "wifi", "optical"]
                                currentIndex: Math.max(0, model.indexOf(app.drInterface))
                                onActivated: app.drInterface = model[currentIndex]
                                Connections {
                                    target: app
                                    function onSettingsChanged() {
                                        drCombo.currentIndex =
                                            Math.max(0, drCombo.model.indexOf(app.drInterface))
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: root.advancedOpen
                            Label {
                                text: qsTr("坞站接口")
                                Layout.preferredWidth: 110
                                color: root.mutedColor
                                font.pixelSize: 12
                            }
                            ComboBox {
                                id: dsCombo
                                Layout.fillWidth: true
                                model: ["usb", "network", "none"]
                                currentIndex: Math.max(0, model.indexOf(app.dsInterface))
                                onActivated: app.dsInterface = model[currentIndex]
                                Connections {
                                    target: app
                                    function onSettingsChanged() {
                                        dsCombo.currentIndex =
                                            Math.max(0, dsCombo.model.indexOf(app.dsInterface))
                                    }
                                }
                            }
                        }

                        // ---- settings file
                        Label {
                            text: qsTr("设置文件（TOML）")
                            font.pixelSize: 14
                            font.bold: true
                            color: root.textColor
                        }
                        Label {
                            Layout.fillWidth: true
                            text: app.settingsPath
                            color: root.mutedColor
                            font.pixelSize: 11
                            elide: Text.ElideLeft
                            wrapMode: Text.NoWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Button {
                                Layout.fillWidth: true
                                text: qsTr("保存")
                                onClicked: app.saveSettings()
                            }
                            Button {
                                Layout.fillWidth: true
                                text: qsTr("另存为")
                                onClicked: saveAsDialog.open()
                            }
                            Button {
                                objectName: "loadButton"
                                Layout.fillWidth: true
                                text: qsTr("加载")
                                // Loading replaces the gate and sampling plan, which
                                // cannot be applied during sampling.
                                enabled: !app.streaming
                                onClicked: openDialog.open()
                            }
                        }

                        Item { Layout.preferredHeight: 4 }
                        }
                    }

                    // ---- fixed action area (always visible)
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: root.borderColor
                    }
                    Button {
                        id: startButton
                        Layout.fillWidth: true
                        Layout.preferredHeight: 42
                        enabled: app.connected && !app.streaming && !app.busy
                                 && app.selectedCount > 0
                        onClicked: app.startStream()
                        background: Rectangle {
                            radius: 6
                            color: startButton.enabled
                                   ? (startButton.pressed ? "#1d4ed8" : root.accentColor)
                                   : "#c8d3e6"
                        }
                        contentItem: Label {
                            text: qsTr("应用选通并开始推流")
                            color: "#ffffff"
                            font.pixelSize: 14
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                    Button {
                        id: restartButton
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        visible: app.streaming
                                 && app.deviceSamplingRate > 0
                                 && app.deviceSamplingRate !== app.effectiveRate
                        enabled: !app.busy && app.selectedCount > 0
                        text: qsTr("应用 %1 Hz 并重启推流").arg(app.effectiveRate)
                        onClicked: app.restartStream()
                    }
                    Button {
                        Layout.fillWidth: true
                        text: qsTr("停止推流")
                        enabled: app.streaming
                        onClicked: app.stopStream()
                    }
                    Label {
                        Layout.fillWidth: true
                        text: app.streamInfoText
                        visible: app.streamInfoText !== ""
                        color: root.mutedColor
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("数据：%1").arg(app.statsText)
                        color: app.streaming ? root.okColor : root.mutedColor
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
            }
        }

        // ------------------------------------------------------------ footer
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 34
            color: root.panelColor

            Rectangle {
                anchors.top: parent.top
                width: parent.width
                height: 1
                color: root.borderColor
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 8

                Rectangle {
                    Layout.preferredWidth: 9
                    Layout.preferredHeight: 9
                    radius: 5
                    color: root.statusColor(app.statusKind)
                }
                Label {
                    Layout.fillWidth: true
                    text: app.statusText
                    color: app.statusKind === "error" ? root.errorColor : root.textColor
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                Button {
                    text: qsTr("消息 (%1)").arg(app.messages.length)
                    flat: true
                    onClicked: logDrawer.open()
                }
                Label {
                    text: "v" + app.version
                    color: root.mutedColor
                    font.pixelSize: 11
                }
            }
        }
    }

    // ------------------------------------------------------------- drawers
    Drawer {
        id: logDrawer
        edge: Qt.BottomEdge
        width: root.width
        height: 240
        modal: false

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                Label { text: qsTr("消息"); font.bold: true; color: root.textColor }
                Item { Layout.fillWidth: true }
                Button { text: qsTr("清空"); onClicked: app.clearMessages() }
                Button { text: qsTr("关闭"); onClicked: logDrawer.close() }
            }

            ListView {
                id: logList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: app.messages
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: RowLayout {
                    required property var modelData
                    width: logList.width
                    spacing: 10
                    Label {
                        text: modelData.time
                        color: root.mutedColor
                        font.pixelSize: 11
                        font.family: "Consolas"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: modelData.text
                        color: modelData.kind === "error" ? root.errorColor
                             : (modelData.kind === "warn" ? root.warnColor : root.textColor)
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }

    // --------------------------------------------------------- file dialogs
    FileDialog {
        id: saveAsDialog
        title: qsTr("另存设置文件")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("TOML 设置 (*.toml)"), qsTr("所有文件 (*)")]
        defaultSuffix: "toml"
        onAccepted: app.saveSettingsAs(selectedFile.toString())
    }

    FileDialog {
        id: openDialog
        title: qsTr("加载设置文件")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("TOML 设置 (*.toml)"), qsTr("所有文件 (*)")]
        onAccepted: app.loadSettingsFrom(selectedFile.toString())
    }

    // ------------------------------------------------------------ shortcuts
    Shortcut { sequence: "Ctrl+S"; onActivated: app.saveSettings() }
    Shortcut { sequence: "Ctrl+O"; onActivated: if (!app.streaming) openDialog.open() }
    Shortcut { sequence: "Ctrl+L"; onActivated: logDrawer.open() }
}
