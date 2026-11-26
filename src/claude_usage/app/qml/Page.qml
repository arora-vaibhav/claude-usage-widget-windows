import QtQuick
import QtQuick.Templates as T

// A scrolling page: content centred with a maximum width, title header on top.
Flickable {
    id: page
    property var snap: null
    property string title: ""
    property string subtitle: ""
    default property alias content: body.data
    property alias tools: tools.data
    readonly property real cw: Math.min(width - 2 * margin, 1520)
    readonly property real margin: width >= 1200 ? 40 : 32

    contentWidth: width
    contentHeight: body.y + body.implicitHeight + 48
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    T.ScrollBar.vertical: Bar {}

    Item {
        id: header
        x: (page.width - page.cw) / 2; y: 30
        width: page.cw; height: 64
        Column {
            spacing: 4
            width: parent.width - tools.width - 16
            Txt { width: parent.width; text: page.title; display: true; font.pixelSize: 28; font.weight: 600; height: 36 }
            Txt { width: parent.width; text: page.subtitle; color: Ui.p.text_muted; visible: text !== "" }
        }
        Row {
            id: tools
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 4
            spacing: 8
        }
    }
    Column {
        id: body
        x: header.x; y: header.y + header.height + 28
        width: page.cw
        spacing: Ui.gap
    }
}
