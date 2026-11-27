import QtQuick
import QtQuick.Shapes

// The last 12 weeks as a honeycomb (a column per week, a row per weekday,
// shade = the day's agent time), then streaks and a few facts.
Card {
    id: card
    property var act: null           // snapshot.activity
    readonly property bool wide: width >= 700
    implicitHeight: 2 * padding + (wide ? Math.max(lcol.height, rcol.height) : lcol.height + 24 + rcol.height)

    readonly property var days: act ? act.days : []
    // today is always in progress; only earlier days can be partial from cleanup
    readonly property bool anyPartial: days.slice(0, -1).some(function (d) { return d.partial })

    // A warm ramp (plum to amber); busier days gain contrast against the card
    // in both modes, so dark runs toward light and light toward deep.
    readonly property var ramp: Ui.dark ? ["#7f2e72", "#c93d50", "#fa7b3c", "#ffd666"] : ["#f0b85a", "#ef8a3f", "#d2443c", "#8a2a6c"]
    function heat(level) { return level <= 0 ? Ui.p.track : ramp[Math.min(4, level) - 1] }

    Column {
        id: lcol
        width: card.wide ? Math.min(parent.width * 0.56, 520) : parent.width
        spacing: 2
        Txt { height: 22; text: "Activity"; font.pixelSize: 15; font.weight: 600; display: true }
        Txt {
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_muted
            text: "Agent time per day, last " + Math.round(card.days.length / 7) + " weeks." + (card.anyPartial ? " Outlined days are partial: older transcripts were cleaned up." : "")
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
            // One path per shade, so 84 days are a dozen paths rather than 84
            // items. Partial days (transcripts cleaned up) are an outline in
            // their shade; today is filled and ringed. The glow under the
            // busiest days is one path, so where halos meet they do not stack.
            readonly property var paths: {
                var fill = ["", "", "", "", ""], ring = ["", "", "", "", ""], glow = "", today = ""
                cells.forEach(function (c) {
                    var lv = Math.max(0, Math.min(4, c.d.level)), part = c.d.partial && !c.last && lv > 0
                    if (part) {
                        fill[0] += Ui.hexPath(c.cx, c.cy, r - 2.4)
                        ring[lv] += Ui.hexPath(c.cx, c.cy, r - 2.4)
                    } else {
                        fill[lv] += Ui.hexPath(c.cx, c.cy, r - 1.6)
                        if (lv === 4) glow += Ui.hexPath(c.cx, c.cy, r * 1.3)
                    }
                    if (c.last) today = Ui.hexPath(c.cx, c.cy, r - 1.6)
                })
                return { fill: fill, ring: ring, glow: glow, today: today }
            }
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: "transparent"; fillRule: ShapePath.WindingFill
                    fillColor: Ui.alpha(card.heat(4), Ui.dark ? 0.12 : 0.08)
                    PathSvg { path: grid.paths.glow }
                }
                ShapePath { strokeColor: "transparent"; fillColor: card.heat(0); PathSvg { path: grid.paths.fill[0] } }
                ShapePath { strokeColor: "transparent"; fillColor: card.heat(1); PathSvg { path: grid.paths.fill[1] } }
                ShapePath { strokeColor: "transparent"; fillColor: card.heat(2); PathSvg { path: grid.paths.fill[2] } }
                ShapePath { strokeColor: "transparent"; fillColor: card.heat(3); PathSvg { path: grid.paths.fill[3] } }
                ShapePath { strokeColor: "transparent"; fillColor: card.heat(4); PathSvg { path: grid.paths.fill[4] } }
                ShapePath { strokeColor: card.heat(1); strokeWidth: 1.5; fillColor: "transparent"; PathSvg { path: grid.paths.ring[1] } }
                ShapePath { strokeColor: card.heat(2); strokeWidth: 1.5; fillColor: "transparent"; PathSvg { path: grid.paths.ring[2] } }
                ShapePath { strokeColor: card.heat(3); strokeWidth: 1.5; fillColor: "transparent"; PathSvg { path: grid.paths.ring[3] } }
                ShapePath { strokeColor: card.heat(4); strokeWidth: 1.5; fillColor: "transparent"; PathSvg { path: grid.paths.ring[4] } }
                ShapePath {
                    strokeColor: Ui.p.text; strokeWidth: 1.5; fillColor: "transparent"; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: grid.paths.today }
                }
            }
            Shape {
                id: hl
                visible: false
                property real cx: 0
                property real cy: 0
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: Ui.p.text; strokeWidth: 2; fillColor: "transparent"; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: Ui.hexPath(hl.cx, hl.cy, grid.r + 0.6) }
                }
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
            function showCell(c) {
                var d = c.d, dt = Ui.date(d.date + "T12:00:00")
                tip.title = Ui.wmd(dt) + ", " + dt.getFullYear()
                tip.value = d.agent_ms ? Ui.dur(d.agent_ms) + " agent time" : "No agent time"
                tip.rows = d.cost ? [{ c: Ui.p.text_faint, name: "Cost", v: Ui.cents(d.cost) }] : []
                tip.note = c.last ? "Today so far." : d.partial ? "Partial: transcripts for this day were cleaned up, so this is a lower bound." : ""
                hl.cx = c.cx; hl.cy = c.cy; hl.visible = true
                tip.showAt(grid, c.cx, c.cy - grid.r)
            }
            function clear() { tip.hide(); hl.visible = false }
            MouseArea {
                width: parent.width; height: parent.height - 24
                hoverEnabled: true
                onExited: grid.clear()
                onPositionChanged: function (m) {
                    var best = null, bd = 1e9
                    grid.cells.forEach(function (c) { var d = Math.hypot(c.cx - m.x, c.cy - m.y); if (d < bd) { bd = d; best = c } })
                    if (!best || bd > grid.r) grid.clear()
                    else grid.showCell(best)
                }
            }
            // Left and Right step through the days, starting from today
            activeFocusOnTab: cells.length > 0
            property int kb: -1
            Keys.onLeftPressed: { kb = kb < 0 ? cells.length - 1 : Math.max(0, kb - 1); showCell(cells[kb]) }
            Keys.onRightPressed: { kb = kb < 0 ? cells.length - 1 : Math.min(cells.length - 1, kb + 1); showCell(cells[kb]) }
            onActiveFocusChanged: if (!activeFocus) { kb = -1; clear() }
            Focus { show: grid.activeFocus; r: 8 }
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
                    Txt { text: modelData[1]; display: true; font.pixelSize: 28; font.weight: 600; height: 38 }
                    Txt { text: modelData[2]; font.pixelSize: 12; color: Ui.p.text_faint; width: parent.width - 8 }
                }
            }
        }
        Item { width: 1; height: 16 }
        Repeater {
            model: card.act ? card.act.facts.length : 0
            Item {
                id: fact
                readonly property var f: card.act && card.act.facts[index] || { label: "", value: "", detail: null }
                width: rcol.width; height: fact.f.detail ? 52 : 40
                Rectangle { width: parent.width; height: 1; color: Ui.p.line }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - val.implicitWidth - 16
                    spacing: 1
                    Txt { width: parent.width; height: 18; text: fact.f.label; color: Ui.p.text_muted }
                    Txt { visible: !!fact.f.detail; width: parent.width; height: 16; text: fact.f.detail || ""; font.pixelSize: 12; color: Ui.p.text_faint }
                }
                Txt { id: val; anchors.right: parent.right; height: parent.height; text: fact.f.value; font.weight: 600 }
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
