import QtQuick
import QtQuick.Templates as T

// Thin overlay scroll bar that widens on hover.
T.ScrollBar {
    id: sb
    implicitWidth: hovered || pressed ? 10 : 6
    implicitHeight: hovered || pressed ? 10 : 6
    padding: 2
    hoverEnabled: true
    minimumSize: 0.06
    visible: size < 1
    contentItem: Rectangle {
        radius: width / 2
        color: sb.pressed ? Ui.p.text_faint : Ui.p.line_strong
        opacity: sb.active || sb.hovered ? 1 : 0.7
    }
}
