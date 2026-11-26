import QtQuick
import QtQuick.Templates as T

// Horizontal slider: accent fill, round thumb with an inner dot.
T.Slider {
    id: s
    implicitWidth: 200
    implicitHeight: 24
    hoverEnabled: true
    background: Item {
        x: s.leftPadding; y: s.topPadding + s.availableHeight / 2 - 2
        width: s.availableWidth; height: 4
        Rectangle { anchors.fill: parent; radius: 2; color: Ui.p.line_strong }
        Rectangle { width: s.visualPosition * parent.width; height: 4; radius: 2; color: Ui.p.accent }
    }
    handle: Rectangle {
        x: s.leftPadding + s.visualPosition * (s.availableWidth - width)
        y: s.topPadding + s.availableHeight / 2 - height / 2
        width: 20; height: 20; radius: 10
        color: Ui.p.surface
        border.color: Ui.p.line_strong
        Rectangle {
            anchors.centerIn: parent
            readonly property int d: s.pressed ? 8 : s.hovered ? 12 : 10
            width: d; height: d; radius: d / 2
            color: Ui.p.accent
            Behavior on width { NumberAnimation { duration: Ui.fast } }
            Behavior on height { NumberAnimation { duration: Ui.fast } }
        }
        Focus { show: s.visualFocus; r: 10 }
    }
}
