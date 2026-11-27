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
    // This week's peak can include samples newer than the weekly card's
    // reading; quote the card's number so the two never disagree.
    function peakOf(w) { return w.current && limits && limits.weekly ? limits.weekly.pct : w.peak }

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
        function barH(w) { return w.hours_at_limit > 0 ? Math.max(4, base - py(w.hours_at_limit)) : 2 }
        // This week always gets a label, then near misses (latest first) where
        // they clear the bars, the axis labels and each other. Labels may use
        // the axis gutter, so this week's (the last bar) can sit right above it.
        readonly property var labels: {
            var out = [], boxes = [], i, w
            for (i = 0; i < n; i++) boxes.push({ x: cx(i) - bw / 2, y: base - barH(card.weeks[i]), w: bw, h: barH(card.weeks[i]) })
            ticks.forEach(function (t) { boxes.push({ x: xR + 6, y: py(t) - 7, w: width - xR - 6, h: 14 }) })
            function free(b) {
                if (b.x < 0 || b.x + b.w > width || b.y < 0) return false
                for (var j = 0; j < boxes.length; j++) {
                    var o = boxes[j]
                    if (b.x < o.x + o.w + 3 && o.x < b.x + b.w + 3 && b.y < o.y + o.h + 3 && o.y < b.y + b.h + 3) return false
                }
                return true
            }
            function place(i, text, now, last) {
                var tw = fm.advanceWidth(text) + (now ? 2 : 0), c = cx(i), y = base - barH(card.weeks[i]) - 20
                var cands = [[c - tw / 2, y], [c - bw / 2, y], [c + bw / 2 - tw, y]]
                if (now) cands.push([c - bw / 2, y - 16], [c + bw / 2 - tw, y - 16])
                if (last) cands.push([xR - tw, 2])
                for (var k = 0; k < cands.length; k++) {
                    var b = { x: cands[k][0], y: cands[k][1], w: tw, h: 14 }
                    if (free(b)) { boxes.push(b); out.push({ x: b.x, y: b.y, text: text, now: now }); return true }
                }
                return false
            }
            // a narrow card drops "Now" so the number can sit on its bar
            for (i = n - 1; i >= 0; i--) if (card.weeks[i].current) {
                var v = Ui.pct(card.peakOf(card.weeks[i]))
                place(i, "Now " + v, true, false) || place(i, v, true, false) || place(i, "Now " + v, true, true)
            }
            for (i = n - 1; i >= 0; i--) {
                w = card.weeks[i]
                if (!w.current && !(w.hours_at_limit > 0) && w.peak >= 0.9) place(i, Ui.pct(w.peak), false)
            }
            return out
        }
        FontMetrics { id: fm; font.family: Ui.fontUi; font.pixelSize: 11; font.variableAxes: Ui.axes(false) }
        Accessible.role: Accessible.Chart
        Accessible.name: "Hours at the weekly limit per past week"

        Repeater {
            model: chart.ticks.length
            Item {
                readonly property real v: chart.ticks[index] || 0
                Rectangle { x: 0; width: chart.xR; y: Math.round(chart.py(parent.v)); height: 1; color: Ui.p.line }
                Txt { x: chart.xR + 6; y: chart.py(parent.v) - 8; height: 16; text: parent.v + "h"; font.pixelSize: 11; color: Ui.p.text_faint }
            }
        }
        Rectangle { width: chart.xR; y: Math.round(chart.base); height: 1; color: Ui.p.line_strong }
        Repeater {
            model: chart.months.length
            Item {
                readonly property var m: chart.months[index] || { x: 0, label: "" }
                Rectangle { x: parent.m.x; y: chart.base; width: 1; height: 4; color: Ui.p.line_strong }
                Txt { x: parent.m.x + 3; y: chart.base + 6; height: 16; text: parent.m.label; font.pixelSize: 11; color: Ui.p.text_faint }
            }
        }
        Repeater {
            id: bars
            model: card.weeks.length
            Item {
                id: bar
                readonly property var wk: card.weeks[index] || { hours_at_limit: 0, peak: 0, current: false, start: "" }
                readonly property bool hit: bar.wk.hours_at_limit > 0
                readonly property real h: hit ? Math.max(4, chart.base - chart.py(bar.wk.hours_at_limit)) : 2
                readonly property bool hovered: hover.containsMouse || chart.kb === index
                x: chart.cx(index) - chart.bw / 2
                width: chart.bw
                y: chart.base - h
                height: h
                Rectangle {
                    anchors.fill: parent
                    radius: bar.hit ? Math.min(4, chart.bw / 2) : 1
                    color: bar.hit ? (bar.hovered ? Qt.lighter(Ui.p.bad_fill, 1.12) : Ui.p.bad_fill)
                         : bar.wk.current ? Ui.p.accent : Ui.p.text_faint
                }
                Rectangle { visible: bar.hit && bar.h > 4; width: parent.width; height: Math.min(4, bar.h / 2); y: parent.height - height; color: parent.children[0].color }
                MouseArea {
                    id: hover
                    x: -(chart.slot - chart.bw) / 2; width: chart.slot
                    y: -(chart.base - chart.yTop) + bar.h - 10; height: chart.base - chart.yTop + 20
                    hoverEnabled: true
                    onEntered: chart.tipFor(index)
                    onExited: tip.hide()
                }
            }
        }
        Repeater {
            model: chart.labels.length
            Txt {
                readonly property var l: chart.labels[index] || { x: 0, y: 0, text: "", now: false }
                x: l.x; y: l.y; height: 14
                text: l.text
                font.pixelSize: 11
                font.weight: l.now ? 600 : 400
                color: l.now ? Ui.p.text : Ui.p.text_faint
            }
        }
        ChartTip { id: tip }
        function tipFor(i) {
            var bar = bars.itemAt(i), wk = card.weeks[i]
            if (!bar || !wk) return
            var s = Ui.date(wk.start), hit = wk.hours_at_limit > 0
            tip.title = wk.current ? "This week, from " + Ui.md(s) : "Week of " + Ui.md(s)
            tip.value = hit ? Math.round(wk.hours_at_limit) + "h at the limit" : (wk.current ? "Now " : "Peak ") + Ui.pct(card.peakOf(wk))
            tip.rows = []
            tip.note = wk.current ? "Still in progress." : ""
            tip.showAt(bar, bar.width / 2, 0)
        }
        // Left and Right step through the weeks, starting from this one
        activeFocusOnTab: n > 0
        property int kb: -1
        Keys.onLeftPressed: { kb = kb < 0 ? n - 1 : Math.max(0, kb - 1); tipFor(kb) }
        Keys.onRightPressed: { kb = kb < 0 ? n - 1 : Math.min(n - 1, kb + 1); tipFor(kb) }
        onActiveFocusChanged: if (!activeFocus) { kb = -1; tip.hide() }
        Focus { show: chart.activeFocus; r: 8 }
    }
}
