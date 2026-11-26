import QtQuick

// A titled card of setting rows.
Column {
    id: g
    property string title: ""
    default property alias rows: list.data
    spacing: 10
    Txt { text: g.title; font.pixelSize: 14; font.weight: 600; display: true; height: 22 }
    Card {
        width: g.width
        height: list.implicitHeight + 8
        padding: 0
        Column {
            id: list
            x: Ui.pad; y: 4
            width: parent.width - 2 * Ui.pad
        }
    }
}
