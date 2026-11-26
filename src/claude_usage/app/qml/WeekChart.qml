import QtQuick
import QtQuick.Shapes

// This week's weekly-limit use against time: the actual line, a dashed
// projection at the current pace, the usual week as a dotted line, and the
// stretch you would spend locked out shaded.
Item {
    id: c
    property var w: null                 // snapshot.limits.weekly
    readonly property bool ok: !!(w && w.resets_at && w.series && w.series.length)
    readonly property real t1: ok ? Date.parse(w.resets_at) : 1
    readonly property real t0: ok ? (w.window_start ? Date.parse(w.window_start) : t1 - 7 * 86400000) : 0
    readonly property var pts: ok ? w.series.map(function (p) { return [Date.parse(p.t), p.pct] }) : []
    readonly property real tNow: pts.length ? pts[pts.length - 1][0] : t0
    readonly property real nowV: ok ? w.pct : 0
    readonly property bool projecting: ok && w.pct < 1 && w.hourly_rate != null && w.hourly_rate > 0
    readonly property bool hits: projecting && w.hits_before_reset && !!w.eta
    readonly property real tEta: hits ? Date.parse(w.eta) : t1
    readonly property real endV: hits ? 1 : projecting ? Math.min(1, nowV + w.hourly_rate * (t1 - tNow) / 3600000) : nowV

    readonly property real yTop: 22
    readonly property real base: height - 28
    readonly property real xL: 2
    readonly property real xR: width - 46
    function px(t) { return xL + (t - t0) / Math.max(1, t1 - t0) * (xR - xL) }
    function py(v) { return base - Math.min(v, 1.04) * (base - yTop) }
    function tAt(px) { return t0 + (px - xL) / Math.max(1, xR - xL) * (t1 - t0) }

    Accessible.role: Accessible.Chart
    Accessible.name: ok ? "Weekly limit " + Ui.pct(nowV) + " used" + (hits ? ", on pace to reach 100% " + Ui.when(new Date(tEta)) : "") : "Weekly limit chart unavailable"

    FontMetrics { id: fm; font.family: Ui.fontUi; font.pixelSize: 11 }

    readonly property var days: {
        var out = []
        if (!ok) return out
        var d = new Date(t0); d.setHours(24, 0, 0, 0)
        for (; d.getTime() < t1; d.setDate(d.getDate() + 1)) {
            var t = d.getTime()
            out.push({ x: px(t), cx: Math.min(px(Math.min(t + 43200000, t1 - 3600000)), xR - 14), label: Ui.wd[d.getDay()] })
        }
        return out
    }
    readonly property var actual: pts.map(function (p) { return [c.px(p[0]), c.py(p[1])] })
    readonly property var usual: ok && w.usual_series ? w.usual_series.map(function (u) { return [c.px(c.t0 + u.elapsed * (c.t1 - c.t0)), c.py(u.pct)] }) : []

    // Evenly spaced points along a polyline, for dotted and dashed strokes
    // drawn as small marks (crisp on every renderer).
    function sample(poly, step) {
        var out = [], carry = 0
        for (var i = 0; i + 1 < poly.length; i++) {
            var a = poly[i], b = poly[i + 1], len = Math.hypot(b[0] - a[0], b[1] - a[1])
            // Bindings can see a half-updated snapshot for one pass (old t0/t1,
            // new points), which puts a point light years off; skip it.
            if (!(len < 4 * (width + height))) continue
            var s = carry
            for (; s < len; s += step) out.push([a[0] + (b[0] - a[0]) * s / len, a[1] + (b[1] - a[1]) * s / len, Math.atan2(b[1] - a[1], b[0] - a[0])])
            carry = s - len
        }
        return out
    }
    readonly property var usualDots: sample(usual, 5)
    readonly property var dashes: projecting ? sample([[px(tNow), py(nowV)], [px(tEta), py(endV)]], 9) : []

    // Label placement: each label takes the first candidate spot that hits
    // nothing already drawn.
    readonly property var labels: {
        if (!ok || width < 40) return ({})
        var boxes = [], H = 14, out = {}
        function hit(a) {
            if (a.x < 0 || a.x + a.w > c.width || a.y < 0 || a.y + a.h > c.height) return true
            for (var i = 0; i < boxes.length; i++) {
                var b = boxes[i]
                if (a.x < b.x + b.w + 4 && b.x < a.x + a.w + 4 && a.y < b.y + b.h + 2 && b.y < a.y + a.h + 2) return true
            }
            return false
        }
        function place(key, text, cands) {
            var w = fm.advanceWidth(text)
            for (var i = 0; i < cands.length; i++) {
                var k = cands[i], bx = k[2] === "end" ? k[0] - w : k[2] === "middle" ? k[0] - w / 2 : k[0]
                var box = { x: bx, y: k[1] - H / 2, w: w, h: H }
                if (!hit(box)) { boxes.push(box); out[key] = { x: box.x, y: box.y, text: text }; return }
            }
        }
        function line(poly, step) { sample(poly, step).forEach(function (q) { boxes.push({ x: q[0] - 1, y: q[1] - 1, w: 2, h: 2 }) }) }
        boxes.push({ x: xR + 6, y: py(1) - 7, w: 36, h: 14 }, { x: xR + 6, y: py(0.5) - 7, w: 36, h: 14 })
        line(actual, 4); line(usual, 6)
        var nx = px(tNow), ny = py(nowV)
        if (projecting) line([[nx, ny], [px(tEta), py(endV)]], 5)
        boxes.push({ x: nx - 7, y: ny - 7, w: 14, h: 14 })
        if (hits) {
            var ex = px(tEta), ey = py(1), d = new Date(tEta)
            boxes.push({ x: ex - 6, y: ey - 6, w: 12, h: 12 })
            place("eta", "100% " + Ui.wd[d.getDay()] + " " + Ui.clock(d), [[ex, ey - 13, "middle"], [ex - 10, ey - 13, "end"], [ex + 10, ey + 16, "start"], [ex - 10, ey + 16, "end"]])
            place("limited", "Limited " + Ui.span(t1 - tEta), [[(ex + xR) / 2, base - 12, "middle"], [(ex + xR) / 2, yTop + 12, "middle"], [xR - 6, base - 12, "end"]])
        }
        place("now", "Now " + Ui.pct(nowV), [[nx - 10, ny, "end"], [nx + 10, ny + 14, "start"], [nx - 8, ny - 14, "end"], [nx - 8, ny + 16, "end"], [nx, ny - 15, "middle"]])
        if (usual.length > 2) {
            var last = usual[usual.length - 1], mid = usual[Math.floor(usual.length * 0.72)]
            place("usual", "Usual week", [[last[0] - 4, last[1] + 15, "end"], [last[0] - 4, last[1] - 12, "end"], [mid[0], mid[1] - 12, "middle"], [mid[0], mid[1] + 15, "middle"]])
        }
        return out
    }

    // ---- grid and axes
    Rectangle { x: xL; width: xR - xL; y: Math.round(py(0.5)); height: 1; color: Ui.p.line; visible: c.ok }
    Rectangle { x: xL; width: xR - xL; y: Math.round(py(1)); height: 1; color: Ui.p.line_strong; visible: c.ok }
    Rectangle { x: xL; width: xR - xL; y: Math.round(base); height: 1; color: Ui.p.line_strong; visible: c.ok }
    Txt { visible: c.ok; x: xR + 8; y: py(1) - 8; height: 16; text: "100%"; font.pixelSize: 11; color: Ui.p.text_faint }
    Txt { visible: c.ok; x: xR + 8; y: py(0.5) - 8; height: 16; text: "50%"; font.pixelSize: 11; color: Ui.p.text_faint }
    Repeater {
        model: c.days
        Item {
            Rectangle { x: modelData.x; y: c.base; width: 1; height: 4; color: Ui.p.line_strong }
            Txt { x: modelData.cx - width / 2; y: c.base + 8; height: 16; text: modelData.label; font.pixelSize: 11; color: Ui.p.text_faint }
        }
    }

    // ---- locked-out stretch
    Rectangle {
        visible: c.hits
        x: c.px(c.tEta); width: Math.max(0, c.xR - x)
        y: c.yTop - 6; height: c.base - c.yTop + 6
        radius: 6
        color: Ui.p.bad_bg
    }

    // ---- usual week (dotted)
    Repeater {
        model: c.usualDots
        Rectangle { x: modelData[0] - 1; y: modelData[1] - 1; width: 2.2; height: 2.2; radius: 1.1; color: Ui.p.text_faint }
    }

    // ---- actual
    Shape {
        anchors.fill: parent
        visible: c.actual.length > 1
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: LinearGradient {
                x1: 0; y1: c.yTop; x2: 0; y2: c.base
                GradientStop { position: 0; color: Ui.alpha(Ui.p.accent, Ui.dark ? 0.34 : 0.22) }
                GradientStop { position: 1; color: Ui.alpha(Ui.p.accent, 0) }
            }
            PathSvg {
                path: c.actual.length > 1 ? Ui.linePath(c.actual) + "L" + Ui.f1(c.actual[c.actual.length - 1][0]) + "," + Ui.f1(c.base) + "L" + Ui.f1(c.actual[0][0]) + "," + Ui.f1(c.base) + "Z" : ""
            }
        }
        ShapePath {
            strokeColor: Ui.p.accent; strokeWidth: 2; fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin; capStyle: ShapePath.RoundCap
            PathSvg { path: Ui.linePath(c.actual) }
        }
    }

    // ---- projection (dashed)
    Repeater {
        model: c.dashes
        Rectangle {
            x: modelData[0]; y: modelData[1] - 1
            width: 5; height: 2; radius: 1
            transformOrigin: Item.Left
            rotation: modelData[2] * 180 / Math.PI
            color: c.hits ? Ui.p.bad_fill : Ui.p.accent
        }
    }

    // ---- markers
    Rectangle {
        visible: c.hits
        x: c.px(c.tEta) - 6; y: c.py(1) - 6; width: 12; height: 12; radius: 6
        color: Ui.p.bad_fill; border.width: 2.5; border.color: Ui.p.surface
    }
    Rectangle {
        visible: c.ok
        x: c.px(c.tNow) - 7; y: c.py(c.nowV) - 7; width: 14; height: 14; radius: 7
        color: Ui.p.accent; border.width: 3; border.color: Ui.p.surface
    }

    // ---- labels
    Repeater {
        model: [["eta", Ui.p.text, 600], ["now", Ui.p.text, 600], ["usual", Ui.p.text_muted, 400], ["limited", Ui.p.bad, 600]]
        Txt {
            readonly property var l: c.labels[modelData[0]]
            visible: !!l
            x: l ? l.x : 0; y: l ? l.y : 0; height: 14
            text: l ? l.text : ""
            font.pixelSize: 11
            font.weight: modelData[2]
            color: modelData[1]
        }
    }

    // ---- hover
    Rectangle { id: cross; visible: false; width: 1; y: c.yTop - 6; height: c.base - c.yTop + 6; color: Ui.alpha(Ui.p.text, 0.35) }
    Rectangle { id: hdot; visible: cross.visible; width: 10; height: 10; radius: 5; color: Ui.p.text; border.width: 2; border.color: Ui.p.surface }
    ChartTip { id: tip }
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        enabled: c.ok
        onExited: { cross.visible = false; tip.hide() }
        onPositionChanged: function (m) {
            var t = c.tAt(m.x)
            if (t < c.t0 || t > c.t1 || m.x > c.xR + 4) { cross.visible = false; tip.hide(); return }
            var e = (t - c.t0) / (c.t1 - c.t0), v, title, note = ""
            if (t <= c.tNow) {
                var best = c.pts[0]
                for (var i = 0; i < c.pts.length; i++) if (Math.abs(c.pts[i][0] - t) < Math.abs(best[0] - t)) best = c.pts[i]
                t = best[0]; v = best[1]
                title = Ui.when(new Date(t))
            } else if (c.projecting) {
                v = Math.min(1, c.nowV + w.hourly_rate * (t - c.tNow) / 3600000)
                title = Ui.when(new Date(t)) + ", projected"
                note = v >= 1 ? "At the current pace you would be at the limit until it resets." : "If you keep the pace of the last day."
            } else { cross.visible = false; tip.hide(); return }
            var u = null, us = w.usual_series || []
            for (var j = 0; j + 1 < us.length; j++) if (us[j].elapsed <= e && us[j + 1].elapsed >= e) {
                var f = (e - us[j].elapsed) / Math.max(1e-9, us[j + 1].elapsed - us[j].elapsed)
                u = us[j].pct + (us[j + 1].pct - us[j].pct) * f
            }
            cross.x = c.px(t); cross.visible = true
            hdot.x = c.px(t) - 5; hdot.y = c.py(v) - 5
            tip.title = title
            tip.value = Ui.pct(v) + " used"
            tip.rows = u == null ? [] : [{ c: Ui.p.text_faint, name: "Usual week by then", v: Ui.pct(u) }]
            tip.note = note
            tip.showAt(c, c.px(t), c.py(v))
        }
    }

    Txt {
        visible: !c.ok
        anchors.centerIn: parent
        text: "No weekly history yet"
        color: Ui.p.text_faint
    }
}
