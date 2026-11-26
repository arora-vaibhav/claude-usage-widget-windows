import QtQuick
import QtQuick.Templates as T

// Push button: quiet by default, accent when primary, glyph optional.
T.Button {
    id: b
    property string glyph: ""
    property string tip: ""
    property bool primary: false
    property bool quiet: false
    property bool spin: false
    implicitHeight: 34
    implicitWidth: Math.max(implicitHeight, row.implicitWidth + (text ? 28 : 18))
    hoverEnabled: true
    Accessible.name: text || tip
    contentItem: Item {
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 8
            Icon {
                visible: b.glyph !== ""
                glyph: b.glyph
                font.pixelSize: 14
                height: 20
                color: b.primary ? Ui.p.on_accent : b.hovered ? Ui.p.text : Ui.p.text_muted
                RotationAnimator on rotation { running: b.spin; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
            }
            Txt {
                visible: b.text !== ""
                height: 20
                text: b.text
                font.weight: b.primary ? 600 : 400
                color: b.primary ? Ui.p.on_accent : Ui.p.text
            }
        }
    }
    background: Rectangle {
        radius: 10
        color: b.primary ? (b.down ? Qt.darker(Ui.p.accent, 1.12) : b.hovered ? Qt.lighter(Ui.p.accent, 1.06) : Ui.p.accent)
             : b.quiet ? (b.hovered ? Ui.p.track : "transparent")
             : b.down ? Ui.p.line : b.hovered ? Ui.p.surface_alt : Ui.p.track
        border.color: b.primary || b.quiet ? "transparent" : b.hovered ? Ui.p.line_strong : Ui.p.line
        Behavior on color { ColorAnimation { duration: Ui.fast } }
        Focus { show: b.visualFocus; r: 10 }
    }
}
