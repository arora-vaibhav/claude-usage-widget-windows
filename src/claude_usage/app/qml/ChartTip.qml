import QtQuick
import QtQuick.Templates as T

// Floating readout for charts. Lives in the window overlay so it can cross
// card edges; the value leads, the series names follow.
Rectangle {
    id: tip
    property string title: ""
    property string value: ""
    property var rows: []        // [{c: color, name: string, v: string}]
    property string note: ""

    parent: T.Overlay.overlay
    z: 1000
    visible: false
    opacity: visible ? 1 : 0
    radius: 12
    color: Ui.p.tooltip_bg
    border.color: Ui.dark ? Ui.p.line_strong : "transparent"
    width: Math.min(340, Math.max(176, fm.advanceWidth(title) + 24, big.advanceWidth(value) + 24, rowsWidth() + 24))
    height: col.implicitHeight + 20
    Behavior on x { enabled: tip.visible; NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }
    Behavior on y { enabled: tip.visible; NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }

    FontMetrics { id: fm; font.family: Ui.fontUi; font.pixelSize: 12; font.variableAxes: Ui.axes(false) }
    FontMetrics { id: big; font.family: Ui.fontUi; font.pixelSize: 15; font.weight: 600; font.variableAxes: Ui.axes(false) }

    function rowsWidth() {
        var w = 0
        for (var i = 0; i < rows.length; i++) w = Math.max(w, fm.advanceWidth(rows[i].name) + fm.advanceWidth(rows[i].v) + 40)
        return w
    }
    function showAt(item, px, py) {
        if (!parent) return
        var pt = item.mapToItem(parent, px, py)
        var nx = pt.x + 16, ny = pt.y - height - 12
        if (nx + width > parent.width - 8) nx = pt.x - width - 16
        if (nx < 8) nx = 8
        if (ny < 8) ny = pt.y + 18
        if (ny + height > parent.height - 8) ny = parent.height - 8 - height
        if (!visible) { x = nx; y = ny }
        x = nx; y = ny
        visible = true
    }
    function hide() { visible = false }

    Column {
        id: col
        x: 12; y: 10
        width: tip.width - 24
        spacing: 2
        Txt { width: parent.width; text: tip.title; color: Ui.p.tooltip_text; opacity: 0.72; font.pixelSize: 12 }
        Txt { width: parent.width; visible: tip.value !== ""; text: tip.value; color: Ui.p.tooltip_text; font.pixelSize: 15; font.weight: 600 }
        Item { width: 1; height: tip.rows.length ? 4 : 0 }
        Repeater {
            model: tip.rows
            Item {
                width: col.width; height: 20
                Rectangle { y: 9; width: 12; height: 3; radius: 1.5; color: modelData.c }
                Txt { x: 20; width: parent.width - 20 - val.implicitWidth - 12; height: 20; text: modelData.name; color: Ui.p.tooltip_text; opacity: 0.85; font.pixelSize: 12 }
                Txt { id: val; anchors.right: parent.right; height: 20; text: modelData.v; color: Ui.p.tooltip_text; font.pixelSize: 12; font.weight: 500 }
            }
        }
        Rectangle { visible: tip.note !== ""; width: parent.width; height: 1; color: Ui.alpha(Ui.p.tooltip_text, 0.18) }
        Txt {
            visible: tip.note !== ""
            width: parent.width
            text: tip.note
            color: Ui.p.tooltip_text
            opacity: 0.75
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            topPadding: 4
        }
    }
}
