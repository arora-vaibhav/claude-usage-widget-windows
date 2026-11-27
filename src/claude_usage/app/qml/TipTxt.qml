import QtQuick
import QtQuick.Templates as T

// Text that shows its whole value in a tooltip when it is cut short.
Txt {
    id: t
    Loader {
        anchors.fill: parent
        active: t.truncated
        sourceComponent: Item {
            HoverHandler { id: hh }
            T.ToolTip {
                visible: hh.hovered
                delay: 400
                margins: 8
                y: -implicitHeight - 6
                padding: 8
                topPadding: 5; bottomPadding: 5
                // the template has no size of its own
                implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
                implicitHeight: contentItem.implicitHeight + topPadding + bottomPadding
                text: t.text
                contentItem: Text {
                    text: t.text
                    color: Ui.p.tooltip_text
                    font.family: Ui.fontUi; font.pixelSize: 12; font.variableAxes: Ui.axes(false)
                }
                background: Rectangle { radius: 8; color: Ui.p.tooltip_bg; border.color: Ui.dark ? Ui.p.line_strong : "transparent" }
            }
        }
    }
}
