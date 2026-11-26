import QtQuick

// One setting: name and a short explanation on the left, its control on the right.
Item {
    id: r
    property string label: ""
    property string hint: ""
    property bool first: false
    default property alias control: slot.data
    width: parent ? parent.width : 0
    implicitHeight: Math.max(64, txt.implicitHeight + 28)
    Rectangle { visible: !r.first; width: parent.width; height: 1; color: Ui.p.line }
    Column {
        id: txt
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - slot.width - 24
        spacing: 2
        Txt { width: parent.width; text: r.label; font.pixelSize: 14 }
        Txt {
            visible: r.hint !== ""
            width: parent.width
            text: r.hint
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_muted
        }
    }
    Row {
        id: slot
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12
    }
}
