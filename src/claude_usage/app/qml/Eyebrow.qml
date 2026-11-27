import QtQuick

// Section label: small caps title, a short scope line, an optional pill and
// a note after it (what went wrong, when the pill warns).
Item {
    id: e
    property string title: ""
    property string sub: ""
    property string pill: ""
    property string pillKind: "none"
    property string note: ""
    implicitHeight: 24
    Row {
        id: row
        spacing: 12
        height: 24
        Txt { id: head; height: 24; text: e.title; font.pixelSize: 12; font.weight: 600; font.letterSpacing: 0.9; color: Ui.p.text_muted }
        TipTxt { id: subT; visible: text !== ""; height: 24; text: e.sub; font.pixelSize: 12; color: Ui.p.text_faint; width: Math.min(implicitWidth, e.width - head.width - 12 - (pl.visible || e.note !== "" ? 200 : 0)) }
        Pill { id: pl; visible: e.pill !== ""; anchors.verticalCenter: parent.verticalCenter; kind: e.pillKind; text: e.pill }
        TipTxt {
            visible: e.note !== ""
            height: 24; text: e.note; font.pixelSize: 12; color: Ui.p.text_muted
            width: Math.max(0, Math.min(implicitWidth, e.width - head.width - (subT.visible ? subT.width + 12 : 0) - (pl.visible ? pl.width + 12 : 0) - 12))
        }
    }
}
