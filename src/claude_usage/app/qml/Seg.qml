import QtQuick
import QtQuick.Templates as T

// Segmented control: a sliding raised segment marks the choice.
Rectangle {
    id: seg
    property var options: []          // [[key, label], ...]
    property string current: ""
    property bool small: false
    property string name: ""
    signal picked(string key)

    readonly property int index: {
        for (var i = 0; i < options.length; i++) if (options[i][0] === current) return i
        return -1
    }
    implicitWidth: row.implicitWidth + 6
    implicitHeight: small ? 30 : 34
    radius: small ? 10 : 12
    color: Ui.p.track
    border.color: Ui.p.line
    Accessible.role: Accessible.Grouping
    Accessible.name: name

    Rectangle {
        id: knob
        readonly property Item target: seg.index >= 0 && rep.count > seg.index ? rep.itemAt(seg.index) : null
        visible: !!target
        x: target ? target.x + 3 : 3
        width: target ? target.width : 0
        y: 3; height: seg.height - 6
        radius: seg.radius - 3
        color: Ui.dark ? Ui.p.surface_alt : Ui.p.surface
        border.color: Ui.dark ? Ui.p.line_strong : Ui.p.line
        Behavior on x { NumberAnimation { duration: Ui.fast; easing.type: Easing.OutCubic } }
        Behavior on width { NumberAnimation { duration: Ui.fast; easing.type: Easing.OutCubic } }
        Rectangle {
            visible: !Ui.dark
            anchors.fill: parent; anchors.topMargin: 1; anchors.bottomMargin: -1
            z: -1; radius: parent.radius; color: Ui.alpha("#000000", 0.06)
        }
    }
    Row {
        id: row
        x: 3; y: 3; height: seg.height - 6
        Repeater {
            id: rep
            model: seg.options
            T.AbstractButton {
                id: b
                readonly property bool on: index === seg.index
                height: row.height
                implicitWidth: lbl.implicitWidth + (seg.small ? 20 : 26)
                text: modelData[1]
                hoverEnabled: true
                Accessible.role: Accessible.RadioButton
                Accessible.checkable: true
                Accessible.checked: on
                onClicked: if (!on) seg.picked(modelData[0])
                contentItem: Txt {
                    id: lbl
                    text: b.text
                    font.pixelSize: seg.small ? 12 : 13
                    font.weight: b.on ? 600 : 400
                    color: b.on || b.hovered ? Ui.p.text : Ui.p.text_muted
                    horizontalAlignment: Text.AlignHCenter
                    Behavior on color { ColorAnimation { duration: Ui.fast } }
                }
                background: Item { Focus { show: b.visualFocus; r: seg.radius - 3; anchors.margins: -1 } }
            }
        }
    }
}
