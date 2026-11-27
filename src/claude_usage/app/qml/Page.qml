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
    readonly property real margin: width >= 1150 ? 40 : 24

    contentWidth: width
    contentHeight: body.y + body.implicitHeight + 48
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    T.ScrollBar.vertical: Bar {}

    // Keyboard: the page scrolls, and whatever takes focus is brought into view.
    function scrollTo(y) {
        glide.to = Math.max(0, Math.min(contentHeight - height, y))
        if (glide.to !== contentY) glide.restart()
    }
    NumberAnimation { id: glide; target: page; property: "contentY"; duration: Ui.ms(150); easing.type: Easing.OutCubic }
    Keys.onPressed: function (e) {
        var y = glide.running ? glide.to : contentY, k = e.key
        if (k === Qt.Key_PageDown || k === Qt.Key_Space && !(e.modifiers & Qt.ShiftModifier)) y += height * 0.9
        else if (k === Qt.Key_PageUp || k === Qt.Key_Space) y -= height * 0.9
        else if (k === Qt.Key_Down) y += 40
        else if (k === Qt.Key_Up) y -= 40
        else if (k === Qt.Key_Home) y = 0
        else if (k === Qt.Key_End) y = contentHeight
        else return
        e.accepted = true
        scrollTo(y)
    }
    readonly property Item focused: Window.activeFocusItem
    onFocusedChanged: {
        var it = focused, p = it
        while (p && p !== page) p = p.parent
        // a click already shows where it landed; only keyboard focus moves the page
        if (!p || it === page || it.focusReason === Qt.MouseFocusReason) return
        var r = it.mapToItem(contentItem, 0, 0, it.width, it.height)
        if (r.y < contentY + 24) scrollTo(r.y - 24)
        else if (r.y + r.height > contentY + height - 24) scrollTo(Math.min(r.y - 24, r.y + r.height - height + 24))
    }
    // a click on the page itself gives it the keys
    TapHandler { onTapped: page.forceActiveFocus() }

    Item {
        id: header
        x: (page.width - page.cw) / 2; y: 20
        width: page.cw; height: 58
        Column {
            spacing: 2
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
        x: header.x; y: header.y + header.height + 16
        width: page.cw
        spacing: Ui.gap
    }
}
