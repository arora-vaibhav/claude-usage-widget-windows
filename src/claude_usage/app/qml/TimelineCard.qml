import QtQuick
import QtQuick.Shapes
import QtQuick.Templates as T

// The range as a smoothed stacked area by project or model, with a metric
// toggle, crosshair readout, legend chips that hide a series, and the
// bucket still filling drawn as an open "so far" dot on a dashed lead.
// Repeaters take counts, so delegates persist across refreshes.
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
    property real chartH: 300
    property string dataKey: ""      // range and project; a change replays the entrance
    implicitHeight: 2 * padding + head.height + 8 + chart.height + 14 + legend.height

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
            // long daily ranges draw a 3-bucket mean so the shape reads; tooltips keep raw values
            var sm = n > 60 ? Ui.smooth(arr.slice(0, lastFull + 1), 1).concat(arr.slice(lastFull + 1)) : arr
            if (sum > 0) out.push({ key: grp.keys[i], color: Ui.seriesColor(grp.colors[i]), other: grp.colors[i] === -1, legacy: grp.colors[i] === -2, arr: arr, sm: sm, sum: sum })
        }
        return out
    }
    readonly property var vis: all.filter(function (s) { return !card.hidden[s.key] })
    function stack(f) {
        var t = []
        for (var k = 0; k < n; k++) { var a = 0; for (var i = 0; i < vis.length; i++) a += vis[i][f][k]; t.push(a) }
        return t
    }
    readonly property var tot: stack("arr")
    readonly property var plotTot: stack("sm")
    readonly property bool anyHidden: vis.length !== all.length
    readonly property real total: tl ? (anyHidden ? tot.reduce(function (a, b) { return a + b }, 0) : tl.totals[metric]) : 0
    readonly property int lastFull: tl && tl.partial_last ? n - 2 : n - 1

    function focusOn(key) { return iso === "" || !!hidden[iso] || iso === key }
    function seriesName(s) { return s.legacy ? "Old daily totals" : s.other ? (group === "model" ? "Other models" : "Other projects") : s.key }
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
    onGroupChanged: { hidden = ({}); replay() }
    onMetricChanged: replay()
    onDataKeyChanged: replay()
    onTlChanged: { if (all.filter(function (s) { return !hidden[s.key] }).length === 0) hidden = ({}) }

    // the big total counts to its new value; a metric switch jumps, since the units differ
    property real shown: 0
    property string shownMetric: metric
    Behavior on shown { id: countUp; NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
    onTotalChanged: {
        countUp.enabled = shownMetric === metric
        shown = total
        countUp.enabled = true
        shownMetric = metric
    }
    function replay() { enter.restart() }

    // The legend stays on one row while labels can keep 104 px or more;
    // past that it splits into two even rows rather than leave one chip alone.
    FontMetrics { id: fmName; font.family: Ui.fontUi; font.pixelSize: 13; font.variableAxes: Ui.axes(false) }
    FontMetrics { id: fmVal; font.family: Ui.fontUi; font.pixelSize: 12; font.variableAxes: Ui.axes(false) }
    readonly property var legendFit: {
        var room = width - 2 * padding
        function rowW(list, cap) {
            var w = -legend.spacing
            for (var i = 0; i < list.length; i++)
                w += legend.spacing + 50 + Math.min(fmName.advanceWidth(seriesName(list[i])), cap) + fmVal.advanceWidth(mt.big(list[i].sum))
            return w
        }
        var caps = [160, 140, 120, 104]
        for (var c = 0; c < caps.length; c++) if (rowW(all, caps[c]) <= room) return { cap: caps[c], w: room }
        var half = Math.ceil(all.length / 2)
        return { cap: 160, w: Math.min(room, Math.max(rowW(all.slice(0, half), 160), rowW(all.slice(half), 160)) + 1) }
    }

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
                text: card.mt.big(card.shown)
                display: true
                font.pixelSize: 40
                font.weight: 600
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
        height: card.chartH
        readonly property real xL: 52
        readonly property real xR: width - 6
        readonly property real yTop: 12
        readonly property real base: height - 30
        activeFocusOnTab: true
        Accessible.role: Accessible.Chart
        Accessible.name: card.mt.label + ", " + (card.range ? card.range.label : "") + ", total " + card.mt.big(card.total)

        function px(k) { return card.n > 1 ? xL + k * (xR - xL) / (card.n - 1) : (xL + xR) / 2 }
        // what is drawn: the (smoothed) complete buckets and the raw bucket still filling
        readonly property real maxV: Math.max.apply(null, card.plotTot.slice(0, card.lastFull + 1).concat(card.tot.slice(card.lastFull + 1), [0]))
        // 4 or 5 gridlines, whichever wastes less headroom
        readonly property var scale: {
            var best = [1, 1]
            for (var g = 4; g <= 5; g++) {
                var st = card.mt.time ? Ui.timeStep(maxV, g) : Ui.niceStep(maxV, g), top = Math.ceil(maxV / st - 1e-9) * st
                if (g === 4 || top < best[1]) best = [st, top]
            }
            return maxV > 0 ? best : [1, 1]
        }
        readonly property real step: scale[0]
        readonly property real ymax: scale[1]
        function py(v) { return base - v / ymax * (base - yTop) }
        // scale read once: step and ymax update one after the other, and a new
        // step with the old ymax (cost after tokens) once looped ten million times
        readonly property var ticks: {
            var t = [], sc = scale
            if (maxV > 0 && sc[1] / sc[0] <= 12) for (var v = 0; v <= sc[1] + 1e-9; v += sc[0]) t.push(v)
            return t
        }

        // Stacked bands over the complete buckets, plus where each band is
        // thickest. Each series' monotone curve is sampled every 3 px or so and
        // the samples are stacked, so no band's top edge dips below its bottom
        // edge. Edges interpolated separately crossed between buckets, and the
        // GPU curve renderer filled those self-intersecting outlines as shards.
        readonly property var stack: {
            var out = [], last = card.lastFull
            if (last < 0) return { bands: out, top: "" }
            var m = last > 0 ? Math.max(1, Math.ceil((px(1) - px(0)) / 3)) : 1, ns = last * m + 1
            var xs = [], acc = [], perV = (base - yTop) / ymax, j, k
            for (j = 0; j < ns; j++) { xs.push(Ui.f1(px(j / m))); acc.push(0) }
            for (var i = 0; i < card.vis.length; i++) {
                var s = card.vis[i], v = Ui.monoFill(s.sm.slice(0, last + 1), m)
                var lo = acc, up = lo.map(function (a, j) { return a + Math.max(0, v[j]) })
                acc = up
                var d = "", e = ""
                if (last === 0) {
                    var bx = px(0)
                    d = "M" + Ui.f1(bx - 4) + "," + Ui.f1(py(lo[0])) + "V" + Ui.f1(py(up[0])) + "H" + Ui.f1(bx + 4) + "V" + Ui.f1(py(lo[0])) + "Z"
                    e = "M" + Ui.f1(bx - 4) + "," + Ui.f1(py(up[0])) + "H" + Ui.f1(bx + 4)
                } else {
                    // one outline per stretch where the band is at least half a
                    // pixel thick, tapering to a point at each end. Thinner
                    // slivers (a few cents beside hundreds) have top and bottom
                    // edges that meet once rounded, and the GPU curve renderer
                    // dropped or misfilled the whole band for them.
                    for (var a0 = -1, q = 0; q <= ns; q++) {
                        var thick = q < ns && (up[q] - lo[q]) * perV > 0.5
                        if (thick && a0 < 0) a0 = Math.max(0, q - 1)
                        if (thick || a0 < 0) continue
                        var b0 = Math.min(ns - 1, q), top = "", bot = ""
                        for (j = a0; j <= b0; j++) top += (j === a0 ? "M" : "L") + xs[j] + "," + Ui.f1(py(up[j]))
                        for (j = b0; j >= a0; j--) bot += "L" + xs[j] + "," + Ui.f1(py(lo[j]))
                        d += top + bot + "Z"
                        e += top
                        a0 = -1
                    }
                }
                var best = { th: 0, k: 0, y: 0 }
                for (k = 0; k <= last; k++) {
                    var th = (up[k * m] - lo[k * m]) * perV
                    if (th > best.th) best = { th: th, k: k, y: (py(up[k * m]) + py(lo[k * m])) / 2 }
                }
                out.push({ key: s.key, color: s.color, d: d, e: e, best: best, s: s })
            }
            var line = ""
            if (out.length && last > 0) for (j = 0; j < ns; j++) line += (j ? "L" : "M") + xs[j] + "," + Ui.f1(py(acc[j]))
            return { bands: out, top: line || (out.length ? out[out.length - 1].e : "") }
        }
        readonly property var bands: stack.bands
        readonly property var soFar: tl && tl.partial_last && card.n > 1 ? { x: px(card.n - 1), y: py(card.tot[card.n - 1]) } : null
        // "so far" goes just above or below the dot where it clears the bands
        // beside it; otherwise the tooltip explains the open dot
        readonly property var soFarLabel: {
            if (!soFar) return null
            var w = fmAxis.advanceWidth("so far"), x = Math.min(xR - w, soFar.x - w / 2), ceil = base
            for (var k = 0; k <= card.lastFull; k++) {
                var bx = px(k)
                if (bx >= x - 6 && bx <= x + w + 6) ceil = Math.min(ceil, py(card.plotTot[k]))
            }
            // the side the dashed lead does not come from
            var from = card.lastFull >= 0 ? py(card.plotTot[card.lastFull]) : soFar.y
            var cands = from < soFar.y ? [soFar.y + 10, soFar.y - 26] : [soFar.y - 26, soFar.y + 10]
            for (var i = 0; i < 2; i++)
                if (cands[i] >= yTop - 10 && cands[i] + 18 <= Math.min(ceil, base)) return { x: x, y: cands[i] }
            return null
        }

        FontMetrics { id: fm; font.family: Ui.fontUi; font.pixelSize: 12; font.variableAxes: Ui.axes(false) }
        FontMetrics { id: fmAxis; font.family: Ui.fontUi; font.pixelSize: 11; font.variableAxes: Ui.axes(false) }

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
            if (soFarLabel) boxes.push({ x: soFarLabel.x, y: soFarLabel.y, w: 40, h: 16 })
            var named = bands.filter(function (b) { return !b.s.other && !b.s.legacy && b.best.th >= 26 }).sort(function (a, b) { return b.s.sum - a.s.sum }).slice(0, 3)
            for (var i = 0; i < named.length; i++) {
                var b = named[i], name = b.key
                // 2 px of slack: FontMetrics and the rendered text can differ by a pixel
                var w = Math.min(fm.advanceWidth(name), 260) + 30, lastX = px(Math.max(0, card.lastFull))
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
            model: chart.ticks.length
            Item {
                readonly property real v: chart.ticks[index] || 0
                Rectangle { visible: parent.v > 0; x: chart.xL; width: chart.xR - chart.xL; y: Math.round(chart.py(parent.v)); height: 1; color: Ui.p.line }
                Txt {
                    x: 0; width: chart.xL - 12; y: chart.py(parent.v) - 8; height: 16
                    horizontalAlignment: Text.AlignRight
                    text: card.mt.axis(parent.v); font.pixelSize: 11; color: Ui.p.text_faint
                }
            }
        }
        Rectangle { x: chart.xL; width: chart.xR - chart.xL; y: Math.round(chart.base); height: 1; color: Ui.p.line_strong }
        Repeater {
            model: chart.xlabels.length
            Txt {
                readonly property var l: chart.xlabels[index]
                x: l ? l.x : 0; y: chart.base + 10; height: 16; text: l ? l.label : ""; font.pixelSize: 11; color: Ui.p.text_faint
            }
        }

        Item {
        id: plot
        anchors.fill: parent
        property real grow: 1
        transform: Scale { origin.y: chart.base; yScale: plot.grow }
        Repeater {
            model: chart.bands.length
            Shape {
                readonly property var b: chart.bands[index] || { key: "", color: "transparent", d: "", e: "" }
                anchors.fill: parent
                // The plain triangulator: the curve renderer has dropped whole
                // bands on the GPU. Its unsmoothed edges sit under the rim strokes.
                preferredRendererType: Shape.GeometryRenderer
                opacity: card.focusOn(b.key) ? 1 : 0.16
                Behavior on opacity { NumberAnimation { duration: Ui.fast } }
                ShapePath {
                    strokeColor: "transparent"
                    fillGradient: LinearGradient {
                        x1: 0; y1: chart.yTop; x2: 0; y2: chart.base
                        GradientStop { position: 0; color: Ui.alpha(b.color, Ui.dark ? 1 : 0.92) }
                        GradientStop { position: 1; color: Ui.alpha(b.color, Ui.dark ? 0.82 : 0.7) }
                    }
                    PathSvg { path: b.d }
                }
            }
        }
        // Light: a surface-coloured gap above every band, so neighbours read
        // apart without relying on hue. Dark: a lighter rim in the band's own
        // colour instead, so the stack reads lit rather than cut by dark seams.
        Repeater {
            model: chart.bands.length
            Shape {
                readonly property var b: chart.bands[index] || { key: "", color: "transparent", e: "" }
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                opacity: card.focusOn(b.key) ? 1 : 0.16
                ShapePath {
                    strokeColor: Ui.dark ? Ui.alpha(Qt.lighter(b.color, 1.35), 0.9) : Ui.p.surface
                    strokeWidth: 1.5; fillColor: "transparent"
                    capStyle: ShapePath.FlatCap; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: b.e }
                }
            }
        }
        Shape {
            anchors.fill: parent
            visible: chart.bands.length > 0
            preferredRendererType: Shape.CurveRenderer
            // a soft halo under the outline in dark mode
            ShapePath {
                strokeColor: Ui.dark ? Ui.alpha(Ui.p.text, 0.08) : "transparent"; strokeWidth: 7; fillColor: "transparent"
                joinStyle: ShapePath.RoundJoin; capStyle: ShapePath.RoundCap
                PathSvg { path: chart.stack.top }
            }
            ShapePath {
                strokeColor: Ui.alpha(Ui.p.text, Ui.dark ? 0.5 : 0.32); strokeWidth: 1.2; fillColor: "transparent"
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: chart.stack.top }
            }
        }
        }
        ParallelAnimation {
            id: enter
            NumberAnimation { target: plot; property: "grow"; from: 0.9; to: 1; duration: 260; easing.type: Easing.OutCubic }
            NumberAnimation { target: plot; property: "opacity"; from: 0.2; to: 1; duration: 200; easing.type: Easing.OutCubic }
        }

        // the bucket still filling: a dashed lead from the last complete bucket to an open dot
        Shape {
            visible: !!chart.soFar && chart.maxV > 0 && card.lastFull >= 0
            anchors.fill: parent
            ShapePath {
                strokeColor: Ui.p.text_muted; strokeWidth: 1.5; fillColor: "transparent"
                strokeStyle: ShapePath.DashLine; dashPattern: [2.5, 2.5]; capStyle: ShapePath.FlatCap
                PathSvg {
                    path: chart.soFar && card.lastFull >= 0
                          ? "M" + Ui.f1(chart.px(card.lastFull)) + "," + Ui.f1(chart.py(card.plotTot[card.lastFull]))
                            + "L" + Ui.f1(chart.soFar.x - 5) + "," + Ui.f1(chart.soFar.y) : ""
                }
            }
        }
        Rectangle {
            visible: !!chart.soFar && chart.maxV > 0
            x: chart.soFar ? chart.soFar.x - 5 : 0; y: chart.soFar ? chart.soFar.y - 5 : 0
            width: 10; height: 10; radius: 5
            color: Ui.p.surface; border.width: 2; border.color: Ui.p.text_muted
        }
        Txt {
            visible: !!chart.soFarLabel && chart.maxV > 0
            x: chart.soFarLabel ? chart.soFarLabel.x : 0
            y: chart.soFarLabel ? chart.soFarLabel.y : 0
            height: 16
            text: "so far"; font.pixelSize: 11; color: Ui.p.text_muted
        }

        Repeater {
            model: chart.direct.length
            Rectangle {
                readonly property var l: chart.direct[index] || { key: "", name: "", color: "transparent", x: 0, y: 0, w: 0 }
                x: l.x; y: l.y; width: l.w; height: 22; radius: 7
                color: Ui.alpha(Ui.p.surface, 0.94)
                border.color: Ui.p.line_strong
                opacity: card.focusOn(l.key) ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Ui.fast } }
                Rectangle { x: 9; y: 7.5; width: 7; height: 7; radius: 3.5; color: parent.l.color }
                Txt { x: 21; width: parent.width - 28; height: 22; text: parent.l.name; font.pixelSize: 12 }
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
            y: card.hover >= 0 ? chart.py(chart.drawn(card.hover)) - 5 : 0
            width: 10; height: 10; radius: 5
            color: Ui.p.text; border.width: 2; border.color: Ui.p.surface
        }
        function drawn(k) { return k <= card.lastFull ? card.plotTot[k] : card.tot[k] }
        function show(k) {
            card.hover = k
            var rows = card.vis.map(function (s) { return [s, s.arr[k]] }).filter(function (r) { return r[1] > 0 }).sort(function (a, b) { return b[1] - a[1] })
            var shown = rows.slice(0, 8).map(function (r) { return { c: r[0].color, name: card.seriesName(r[0]), v: card.mt.fmt(r[1]) } })
            if (rows.length > 8) shown.push({ c: "transparent", name: "+" + (rows.length - 8) + " more", v: "" })
            tip.title = card.bucketLabel(k)
            tip.value = card.mt.fmt(card.tot[k]) + (k === card.n - 1 && card.tl.partial_last ? " so far" : "")
            tip.rows = shown
            tip.note = k === card.n - 1 && card.tl.partial_last ? card.partialNote() : ""
            tip.showAt(chart, chart.px(k), chart.py(drawn(k)))
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
        width: card.legendFit.w
        spacing: 8
        Repeater {
            model: card.all.length
            T.AbstractButton {
                id: chip
                readonly property var m: card.all[index] || { key: "", color: "transparent", sum: 0 }
                readonly property bool on: !card.hidden[m.key]
                implicitWidth: cr.implicitWidth + 22
                implicitHeight: 30
                hoverEnabled: true
                Accessible.role: Accessible.CheckBox
                Accessible.checkable: true
                Accessible.checked: on
                Accessible.name: card.seriesName(m) + ", " + card.mt.big(m.sum)
                // a hidden series cannot be the focus, so the rest come back at once
                onClicked: { card.toggle(m.key); card.iso = "" }
                onHoveredChanged: card.iso = hovered && on ? m.key : (card.iso === m.key ? "" : card.iso)
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
                        spacing: 7
                        // the dot turns into an eye on hover: what a click will do
                        Item {
                            width: 12; height: 20
                            Rectangle {
                                visible: !chip.hovered
                                anchors.centerIn: parent
                                width: 8; height: 8; radius: 4
                                color: chip.on ? chip.m.color : "transparent"
                                border.width: chip.on ? 0 : 1.5
                                border.color: chip.m.color
                            }
                            Icon {
                                visible: chip.hovered
                                anchors.fill: parent
                                glyph: chip.on ? "\ued1a" : "\ue890"
                                font.pixelSize: 12
                                color: Ui.p.text
                            }
                        }
                        TipTxt { height: 20; text: card.seriesName(chip.m); color: chip.on ? Ui.p.text : Ui.p.text_muted; width: Math.min(implicitWidth, card.legendFit.cap) }
                        Txt { height: 20; text: card.mt.big(chip.m.sum); font.pixelSize: 12; color: Ui.p.text_muted }
                    }
                }
            }
        }
    }
}
