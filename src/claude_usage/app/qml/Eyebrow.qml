import QtQuick

// Section label: small caps title, a short scope line, an optional pill.
Item {
    id: e
    property string title: ""
    property string sub: ""
    property string pill: ""
    property string pillKind: "none"
    implicitHeight: 30
    Row {
        y: 6
        spacing: 12
        height: 24
        Txt { height: 24; text: e.title; font.pixelSize: 12; font.weight: 600; font.letterSpacing: 0.9; color: Ui.p.text_muted }
        Txt { height: 24; text: e.sub; font.pixelSize: 12; color: Ui.p.text_faint; width: Math.min(implicitWidth, e.width - 200) }
        Pill { visible: e.pill !== ""; anchors.verticalCenter: parent.verticalCenter; kind: e.pillKind; text: e.pill }
    }
}
