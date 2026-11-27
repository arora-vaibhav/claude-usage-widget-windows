import QtQuick
import QtQuick.Templates as T

// On/off switch in the Windows 11 shape, drawn from the tokens.
T.Switch {
    id: s
    implicitWidth: 40
    implicitHeight: 20
    hoverEnabled: true
    indicator: Rectangle {
        width: 40; height: 20; radius: 10
        color: s.checked ? (s.hovered ? Qt.lighter(Ui.p.accent, 1.06) : Ui.p.accent) : (s.hovered ? Ui.p.track : "transparent")
        border.width: s.checked ? 0 : 1
        border.color: Ui.p.text_faint
        Behavior on color { ColorAnimation { duration: Ui.fast } }
        Rectangle {
            readonly property int d: s.pressed ? 14 : s.hovered ? 13 : 12
            width: s.pressed ? 17 : d; height: d; radius: d / 2
            y: (parent.height - height) / 2
            x: s.checked ? parent.width - width - 4 : 4
            color: s.checked ? Ui.p.on_accent : Ui.p.text_muted
            Behavior on x { NumberAnimation { duration: Ui.fast; easing.type: Easing.OutCubic } }
        }
        Focus { show: s.visualFocus; r: 10 }
    }
    contentItem: Item {}
}
