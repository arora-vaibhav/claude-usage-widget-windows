import QtQuick
import QtQuick.Shapes

// The last 12 weeks as a honeycomb (a column per week, a row per weekday,
// shade = the day's agent time), then streaks and a few facts.
Card {
    id: card
    property var act: null           // snapshot.activity
    readonly property bool wide: width >= 760
    implicitHeight: 2 * padding + (wide ? Math.max(lcol.height, rcol.height) : lcol.height + 24 + rcol.height)

    readonly property var days: act ? act.days : []
    readonly property bool anyPartial: days.some(function (d) { return d.partial })

    function heat(level) {
        if (level <= 0) return Ui.p.track
        return Ui.mix(Ui.p.track, Ui.p.accent, [0, 0.34, 0.58, 0.8, 1][Math.min(4, level)])
    }

    Column {
        id: lcol
        width: card.wide ? Math.min(parent.width * 0.56, 520) : parent.width
        spacing: 2
        Txt { height: 22; text: "Activity"; font.pixelSize: 15; font.weight: 600; display: true }
        Txt {
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_muted
            text: "Agent time per day, last " + Math.round(card.days.length / 7) + " weeks." + (card.anyPartial ? " Faded days are partial: older transcripts were cleaned up." : "")
        }
        Item { width: 1; height: 12 }
        Item {
            id: grid
            width: parent.width
            readonly property real lab: 32
            readonly property int firstDow: card.days.length ? (Ui.date(card.days[0].date + "T12:00:00").getDay() + 6) % 7 : 0
            readonly property int cols: Math.ceil((card.days.length + firstDow) / 7)
            readonly property real r: Math.max(6, Math.min(17, (width - lab - 8) / ((cols + 0.5) * Math.sqrt(3))))
            readonly property real cw: Math.sqrt(3) * r
            height: r + 6 * 1.5 * r + r + 2 + 28
            readonly property var cells: card.days.map(function (d, i) {
                var pos = i + firstDow, row = pos % 7
                return { d: d, cx: lab + Math.floor(pos / 7) * cw + (row % 2 ? cw / 2 : 0) + cw / 2, cy: r + 1 + row * 1.5 * r, last: i === card.days.length - 1 }
            })
            Repeater {
                model: ["Mon", "Wed", "Fri", "Sun"]
                Txt { x: 0; y: grid.r + 1 + index * 3 * grid.r - 8; height: 16; text: modelData; font.pixelSize: 11; color: Ui.p.text_faint }
            }
            Repeater {
                model: grid.cells
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    opacity: modelData.d.partial ? 0.45 : 1
                    ShapePath {
                        strokeColor: modelData.last ? Ui.p.text : "transparent"
                        strokeWidth: modelData.last ? 1.5 : 0
                        fillColor: card.heat(modelData.d.level)
                        PathSvg { path: Ui.hexPath(modelData.cx, modelData.cy, grid.r - 1.6) }
                    }
                }
            }
            Rectangle {
                id: hl
                visible: false
                width: grid.r * 1.6; height: width; radius: width / 2
                color: "transparent"; border.width: 2; border.color: Ui.p.text
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 4
                y: grid.height - 18
                spacing: 4
                Txt { height: 16; text: "Less"; font.pixelSize: 11; color: Ui.p.text_faint; rightPadding: 4 }
                Repeater {
                    model: 5
                    Shape {
                        width: 14; height: 16
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath { strokeColor: "transparent"; fillColor: card.heat(index); PathSvg { path: Ui.hexPath(7, 8, 6.5) } }
                    }
                }
                Txt { height: 16; text: "More"; font.pixelSize: 11; color: Ui.p.text_faint; leftPadding: 4 }
            }
            MouseArea {
                width: parent.width; height: parent.height - 24
                hoverEnabled: true
                onExited: { tip.hide(); hl.visible = false }
                onPositionChanged: function (m) {
                    var best = null, bd = 1e9
                    grid.cells.forEach(function (c) { var d = Math.hypot(c.cx - m.x, c.cy - m.y); if (d < bd) { bd = d; best = c } })
                    if (!best || bd > grid.r) { tip.hide(); hl.visible = false; return }
                    var d = best.d, dt = Ui.date(d.date + "T12:00:00")
                    tip.title = Ui.wmd(dt) + ", " + dt.getFullYear()
                    tip.value = d.agent_ms ? Ui.dur(d.agent_ms) + " agent time" : "No agent time"
                    tip.rows = d.cost ? [{ c: Ui.p.text_faint, name: "Cost", v: Ui.cents(d.cost) }] : []
                    tip.note = d.partial ? "Partial: transcripts for this day were cleaned up, so this is a lower bound." : best.last ? "Today so far." : ""
                    hl.x = best.cx - hl.width / 2; hl.y = best.cy - hl.height / 2; hl.visible = true
                    tip.showAt(grid, best.cx, best.cy - grid.r)
                }
            }
            ChartTip { id: tip }
            Accessible.role: Accessible.Chart
            Accessible.name: "Agent time per day: " + (card.act ? card.act.active_days + " active days of " + card.act.span_days : "")
        }
    }

    Column {
        id: rcol
        x: card.wide ? lcol.width + 32 : 0
        y: card.wide ? 0 : lcol.height + 24
        width: card.wide ? parent.width - lcol.width - 32 : parent.width
        spacing: 0
        Row {
            width: parent.width
            Repeater {
                model: card.act ? [
                    ["Active days", Ui.int(card.act.active_days), Ui.pct(card.act.span_days ? card.act.active_days / card.act.span_days : 0) + " of the last " + card.act.span_days + " days"],
                    ["Streak", Ui.plural(card.act.streak, "day"), "longest " + Ui.plural(card.act.longest_streak, "day")]
                ] : []
                Column {
                    width: parent.width / 2
                    spacing: 2
                    Txt { text: modelData[0]; color: Ui.p.text_muted }
                    Txt { text: modelData[1]; display: true; font.pixelSize: 28; font.weight: 600; height: 38; font.features: ({}) }
                    Txt { text: modelData[2]; font.pixelSize: 12; color: Ui.p.text_faint; width: parent.width - 8 }
                }
            }
        }
        Item { width: 1; height: 16 }
        Repeater {
            model: card.act ? card.act.facts : []
            Item {
                width: rcol.width; height: 40
                Rectangle { width: parent.width; height: 1; color: Ui.p.line }
                Txt { height: parent.height; text: modelData.label; color: Ui.p.text_muted; width: parent.width * 0.42 }
                Row {
                    anchors.right: parent.right
                    height: parent.height
                    spacing: 8
                    Txt { visible: !!modelData.detail; height: parent.height; text: modelData.detail || ""; font.pixelSize: 12; color: Ui.p.text_faint; width: Math.min(implicitWidth, rcol.width * 0.58 - val.implicitWidth - 8) }
                    Txt { id: val; height: parent.height; text: modelData.value; font.weight: 600 }
                }
            }
        }
        Rectangle { width: parent.width; height: 1; color: Ui.p.line }
        Txt {
            width: parent.width
            topPadding: 10
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: "Agent time adds up sessions that ran in parallel, so one day can pass 24 hours."
        }
    }
}
