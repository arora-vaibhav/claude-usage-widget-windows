import QtQuick

// Small outlined label.
Rectangle {
    property alias text: t.text
    implicitWidth: t.implicitWidth + 18
    implicitHeight: 22
    radius: 11
    color: "transparent"
    border.color: Ui.p.line_strong
    Txt { id: t; anchors.centerIn: parent; font.pixelSize: 12; color: Ui.p.text_muted }
}
