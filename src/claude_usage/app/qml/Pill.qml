import QtQuick

// Status pill: tinted background, coloured icon and text. Kinds: ok, warn, bad, none.
Rectangle {
    id: pill
    property string kind: "none"
    property string text: ""
    property string glyph: kind === "ok" ? "" : kind === "warn" ? "" : kind === "bad" ? "" : ""
    readonly property color fg: kind === "ok" ? Ui.p.ok : kind === "warn" ? Ui.p.warn : kind === "bad" ? Ui.p.bad : Ui.p.text_muted
    implicitWidth: row.implicitWidth + 20
    implicitHeight: 24
    radius: height / 2
    color: kind === "ok" ? Ui.p.ok_bg : kind === "warn" ? Ui.p.warn_bg : kind === "bad" ? Ui.p.bad_bg : Ui.p.track
    Accessible.role: Accessible.StaticText
    Accessible.name: text
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Icon { visible: pill.glyph !== ""; glyph: pill.glyph; color: pill.fg; font.pixelSize: 11; height: t.height }
        Txt { id: t; text: pill.text; color: pill.fg; font.pixelSize: 12; font.weight: 500 }
    }
}
