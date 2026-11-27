import QtQuick
import QtQuick.Templates as T

// Search box with a magnifier and a clear button.
T.TextField {
    id: f
    implicitWidth: 240
    implicitHeight: 34
    leftPadding: 34
    rightPadding: text ? 30 : 12
    color: Ui.p.text
    selectionColor: Ui.alpha(Ui.p.accent, 0.35)
    selectedTextColor: Ui.p.text
    font.family: Ui.fontUi
    font.variableAxes: Ui.axes(false)
    font.pixelSize: 13
    verticalAlignment: TextInput.AlignVCenter
    selectByMouse: true
    hoverEnabled: true
    Accessible.role: Accessible.EditableText
    Accessible.name: placeholderText
    background: Rectangle {
        radius: 10
        color: f.activeFocus ? Ui.p.surface : Ui.p.track
        border.color: f.activeFocus ? Ui.p.accent : f.hovered ? Ui.p.line_strong : Ui.p.line
        border.width: f.activeFocus ? 2 : 1
        Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; glyph: ""; font.pixelSize: 13; color: Ui.p.text_muted }
    }
    Txt {
        visible: !f.text && !f.preeditText
        x: f.leftPadding; height: f.height
        text: f.placeholderText
        color: Ui.p.text_muted
    }
    T.AbstractButton {
        id: clearBtn
        visible: f.text !== ""
        width: 24; height: 24
        x: f.width - 28; y: (f.height - height) / 2
        hoverEnabled: true
        Accessible.name: "Clear search"
        onClicked: f.clear()
        contentItem: Icon { glyph: ""; font.pixelSize: 10; color: clearBtn.hovered ? Ui.p.text : Ui.p.text_muted }
        background: Rectangle { radius: 6; color: clearBtn.hovered ? Ui.p.track : "transparent" }
    }
}
