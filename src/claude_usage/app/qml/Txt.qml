import QtQuick

// Body text on the token ramp. Tabular figures everywhere, so numbers line up
// and live values do not jitter.
Text {
    property bool display: false
    color: Ui.p.text
    font.family: display ? Ui.fontDisplay : Ui.fontUi
    font.pixelSize: 13
    font.features: { "tnum": 1 }
    textFormat: Text.PlainText
    elide: Text.ElideRight
    verticalAlignment: Text.AlignVCenter
}
