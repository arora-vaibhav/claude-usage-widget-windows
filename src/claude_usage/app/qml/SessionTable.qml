import QtQuick
import QtQuick.Templates as T

// Sessions as rows: title and metadata on the left, right-aligned tabular
// numbers, a thin project-coloured bar scaled to cost.
Item {
    id: t
    property var rows: []
    property string sortKey: "cost"
    property bool sortDesc: true
    property bool sortable: true
    property bool showProject: false
    signal sortBy(string key)

    readonly property int rowH: 62
    implicitHeight: 34 + rows.length * rowH
    readonly property real maxCost: rows.reduce(function (a, s) { return Math.max(a, s.cost) }, 0)
    readonly property var cols: {
        var c = [["agent_ms", "Agent time", 84], ["turns", "Turns", 56], ["tokens", "Tokens", 76], ["cost", "Cost", 92], ["cache_hit", "Cache hit", 80], ["subagents", "Subagents", 84]]
        if (width < 820) c = c.filter(function (x) { return x[0] !== "subagents" && x[0] !== "cache_hit" })
        if (showProject && width >= 980) c.unshift(["project", "Project", 160])
        return c
    }
    readonly property real numW: cols.reduce(function (a, c) { return a + c[2] }, 0)
    readonly property real nameW: width - numW - 2 * Ui.pad

    function entry(e) {
        return ({ "cli": "CLI", "sdk-cli": "Automated", "sdk-py": "Automated", "sdk-ts": "Automated", "desktop": "Desktop", "claude-desktop": "Desktop", "claude-vscode": "VS Code", "vscode": "VS Code", "jetbrains": "JetBrains", "web": "Web" })[e] || e
    }
    function cell(s, key) {
        if (key === "agent_ms") return s.agent_ms || s.turns ? Ui.dur(s.agent_ms) : "n/a"
        if (key === "turns") return Ui.int(s.turns)
        if (key === "tokens") return Ui.tok(s.tokens)
        if (key === "cost") return Ui.cents(s.cost)
        if (key === "cache_hit") return Ui.pct(s.cache_hit, 1)
        if (key === "subagents") return Ui.int(s.subagents)
        if (key === "project") return s.project
        return ""
    }

    // header
    Item {
        width: t.width; height: 34
        T.AbstractButton {
            x: Ui.pad; width: t.nameW; height: 34
            enabled: t.sortable
            hoverEnabled: true
            Accessible.name: "Sort by start time"
            onClicked: t.sortBy("started")
            contentItem: Row {
                spacing: 4
                Txt { height: 34; text: "Session"; font.pixelSize: 12; color: t.sortKey === "started" ? Ui.p.text : Ui.p.text_faint }
                Icon { visible: t.sortKey === "started"; height: 34; glyph: t.sortDesc ? "" : ""; font.pixelSize: 9; color: Ui.p.text }
            }
            background: Item { Focus { show: parent.parent.visualFocus; r: 6 } }
        }
        Row {
            x: Ui.pad + t.nameW
            Repeater {
                model: t.cols
                T.AbstractButton {
                    id: hb
                    width: modelData[2]; height: 34
                    enabled: t.sortable
                    hoverEnabled: true
                    Accessible.name: "Sort by " + modelData[1]
                    onClicked: t.sortBy(modelData[0])
                    readonly property bool on: t.sortKey === modelData[0]
                    contentItem: Item {
                        Txt {
                            id: hl
                            anchors.right: parent.right
                            horizontalAlignment: modelData[0] === "project" ? Text.AlignLeft : Text.AlignRight
                            x: modelData[0] === "project" ? 12 : 0
                            height: 34; text: modelData[1]; font.pixelSize: 12
                            color: hb.on || hb.hovered ? Ui.p.text : Ui.p.text_faint
                        }
                        Icon {
                            visible: hb.on
                            anchors.right: hl.left; anchors.rightMargin: 4
                            height: 34; glyph: t.sortDesc ? "" : ""; font.pixelSize: 9; color: Ui.p.text
                        }
                    }
                    background: Item { Focus { show: hb.visualFocus; r: 6 } }
                }
            }
        }
    }

    Column {
        y: 34
        width: t.width
        Repeater {
            model: t.rows
            Item {
                id: row
                width: t.width; height: t.rowH
                readonly property var s: modelData
                readonly property var started: Ui.date(s.started)
                HoverHandler { id: hh }
                Rectangle { anchors.fill: parent; color: hh.hovered ? Ui.alpha(Ui.p.text, Ui.dark ? 0.04 : 0.03) : "transparent" }
                Rectangle { width: parent.width; height: 1; color: Ui.p.line }
                Column {
                    x: Ui.pad; y: 11
                    width: t.nameW - 16
                    spacing: 3
                    Txt { width: parent.width; height: 18; text: row.s.title || Ui.when(row.started) }
                    Row {
                        spacing: 6
                        height: 16
                        Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 6; height: 6; radius: 3; color: Ui.seriesColor(row.s.color) }
                        Txt {
                            width: t.nameW - 30; height: 16
                            font.pixelSize: 12; color: Ui.p.text_faint
                            text: [t.showProject && t.width >= 980 ? null : row.s.project, row.s.title ? Ui.whenShort(row.started) : null, row.s.model, t.entry(row.s.entrypoint),
                                   "ran " + Ui.span(Date.parse(row.s.ended) - row.started.getTime()), row.s.id.slice(0, 8)].filter(function (x) { return x }).join("  ·  ")
                        }
                    }
                    Rectangle {
                        width: t.maxCost > 0 ? Math.max(2, (t.nameW - 40) * row.s.cost / t.maxCost) : 0
                        height: 2; radius: 1
                        color: Ui.seriesColor(row.s.color)
                        opacity: 0.85
                    }
                }
                Row {
                    x: Ui.pad + t.nameW
                    height: parent.height
                    Repeater {
                        model: t.cols
                        Txt {
                            width: modelData[2]; height: row.height
                            leftPadding: modelData[0] === "project" ? 12 : 0
                            horizontalAlignment: modelData[0] === "project" ? Text.AlignLeft : Text.AlignRight
                            text: t.cell(row.s, modelData[0])
                            color: text === "n/a" ? Ui.p.text_faint : modelData[0] === "project" ? Ui.p.text_muted : Ui.p.text
                            font.weight: modelData[0] === "cost" ? 600 : 400
                        }
                    }
                }
            }
        }
    }
}
