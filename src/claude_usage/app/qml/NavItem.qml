import QtQuick
import QtQuick.Templates as T

// Sidebar row: page link or project filter.
T.AbstractButton {
    id: n
    property string glyph: ""
    property string count: ""
    property color dot: "transparent"
    property bool hasDot: false
    property bool ring: false
    property bool selected: false
    property bool dim: false
    implicitHeight: 34
    hoverEnabled: true
    Accessible.role: Accessible.Button
    Accessible.name: text + (count ? ", " + count : "")
    Accessible.checked: selected

    background: Rectangle {
        radius: 10
        color: n.selected ? (Ui.dark ? Ui.p.surface_alt : Ui.p.surface) : n.hovered ? Ui.alpha(Ui.p.text, 0.05) : "transparent"
        border.color: n.selected ? Ui.p.line : "transparent"
        Behavior on color { ColorAnimation { duration: Ui.fast } }
        Rectangle {
            visible: n.selected
            x: 0; width: 3; radius: 1.5
            y: 9; height: parent.height - 18
            color: Ui.p.brand
        }
        Focus { show: n.visualFocus; r: 10 }
    }
    contentItem: Item {
        Icon {
            id: ic
            visible: n.glyph !== ""
            x: 12; width: 18; height: parent.height
            glyph: n.glyph
            color: n.selected ? Ui.p.text : Ui.p.text_muted
        }
        Rectangle {
            id: d
            visible: n.hasDot
            x: 14; anchors.verticalCenter: parent.verticalCenter
            width: 8; height: 8; radius: 4
            color: n.ring ? "transparent" : n.dot
            border.width: n.ring ? 1.5 : 0
            border.color: Ui.p.text_faint
        }
        Txt {
            x: n.glyph !== "" ? 42 : n.hasDot ? 32 : 12
            width: parent.width - x - cnt.implicitWidth - 20
            height: parent.height
            text: n.text
            font.weight: n.selected ? 600 : 400
            color: n.selected || n.hovered ? Ui.p.text : n.dim ? Ui.p.text_faint : Ui.p.text_muted
        }
        Txt {
            id: cnt
            anchors.right: parent.right; anchors.rightMargin: 10
            height: parent.height
            text: n.count
            font.pixelSize: 12
            color: Ui.p.text_faint
        }
    }
}
