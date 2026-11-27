import QtQuick

// Keyboard focus ring, shown only for keyboard focus (visualFocus).
Rectangle {
    property bool show: false
    property real r: 10
    anchors.fill: parent
    anchors.margins: -3
    radius: r + 3
    color: "transparent"
    border.width: 2
    border.color: Ui.p.accent
    visible: show
}
