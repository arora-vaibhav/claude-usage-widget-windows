pragma Singleton
import QtQuick

// Theme tokens (set from backend.theme by Main.qml) and the formatters every
// page shares, so a number reads the same wherever it appears.
QtObject {
    id: ui
    property var t: backend.theme
    readonly property bool dark: !!t.dark
    readonly property var p: t.p || ({})
    readonly property var s: t.s || []
    readonly property color other: t.other || "#9A9891"
    readonly property string fontUi: t.fontUi || "Segoe UI"
    readonly property string fontDisplay: t.fontDisplay || "Segoe UI"
    // Segoe UI Variable: the Text and Display families only carry Regular and
    // Bold, so a 600 weight came out Bold. One family with a weight and an
    // optical size axis gives the real Semibold at either size.
    function axes(display) { return t.opsz ? { "opsz": display ? 36 : 10.5 } : ({}) }
    readonly property string icons: "Segoe Fluent Icons"
    readonly property real caution: t.caution || 0.75
    readonly property real critical: t.critical || 0.9

    readonly property int radius: 16
    readonly property int pad: 24
    readonly property int gap: 16
    readonly property int fast: 140

    readonly property var wd: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    readonly property var mon: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    // -1 is "Other", -2 the days that only have the old daily totals.
    function seriesColor(i) { return i >= 0 && i < s.length ? s[i] : i === -2 ? mix(other, p.surface || "#888", 0.45) : other }
    // Colour index = all-time cost rank, so a series ranked past the palette
    // would draw grey like "Other". Each such entity gets a palette slot that
    // it keeps across ranges and restarts (saved as the series_slots setting)
    // unless another series in the same chart already shows it; a new slot
    // prefers colours no other entity remembers. Lists (sidebar, sessions,
    // models) then colour only what the chart draws, and everything else in
    // the colour of "Other", so a colour always means the same band.
    property var slots: null
    function fixColors(snap) {
        if (!snap || !snap.timeline) return snap
        if (!slots) {
            try { slots = JSON.parse(backend.settings.series_slots || "{}") } catch (e) { slots = {} }
        }
        var n = s.length || 8, changed = false, inChart = {}
        function remap(g, pre) {
            var used = {}, taken = {}, map = {}
            g.colors.forEach(function (c) { if (c >= 0 && c < n) used[c] = true })
            for (var k in slots) if (k.indexOf(pre) === 0 && g.keys.indexOf(k.slice(pre.length)) < 0) taken[slots[k]] = true
            var past = g.keys.map(function (k, i) { return [g.colors[i], k] }).filter(function (x) { return x[0] >= n })
                .sort(function (a, b) { return a[0] - b[0] })
            past.forEach(function (x) {
                var m = slots[pre + x[1]]
                if (m >= 0 && m < n && !used[m]) { map[x[1]] = m; used[m] = true }
            })
            past.forEach(function (x) {
                if (x[1] in map) return
                var pick = -1
                for (var i = 0; i < n; i++) if (!used[i] && (pick < 0 || taken[pick] && !taken[i])) pick = i
                if (pick < 0) return
                map[x[1]] = pick
                used[pick] = true
                slots[pre + x[1]] = pick
                changed = true
            })
            g.colors = g.keys.map(function (k, i) { return k in map ? map[k] : g.colors[i] })
            g.keys.forEach(function (k, i) { inChart[pre + k] = g.colors[i] })
        }
        remap(snap.timeline.by.project, "p:")
        remap(snap.timeline.by.model, "m:")
        function carry(list, field, pre) {
            (list || []).forEach(function (o) { var c = inChart[pre + o[field]]; o.color = c === undefined ? -1 : c })
        }
        carry(snap.projects, "name", "p:"); carry(snap.sessions, "project", "p:"); carry(snap.top_sessions, "project", "p:")
        carry(snap.models, "name", "m:")
        if (changed) backend.setSetting("series_slots", JSON.stringify(slots))
        return snap
    }
    function alpha(c, a) { var q = Qt.darker(c, 1.0); return Qt.rgba(q.r, q.g, q.b, a) }
    function mix(a, b, t) { var x = Qt.darker(a, 1.0), y = Qt.darker(b, 1.0); return Qt.rgba(x.r + (y.r - x.r) * t, x.g + (y.g - x.g) * t, x.b + (y.b - x.b) * t, 1) }

    // ---- numbers
    function grp(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",") }
    function int(n) { return n == null ? "n/a" : (n < 0 ? "-" : "") + grp(Math.round(Math.abs(n))) }
    function money(n) {
        if (n == null) return "n/a"
        if (n === 0) return "$0"
        if (n < 0.005) return "<$0.01"
        if (n >= 99.995) return "$" + grp(Math.round(n))
        return "$" + n.toFixed(2)
    }
    function cents(n) {
        if (n == null) return "n/a"
        if (n > 0 && n < 0.005) return "<$0.01"
        var s = n.toFixed(2).split(".")
        return "$" + grp(s[0]) + "." + s[1]
    }
    // two decimals so a 2.5 step reads $1.25k and $12.5, not $1.3k and $13
    function moneyAxis(n) { return n === 0 ? "$0" : n >= 1000 ? "$" + trim(n / 1000, 2) + "k" : "$" + trim(n, n < 100 ? 2 : 0) }
    function trim(n, d) { return String(+n.toFixed(d)) }
    function tok(n) {
        if (n == null) return "n/a"
        var a = Math.abs(n)
        if (a < 1000) return String(Math.round(n))
        var u = a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : [1e3, "k"]
        var v = n / u[0]
        return (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)) + u[1]
    }
    function tokAxis(n) { return n === 0 ? "0" : tok(n).replace(/\.0+(?=[kMB]$)/, "").replace(/(\.\d*?)0+(?=[kMB]$)/, "$1") }
    function dur(ms) {
        if (ms == null) return "n/a"
        var s = Math.round(ms / 1000)
        if (s < 60) return s + "s"
        var m = Math.round(s / 60)
        if (m < 60) return m + "m"
        return Math.floor(m / 60) + "h " + (m % 60) + "m"
    }
    function durAxis(ms) { return ms === 0 ? "0h" : ms >= 3600000 ? trim(ms / 3600000, 1) + "h" : Math.round(ms / 60000) + "m" }
    function pct(x, d) { return x == null ? "n/a" : (x * 100).toFixed(d == null ? 0 : d) + "%" }
    function share(x) { return x > 0 && x < 0.0005 ? "<0.1%" : pct(x, 1) }
    function fmt(kind, v) {
        if (v == null) return "n/a"
        return kind === "usd" ? money(v) : kind === "tokens" ? tok(v) : kind === "duration" ? dur(v)
             : kind === "pct" ? pct(v, 1) : int(v)
    }
    // "in the last 7 days", or "over all time" for the all range.
    function inRange(r) { return !r ? "in this range" : r.key === "all" ? "over all time" : "in the " + r.label }
    function plural(n, w) { return int(n) + " " + w + (n === 1 ? "" : "s") }

    // ---- time
    function date(iso) { return iso ? new Date(iso) : null }
    function clock(d) { return (d.getHours() % 12 || 12) + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes() + (d.getHours() < 12 ? " AM" : " PM") }
    function hourLabel(h) { return (h % 12 || 12) + (h < 12 ? " AM" : " PM") }
    function md(d) { return mon[d.getMonth()] + " " + d.getDate() }
    function wmd(d) { return wd[d.getDay()] + ", " + md(d) }
    function when(d) { return wmd(d) + ", " + clock(d) }
    function whenShort(d) { return md(d) + ", " + clock(d) }
    function span(ms) {
        var m = Math.max(0, Math.round(ms / 60000)), dd = Math.floor(m / 1440), hh = Math.floor(m % 1440 / 60)
        return dd ? dd + "d " + hh + "h" : hh ? hh + "h " + (m % 60) + "m" : (m % 60) + "m"
    }
    function ago(ms) {
        var s = Math.max(0, Math.round(ms / 1000))
        return s < 60 ? s + "s ago" : s < 3600 ? Math.floor(s / 60) + "m ago" : s < 86400 ? Math.floor(s / 3600) + "h ago" : Math.floor(s / 86400) + "d ago"
    }

    // ---- chart helpers
    function niceStep(max, n) {
        if (max <= 0) return 1
        var raw = max / n, mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), r = raw / mag
        return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * mag
    }
    function timeStep(max, n) {
        var c = [60, 300, 600, 900, 1800, 3600, 7200, 18000, 36000, 72000, 180000, 360000, 720000, 1800000]
        for (var i = 0; i < c.length; i++) if (c[i] * 1000 >= max / n) return c[i] * 1000
        return c[c.length - 1] * 1000
    }
    function f1(x) { return x.toFixed(1) }
    // Monotone cubic (as d3 curveMonotoneX): flat at peaks, never overshoots a point.
    function monoSegs(p) {
        var n = p.length, m = [], out = []
        if (n < 2) return out
        function sl(i) { var dx = p[i + 1][0] - p[i][0]; return dx ? (p[i + 1][1] - p[i][1]) / dx : 0 }
        if (n === 2) { m = [sl(0), sl(0)] }
        else {
            for (var i = 1; i < n - 1; i++) {
                var s0 = sl(i - 1), s1 = sl(i)
                m[i] = (Math.sign(s0) + Math.sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), Math.abs(s0 + s1) / 4) || 0
            }
            m[0] = sl(0); m[n - 1] = sl(n - 2)
        }
        for (var j = 0; j < n - 1; j++) {
            var dx = (p[j + 1][0] - p[j][0]) / 3
            out.push([p[j][0], p[j][1], p[j][0] + dx, p[j][1] + m[j] * dx, p[j + 1][0] - dx, p[j + 1][1] - m[j + 1] * dx, p[j + 1][0], p[j + 1][1]])
        }
        return out
    }
    function pathFwd(p) {
        if (!p.length) return ""
        var s = monoSegs(p), d = "M" + f1(p[0][0]) + "," + f1(p[0][1])
        for (var i = 0; i < s.length; i++) d += "C" + f1(s[i][2]) + "," + f1(s[i][3]) + " " + f1(s[i][4]) + "," + f1(s[i][5]) + " " + f1(s[i][6]) + "," + f1(s[i][7])
        return d
    }
    // The same monotone curve through equally spaced values, sampled m times
    // per gap (knots included). Between two knots it stays between their
    // values, so a curve of non-negative values never dips below zero.
    function monoFill(v, m) {
        var n = v.length, d = [], t = [], out = [], i
        if (n < 2) return v.slice()
        for (i = 0; i < n - 1; i++) d.push(v[i + 1] - v[i])
        for (i = 0; i < n; i++)
            t.push(i === 0 ? d[0] : i === n - 1 ? d[n - 2]
                   : (Math.sign(d[i - 1]) + Math.sign(d[i])) * Math.min(Math.abs(d[i - 1]), Math.abs(d[i]), Math.abs(d[i - 1] + d[i]) / 4) || 0)
        for (i = 0; i < n - 1; i++)
            for (var j = 0; j < m; j++) {
                var u = j / m, u2 = u * u, u3 = u2 * u
                out.push((2 * u3 - 3 * u2 + 1) * v[i] + (u3 - 2 * u2 + u) * t[i] + (3 * u2 - 2 * u3) * v[i + 1] + (u3 - u2) * t[i + 1])
            }
        out.push(v[n - 1])
        return out
    }
    function linePath(p) {
        var d = ""
        for (var i = 0; i < p.length; i++) d += (i ? "L" : "M") + f1(p[i][0]) + "," + f1(p[i][1])
        return d
    }
    function roundRect(x, y, w, h, r) {
        return "M" + f1(x + r) + "," + f1(y) + "H" + f1(x + w - r) + "Q" + f1(x + w) + "," + f1(y) + " " + f1(x + w) + "," + f1(y + r)
             + "V" + f1(y + h - r) + "Q" + f1(x + w) + "," + f1(y + h) + " " + f1(x + w - r) + "," + f1(y + h)
             + "H" + f1(x + r) + "Q" + f1(x) + "," + f1(y + h) + " " + f1(x) + "," + f1(y + h - r)
             + "V" + f1(y + r) + "Q" + f1(x) + "," + f1(y) + " " + f1(x + r) + "," + f1(y) + "Z"
    }
    function smooth(v, r) {
        if (!r) return v
        return v.map(function (_, i) {
            var a = 0, n = 0
            for (var j = Math.max(0, i - r); j <= Math.min(v.length - 1, i + r); j++) { a += v[j]; n++ }
            return a / n
        })
    }
    function hexPath(cx, cy, r) {
        var d = ""
        for (var k = 0; k < 6; k++) {
            var a = Math.PI / 3 * k - Math.PI / 2
            d += (k ? "L" : "M") + f1(cx + r * Math.cos(a)) + "," + f1(cy + r * Math.sin(a))
        }
        return d + "Z"
    }
}
