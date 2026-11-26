import QtQuick
import QtQuick.Shapes
import QtQuick.Templates as T

// The range as a smoothed stacked area by project or model, with a metric
// toggle, crosshair readout, legend chips that hide a series, and the
// bucket still filling drawn as an open "so far" dot.
Card {
    id: card
    property var tl: null            // snapshot.timeline
    property var range: null         // snapshot.range
    property bool scoped: false      // a project filter is on
    property string metric: "cost"
    property string group: "project"
    property var hidden: ({})
    property string iso: ""
    property int hover: -1
    implicitHeight: 2 * padding + head.height + 8 + chart.height + 14 + legend.height + (soFarNote.visible ? 30 : 0)

    readonly property var metrics: ({
        cost: { label: "Cost", fmt: Ui.cents, big: Ui.money, axis: Ui.moneyAxis, time: false },
        tokens: { label: "Tokens", fmt: Ui.tok, big: Ui.tok, axis: Ui.tokAxis, time: false },
        messages: { label: "Messages", fmt: Ui.int, big: Ui.int, axis: function (v) { return v >= 1000 ? Ui.tokAxis(v) : String(v) }, time: false },
        agent_time: { label: "Agent time", fmt: Ui.dur, big: Ui.dur, axis: Ui.durAxis, time: true }
    })
    readonly property var mt: metrics[metric]
    readonly property var grp: tl ? tl.by[group] : null
    readonly property int n: tl ? tl.starts.length : 0
    readonly property var starts: tl ? tl.starts.map(function (s) { return Ui.date(s) }) : []
    readonly property var all: {
        if (!grp) return []
        var out = []
        for (var i = 0; i < grp.keys.length; i++) {
            var arr = grp.values[metric][i], sum = 0
            for (var j = 0; j < arr.length; j++) sum += arr[j]
            if (sum > 0) out.push({ key: grp.keys[i], color: Ui.seriesColor(grp.colors[i]), other: grp.colors[i] < 0, arr: arr, sum: sum })
        }
        return out
    }
    readonly property var vis: all.filter(function (s) { return !card.hidden[s.key] })
    readonly property var tot: {
        var t = []
        for (var k = 0; k < n; k++) { var a = 0; for (var i = 0; i < vis.length; i++) a += vis[i].arr[k]; t.push(a) }
        return t
    }
    readonly property bool anyHidden: vis.length !== all.length
    readonly property real total: tl ? (anyHidden ? tot.reduce(function (a, b) { return a + b }, 0) : tl.totals[metric]) : 0
    readonly property int lastFull: tl && tl.partial_last ? n - 2 : n - 1

    function seriesName(s) { return s.other ? (group === "model" ? "Other models" : "Other projects") : s.key }
    function bucketLabel(k) {
        var d = starts[k]
        if (!d) return ""
        if (tl.bucket === "hour") return Ui.wmd(d) + ", " + Ui.hourLabel(d.getHours())
        if (tl.bucket === "6h") { var e = new Date(d.getTime() + 6 * 3600000); return Ui.wmd(d) + ", " + Ui.hourLabel(d.getHours()) + " to " + Ui.hourLabel(e.getHours()) }
        if (tl.bucket === "week") return "Week of " + Ui.md(d) + ", " + d.getFullYear()
        return Ui.wmd(d)
    }
    function partialNote() {
        return tl.bucket === "hour" ? "This hour so far." : tl.bucket === "6h" ? "This 6-hour block so far." : tl.bucket === "week" ? "This week so far." : "Today so far."
    }
    function toggle(key) {
        var h = {}
        for (var k in hidden) h[k] = hidden[k]
        h[key] = !h[key]
        // never hide the last visible series
        if (all.filter(function (s) { return !h[s.key] }).length === 0) return
        hidden = h
    }
    onGroupChanged: hidden = ({})
    onTlChanged: { if (all.filter(function (s) { return !hidden[s.key] }).length === 0) hidden = ({}) }

    // ---- header
    Item {
        id: head
        width: parent.width; height: 84
        Column {
            spacing: 2
            Txt {
                text: card.mt.label + ", " + (card.range ? card.range.label : "") + (card.tl ? { hour: ", per hour", "6h": ", every 6 hours", day: ", per day", week: ", per week" }[card.tl.bucket] : "")
                      + (card.scoped && card.group === "project" ? "" : card.group === "model" ? ", by model" : ", by project")
                color: Ui.p.text_muted
            }
            Txt {
                text: card.mt.big(card.total)
                display: true
                font.pixelSize: 40
                font.weight: 600
                font.features: ({})
                height: 52
            }
        }
        Row {
            anchors.right: parent.right
            spacing: 8
            Seg {
                small: true
                name: "Metric"
                options: [["cost", "Cost"], ["tokens", "Tokens"], ["messages", "Messages"], ["agent_time", "Agent time"]]
                current: card.metric
                onPicked: function (k) { card.metric = k }
            }
            Seg {
                small: true
                name: "Group by"
                options: [["project", "Project"], ["model", "Model"]]
                current: card.group
                onPicked: function (k) { card.group = k }
            }
        }
    }

    // ---- plot
    Item {
        id: chart
        y: head.height + 8
        width: parent.width
        height: 300
        readonly property real xL: 52
        readonly property real xR: width - 6
        readonly property real yTop: 12
        readonly property real base: height - 30
        activeFocusOnTab: true
        Accessible.role: Accessible.Chart
        Accessible.name: card.mt.label + ", " + (card.range ? card.range.label : "") + ", total " + card.mt.big(card.total)

        function px(k) { return card.n > 1 ? xL + k * (xR - xL) / (card.n - 1) : (xL + xR) / 2 }
        readonly property real maxV: Math.max.apply(null, card.tot.concat([0]))
        readonly property real step: card.mt.time ? Ui.timeStep(maxV, 4) : Ui.niceStep(maxV, 4)
        readonly property real ymax: maxV > 0 ? Math.ceil(maxV / step) * step : 1
        function py(v) { return base - v / ymax * (base - yTop) }
        readonly property var ticks: { var t = []; if (maxV > 0) for (var v = 0; v <= ymax + 1e-9; v += step) t.push(v); return t }

        // stacked bands over the complete buckets, plus where each band is thickest
        readonly property var bands: {
            var out = [], last = card.lastFull
            if (last < 0) return out
            var acc = []
            for (var k = 0; k <= last; k++) acc.push(0)
            for (var i = 0; i < card.vis.length; i++) {
                var s = card.vis[i], lo = acc.slice(), up = lo.map(function (a, k) { return a + s.arr[k] })
                acc = up
                var U = up.map(function (v, k) { return [chart.px(k), chart.py(v)] }), L = lo.map(function (v, k) { return [chart.px(k), chart.py(v)] })
                var d, e
                if (U.length === 1) {
                    var bx = U[0][0]
                    d = "M" + Ui.f1(bx - 4) + "," + Ui.f1(L[0][1]) + "V" + Ui.f1(U[0][1]) + "H" + Ui.f1(bx + 4) + "V" + Ui.f1(L[0][1]) + "Z"
                    e = "M" + Ui.f1(bx - 4) + "," + Ui.f1(U[0][1]) + "H" + Ui.f1(bx + 4)
                } else {
                    e = Ui.pathFwd(U)
                    d = e + Ui.pathBack(L) + "Z"
                }
                var best = { th: 0, k: 0, y: 0 }
                for (k = 0; k < U.length; k++) { var th = L[k][1] - U[k][1]; if (th > best.th) best = { th: th, k: k, y: (U[k][1] + L[k][1]) / 2 } }
                out.push({ key: s.key, color: s.color, d: d, e: e, best: best, s: s })
            }
            return out
        }
        readonly property var soFar: tl && tl.partial_last && card.n > 1 ? { x: px(card.n - 1), y: py(card.tot[card.n - 1]) } : null

        FontMetrics { id: fm; font.family: Ui.fontUi; font.pixelSize: 12 }
        FontMetrics { id: fmAxis; font.family: Ui.fontUi; font.pixelSize: 11 }

        readonly property var xlabels: {
            var out = [], c = card, d, k, n = c.starts.length
            if (!c.tl) return out
            var b = c.tl.bucket
            for (k = 0; k < n; k++) {
                d = c.starts[k]
                if (b === "hour") { if (d.getHours() % 4 === 0) out.push([k, d.getHours() === 0 ? Ui.wd[d.getDay()] : Ui.hourLabel(d.getHours())]) }
                else if (b === "6h") { if (d.getHours() === 0) out.push([k, Ui.wd[d.getDay()] + " " + d.getDate()]) }
                else if (b === "week") { var e = new Date(d.getTime() + 6 * 86400000); if (e.getMonth() !== d.getMonth() || k === 0) out.push([k, Ui.mon[e.getMonth()] + (e.getMonth() === 0 || k === 0 ? " " + e.getFullYear() : "")]) }
            }
            if (b === "day") {
                if (n <= 10) for (k = 0; k < n; k++) out.push([k, Ui.wd[c.starts[k].getDay()] + " " + c.starts[k].getDate()])
                else { var st = n <= 31 ? 5 : 14; for (k = n - 1; k >= 0; k -= st) out.unshift([k, Ui.md(c.starts[k])]) }
            }
            var placed = [], prevEnd = -1e9
            for (var i = 0; i < out.length; i++) {
                var w = fmAxis.advanceWidth(out[i][1]), cx = Math.max(xL + w / 2, Math.min(xR - w / 2, px(out[i][0])))
                if (cx - w / 2 < prevEnd + 10) continue
                placed.push({ x: cx - w / 2, w: w, label: out[i][1] })
                prevEnd = cx + w / 2
            }
            return placed
        }

        // direct labels on the three largest named bands, where each is thickest
        readonly property var direct: {
            var out = [], boxes = []
            if (soFar) boxes.push({ x: soFar.x - 60, y: soFar.y - 30, w: 70, h: 44 })
            var named = bands.filter(function (b) { return !b.s.other && b.best.th >= 26 }).sort(function (a, b) { return b.s.sum - a.s.sum }).slice(0, 3)
            for (var i = 0; i < named.length; i++) {
                var b = named[i], name = b.key.length > 26 ? b.key.slice(0, 25) + "." : b.key
                var w = fm.advanceWidth(name) + 28, lastX = px(Math.max(0, card.lastFull))
                var cx = Math.max(xL + w / 2 + 4, Math.min(lastX - w / 2 - 4, px(b.best.k)))
                var box = { x: cx - w / 2, y: b.best.y - 11, w: w, h: 22 }
                if (box.x < xL) continue
                var clash = false
                for (var j = 0; j < boxes.length; j++) { var o = boxes[j]; if (box.x < o.x + o.w + 4 && o.x < box.x + box.w + 4 && box.y < o.y + o.h + 4 && o.y < box.y + box.h + 4) clash = true }
                if (clash) continue
                boxes.push(box)
                out.push({ key: b.key, name: name, color: b.color, x: box.x, y: box.y, w: w })
            }
            return out
        }

        Repeater {
            model: chart.ticks
            Item {
                Rectangle { visible: modelData > 0; x: chart.xL; width: chart.xR - chart.xL; y: Math.round(chart.py(modelData)); height: 1; color: Ui.p.line }
                Txt {
                    x: 0; width: chart.xL - 12; y: chart.py(modelData) - 8; height: 16
                    horizontalAlignment: Text.AlignRight
                    text: card.mt.axis(modelData); font.pixelSize: 11; color: Ui.p.text_faint
                }
            }
        }
        Rectangle { x: chart.xL; width: chart.xR - chart.xL; y: Math.round(chart.base); height: 1; color: Ui.p.line_strong }
        Repeater {
            model: chart.xlabels
            Txt { x: modelData.x; y: chart.base + 10; height: 16; text: modelData.label; font.pixelSize: 11; color: Ui.p.text_faint }
        }

        Repeater {
            model: chart.bands
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                opacity: card.iso === "" || card.iso === modelData.key ? 1 : 0.16
                Behavior on opacity { NumberAnimation { duration: Ui.fast } }
                ShapePath {
                    strokeColor: "transparent"
                    fillGradient: LinearGradient {
                        x1: 0; y1: chart.yTop; x2: 0; y2: chart.base
                        GradientStop { position: 0; color: Ui.alpha(modelData.color, Ui.dark ? 0.95 : 0.92) }
                        GradientStop { position: 1; color: Ui.alpha(modelData.color, Ui.dark ? 0.62 : 0.7) }
                    }
                    PathSvg { path: modelData.d }
                }
            }
        }
        // a surface-coloured gap above every band, so neighbours read apart without relying on hue
        Repeater {
            model: chart.bands
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                opacity: card.iso === "" || card.iso === modelData.key ? 1 : 0.16
                ShapePath {
                    strokeColor: Ui.p.surface; strokeWidth: 1.5; fillColor: "transparent"
                    capStyle: ShapePath.FlatCap; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: modelData.e }
                }
            }
        }
        Shape {
            anchors.fill: parent
            visible: chart.bands.length > 0
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Ui.alpha(Ui.p.text, 0.32); strokeWidth: 1.2; fillColor: "transparent"
                PathSvg { path: chart.bands.length ? chart.bands[chart.bands.length - 1].e : "" }
            }
        }

        // the bucket still filling
        Rectangle {
            visible: !!chart.soFar && chart.maxV > 0
            x: chart.soFar ? chart.soFar.x - 5 : 0; y: chart.soFar ? chart.soFar.y - 5 : 0
            width: 10; height: 10; radius: 5
            color: Ui.p.surface; border.width: 2; border.color: Ui.p.text_muted
        }
        Txt {
            visible: !!chart.soFar && chart.maxV > 0
            x: chart.soFar ? Math.min(chart.xR - width, chart.soFar.x - width / 2) : 0
            y: chart.soFar ? (chart.soFar.y - 30 < chart.yTop - 8 ? chart.soFar.y + 10 : chart.soFar.y - 26) : 0
            height: 16
            text: "so far"; font.pixelSize: 11; color: Ui.p.text_muted
        }

        Repeater {
            model: chart.direct
            Rectangle {
                x: modelData.x; y: modelData.y; width: modelData.w; height: 22; radius: 7
                color: Ui.alpha(Ui.p.surface, 0.94)
                border.color: Ui.p.line_strong
                opacity: card.iso === "" || card.iso === modelData.key ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Ui.fast } }
                Rectangle { x: 9; y: 7.5; width: 7; height: 7; radius: 3.5; color: modelData.color }
                Txt { x: 21; height: 22; text: modelData.name; font.pixelSize: 12 }
            }
        }

        Txt {
            visible: chart.maxV <= 0
            anchors.centerIn: parent
            text: card.all.length && !card.vis.length ? "Every series is hidden" : "No usage in this range"
            color: Ui.p.text_faint
        }

        // crosshair, readout, keyboard
        Rectangle {
            visible: card.hover >= 0
            x: card.hover >= 0 ? Math.round(chart.px(card.hover)) : 0
            y: chart.yTop - 6; width: 1; height: chart.base - chart.yTop + 6
            color: Ui.alpha(Ui.p.text, 0.4)
        }
        Rectangle {
            visible: card.hover >= 0 && chart.maxV > 0
            x: card.hover >= 0 ? chart.px(card.hover) - 5 : 0
            y: card.hover >= 0 ? chart.py(card.tot[card.hover]) - 5 : 0
            width: 10; height: 10; radius: 5
            color: Ui.p.text; border.width: 2; border.color: Ui.p.surface
        }
        function show(k) {
            card.hover = k
            var rows = card.vis.map(function (s) { return [s, s.arr[k]] }).filter(function (r) { return r[1] > 0 }).sort(function (a, b) { return b[1] - a[1] })
            var shown = rows.slice(0, 8).map(function (r) { return { c: r[0].color, name: card.seriesName(r[0]), v: card.mt.fmt(r[1]) } })
            if (rows.length > 8) shown.push({ c: "transparent", name: "+" + (rows.length - 8) + " more", v: "" })
            tip.title = card.bucketLabel(k)
            tip.value = card.mt.fmt(card.tot[k]) + (k === card.n - 1 && card.tl.partial_last ? " so far" : "")
            tip.rows = shown
            tip.note = k === card.n - 1 && card.tl.partial_last ? card.partialNote() : ""
            tip.showAt(chart, chart.px(k), chart.py(card.tot[k]))
        }
        function clear() { card.hover = -1; tip.hide() }
        Keys.onLeftPressed: show(Math.max(0, (card.hover < 0 ? card.n : card.hover) - 1))
        Keys.onRightPressed: show(Math.min(card.n - 1, card.hover + 1))
        Keys.onPressed: function (e) {
            if (e.key === Qt.Key_Home) { show(0); e.accepted = true }
            else if (e.key === Qt.Key_End) { show(card.n - 1); e.accepted = true }
        }
        onActiveFocusChanged: if (!activeFocus) clear()
        Focus { show: chart.activeFocus; r: 12; anchors.margins: -4 }

        MouseArea {
            x: chart.xL - 10; width: chart.xR - chart.xL + 20
            height: chart.height
            hoverEnabled: true
            enabled: card.n > 0
            onExited: chart.clear()
            onPositionChanged: function (m) {
                var px = m.x + x
                chart.show(Math.max(0, Math.min(card.n - 1, Math.round((px - chart.xL) / Math.max(1, chart.xR - chart.xL) * (card.n - 1)))))
            }
        }
        ChartTip { id: tip }
    }

    // ---- legend
    Flow {
        id: legend
        y: chart.y + chart.height + 14
        width: parent.width
        spacing: 8
        Repeater {
            model: card.all
            T.AbstractButton {
                id: chip
                readonly property bool on: !card.hidden[modelData.key]
                implicitWidth: cr.implicitWidth + 22
                implicitHeight: 30
                hoverEnabled: true
                Accessible.role: Accessible.CheckBox
                Accessible.checkable: true
                Accessible.checked: on
                Accessible.name: card.seriesName(modelData) + ", " + card.mt.big(modelData.sum)
                onClicked: card.toggle(modelData.key)
                onHoveredChanged: card.iso = hovered && on ? modelData.key : (card.iso === modelData.key ? "" : card.iso)
                background: Rectangle {
                    radius: 10
                    color: chip.on ? (chip.hovered ? Ui.p.surface_alt : Ui.p.track) : "transparent"
                    border.color: chip.on ? Ui.p.line : Ui.p.line_strong
                    Behavior on color { ColorAnimation { duration: Ui.fast } }
                    Focus { show: chip.visualFocus; r: 10 }
                }
                contentItem: Item {
                    Row {
                        id: cr
                        anchors.centerIn: parent
                        spacing: 8
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 8; height: 8; radius: 4
                            color: chip.on ? modelData.color : "transparent"
                            border.width: chip.on ? 0 : 1.5
                            border.color: modelData.color
                        }
                        Txt { height: 20; text: card.seriesName(modelData); color: chip.on ? Ui.p.text : Ui.p.text_muted; width: Math.min(implicitWidth, 220) }
                        Txt { height: 20; text: card.mt.big(modelData.sum); font.pixelSize: 12; color: Ui.p.text_muted }
                    }
                }
            }
        }
    }
    Row {
        id: soFarNote
        visible: !!(card.tl && card.tl.partial_last)
        y: legend.y + legend.height + 14
        spacing: 8
        Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 9; height: 9; radius: 4.5; color: "transparent"; border.width: 1.5; border.color: Ui.p.text_muted }
        Txt {
            height: 16
            font.pixelSize: 12; color: Ui.p.text_faint
            text: card.tl ? "Open dot: " + card.partialNote().replace(" so far.", "").toLowerCase() + ", still in progress. Click a chip to hide a series." : ""
        }
    }
}
