import QtQuick

// Past weekly windows: bar height is the time spent at 100% (the part that
// varies); weeks that never hit the limit are a stub with their peak.
Card {
    id: card
    property var limits: null
    readonly property var weeks: limits ? limits.past_weeks : []
    readonly property var done: weeks.filter(function (w) { return !w.current })
    readonly property real natural: 260 + 2 * padding
    readonly property var worst: done.reduce(function (a, w) { return w.hours_at_limit > (a ? a.hours_at_limit : 0) ? w : a }, null)

    Item {
        id: head
        width: parent.width; height: 24
        Txt { height: 24; text: "Past weeks"; color: Ui.p.text_muted }
        Txt {
            anchors.right: parent.right; height: 24
            text: card.done.length ? "since " + Ui.md(Ui.date(card.done[0].start)) : ""
            font.pixelSize: 12; color: Ui.p.text_faint
        }
    }
    Row {
        id: big
        y: head.height + 8
        height: 40
        spacing: 8
        Txt {
            height: 40
            text: card.limits && card.limits.weeks_counted ? card.limits.weeks_hit + " of " + card.limits.weeks_counted : "No history yet"
            display: true; font.pixelSize: card.limits && card.limits.weeks_counted ? 30 : 20; font.weight: 600
        }
        Txt { visible: !!(card.limits && card.limits.weeks_counted); height: 34; anchors.bottom: parent.bottom; text: "weeks hit 100%"; color: Ui.p.text_muted }
    }
    Txt {
        id: note
        y: big.y + big.height + 4
        width: parent.width
        wrapMode: Text.WordWrap
        elide: Text.ElideNone
        font.pixelSize: 12
        color: Ui.p.text_muted
        text: card.done.length ? "Hours spent at 100% each week." + (card.worst ? " Longest: " + Math.round(card.worst.hours_at_limit) + "h, week of " + Ui.md(Ui.date(card.worst.start)) + "." : "") : ""
    }

    Item {
        id: chart
        y: note.y + note.height + 16
        width: parent.width
        height: parent.height - y
        readonly property int n: card.weeks.length
        readonly property real yTop: 20
        readonly property real base: height - 24
        readonly property real xR: width - 34
        readonly property real slot: n ? xR / n : 1
        readonly property real bw: Math.max(6, Math.min(16, slot * 0.56))
        readonly property real maxH: {
            var m = 0
            card.done.forEach(function (w) { m = Math.max(m, w.hours_at_limit) })
            return Math.max(10, Math.ceil(m / 10) * 10)
        }
        readonly property var ticks: { var t = []; for (var g = 10; g <= maxH; g += maxH > 40 ? 20 : 10) t.push(g); return t }
        function py(v) { return base - v / maxH * (base - yTop) }
        function cx(i) { return slot * (i + 0.5) }
        readonly property var months: {
            var out = [], prev = -1
            for (var i = 0; i < n; i++) {
                var d = Ui.date(card.weeks[i].start), m = d.getMonth()
                if (prev >= 0 && m !== prev) out.push({ x: cx(i) - slot / 2, label: Ui.mon[m] })
                prev = m
            }
            return out
        }
        Accessible.role: Accessible.Chart
        Accessible.name: "Hours at the weekly limit per past week"

        Repeater {
            model: chart.ticks
            Item {
                Rectangle { x: 0; width: chart.xR; y: Math.round(chart.py(modelData)); height: 1; color: Ui.p.line }
                Txt { x: chart.xR + 6; y: chart.py(modelData) - 8; height: 16; text: modelData + "h"; font.pixelSize: 11; color: Ui.p.text_faint }
            }
        }
        Rectangle { width: chart.xR; y: Math.round(chart.base); height: 1; color: Ui.p.line_strong }
        Repeater {
            model: chart.months
            Item {
                Rectangle { x: modelData.x; y: chart.base; width: 1; height: 4; color: Ui.p.line_strong }
                Txt { x: modelData.x + 3; y: chart.base + 6; height: 16; text: modelData.label; font.pixelSize: 11; color: Ui.p.text_faint }
            }
        }
        Repeater {
            id: bars
            model: card.weeks
            Item {
                id: bar
                readonly property bool hit: modelData.hours_at_limit > 0
                readonly property real h: hit ? Math.max(4, chart.base - chart.py(modelData.hours_at_limit)) : 2
                readonly property bool hovered: hover.containsMouse
                x: chart.cx(index) - chart.bw / 2
                width: chart.bw
                y: chart.base - h
                height: h
                Rectangle {
                    anchors.fill: parent
                    radius: bar.hit ? Math.min(4, chart.bw / 2) : 1
                    color: bar.hit ? (bar.hovered ? Qt.lighter(Ui.p.bad_fill, 1.12) : Ui.p.bad_fill)
                         : modelData.current ? Ui.p.accent : Ui.p.text_faint
                }
                Rectangle { visible: bar.hit && bar.h > 4; width: parent.width; height: Math.min(4, bar.h / 2); y: parent.height - height; color: parent.children[0].color }
                Txt {
                    // near misses and this week carry their peak; the rest stay quiet
                    visible: !bar.hit && (modelData.current || modelData.peak >= 0.9) && chart.slot >= 22
                    x: (parent.width - width) / 2; y: -18; height: 14
                    text: (modelData.current ? "Now " : "") + Ui.pct(modelData.peak)
                    font.pixelSize: 11
                    font.weight: modelData.current ? 600 : 400
                    color: modelData.current ? Ui.p.text : Ui.p.text_faint
                }
                MouseArea {
                    id: hover
                    x: -(chart.slot - chart.bw) / 2; width: chart.slot
                    y: -(chart.base - chart.yTop) + bar.h - 10; height: chart.base - chart.yTop + 20
                    hoverEnabled: true
                    onEntered: {
                        var s = Ui.date(modelData.start)
                        tip.title = modelData.current ? "This week, from " + Ui.md(s) : "Week of " + Ui.md(s)
                        tip.value = bar.hit ? Math.round(modelData.hours_at_limit) + "h at the limit" : "Peak " + Ui.pct(modelData.peak)
                        tip.rows = []
                        tip.note = modelData.current ? "Still in progress." : ""
                        tip.showAt(bar, bar.width / 2, 0)
                    }
                    onExited: tip.hide()
                }
            }
        }
        ChartTip { id: tip }
    }
}
