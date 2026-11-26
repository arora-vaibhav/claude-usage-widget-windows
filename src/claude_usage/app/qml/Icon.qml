import QtQuick

// A Segoe Fluent Icons glyph.
Text {
    property string glyph: ""
    text: glyph
    color: Ui.p.text_muted
    font.family: Ui.icons
    font.pixelSize: 16
    textFormat: Text.PlainText
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    Accessible.ignored: true
}
