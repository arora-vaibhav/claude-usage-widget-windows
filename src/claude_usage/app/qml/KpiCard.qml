import QtQuick
import QtQuick.Shapes

// One headline number for the range: value, change against the previous
// equal stretch, and an edge-to-edge sparkline with a soft glow.
Card {
    id: card
    property var k: null             // snapshot.kpis[i]
    property color hue: Ui.p.accent
    property bool dropLast: false    // the bucket still filling is left out of the trend
    property int smoothing: 0
    padding: 0
    implicitHeight: 156

    // the headline counts to a new value when the range or project changes
    // (a null value is a cache hit rate with no prompt tokens: undefined, shown as n/a)
    property real shown: k && k.value != null ? k.value : 0
    Behavior on shown { id: countUp; NumberAnimation { duration: Ui.ms(300); easing.type: Easing.OutCubic } }
    property string shownKey: ""
    onKChanged: {
        if (!k) return
        countUp.enabled = shownKey === k.key
        shown = k.value != null ? k.value : 0
        countUp.enabled = true
        shownKey = k.key
    }

    readonly property var delta: {
        if (!k || k.delta == null || k.prev == null) return null
        var d, num, unit
        if (k.delta_unit === "pts") {
            d = (k.value - k.prev) * 100
            num = Math.abs(d).toFixed(1); unit = ["pt", " points"]
        } else {
            if (!k.prev) return null
            d = (k.value - k.prev) / k.prev * 100
            var m = Math.abs(d), times = k.value / k.prev
            // a five-digit percentage reads as a glitch; past ten times, say how many times
            if (times > 10) { num = Ui.int(times); unit = ["x", " times"] }
            else { num = m < 10 ? m.toFixed(1) : Ui.int(m); unit = ["%", "%"] }
        }
        var flat = Math.abs(d) < 0.05
        // the chip shows an arrow; a screen reader says the word
        var txt = (flat ? "" : d > 0 ? "↑" : "↓") + num + unit[0]
        var spoken = (flat ? "" : d > 0 ? "up " : "down ") + num + unit[1]
        // colour only where the direction is good or bad: spend up is a caution
        var kind = "none"
        if (k.key === "cost" && Math.abs(d) >= 1) kind = d > 0 ? "warn" : "ok"
        if (k.key === "cache_hit" && Math.abs(d) >= 0.05) kind = d > 0 ? "ok" : "warn"
        return { text: txt, spoken: spoken, kind: kind }
    }
    // nothing in the previous stretch to compare with: say so rather than a bare "vs prior 7 days"
    // (a null prev here is a cache hit rate over a stretch with no prompts; other notes do not start "vs prior")
    readonly property string note: !k ? "" : !k.prev && k.delta == null ? k.note.replace(/^vs prior/, "none in the prior") : k.note
    readonly property var values: {
        if (!k) return []
        var v = k.series.slice(0, dropLast && k.series.length > 2 ? k.series.length - 1 : k.series.length)
        if (k.format === "pct") {
            // a rate of 0 means no calls in that bucket, not a 0% rate: hold the last known value
            var seen = v.filter(function (x) { return x > 0 }), last = seen.length ? seen[0] : 0
            v = v.map(function (x) { if (x > 0) last = x; return last })
        }
        // 90 daily points in a 240 px card read as noise; average them in pairs or threes
        if (v.length > 60) {
            var step = Math.ceil(v.length / 45), out = []
            for (var i = 0; i < v.length; i += step) {
                var part = v.slice(i, i + step)
                out.push(part.reduce(function (a, b) { return a + b }, 0) / part.length)
            }
            v = out
        }
        return Ui.smooth(v, smoothing)
    }

    // glow from the bottom edge in the card's colour
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: card.width / 2; centerY: card.height * 1.25
                centerRadius: card.width * 0.85
                focalX: centerX; focalY: centerY
                GradientStop { position: 0; color: Ui.alpha(card.hue, Ui.dark ? 0.22 : 0.10) }
                GradientStop { position: 1; color: Ui.alpha(card.hue, 0) }
            }
            PathSvg { path: Ui.roundRect(1, 1, card.width - 2, card.height - 2, Ui.radius - 1) }
        }
    }

    Column {
        x: 20; y: 18
        width: card.width - 40
        spacing: 4
        Row {
            spacing: 8
            height: 20
            Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; radius: 4; color: card.hue }
            Txt { height: 20; text: card.k ? card.k.label : ""; color: Ui.p.text_muted }
        }
        Txt {
            width: parent.width
            height: 38
            text: card.k ? Ui.fmt(card.k.format, card.k.value == null ? null : card.shown) : ""
            display: true
            fontSizeMode: Text.HorizontalFit
            minimumPixelSize: 18
            font.pixelSize: card.width < 180 ? 22 : 28
            font.weight: 600
        }
        Row {
            spacing: 6
            height: 20
            Rectangle {
                visible: !!card.delta
                anchors.verticalCenter: parent.verticalCenter
                width: dl.implicitWidth + 12; height: 18; radius: 9
                color: !card.delta ? "transparent" : card.delta.kind === "ok" ? Ui.p.ok_bg : card.delta.kind === "warn" ? Ui.p.warn_bg : Ui.p.track
                Txt {
                    id: dl
                    anchors.centerIn: parent
                    text: card.delta ? card.delta.text : ""
                    font.pixelSize: 12; font.weight: 600
                    color: !card.delta ? "transparent" : card.delta.kind === "ok" ? Ui.p.ok : card.delta.kind === "warn" ? Ui.p.warn : Ui.p.text_muted
                }
            }
            Txt {
                visible: card.width >= 150
                height: 20
                width: Math.min(implicitWidth, card.width - 40 - (card.delta ? dl.implicitWidth + 18 : 0))
                text: card.note
                font.pixelSize: 12
                color: Ui.p.text_muted  // faint reads under 4.5:1 on the tinted card
            }
        }
    }

    Item {
        id: spark
        width: card.width; height: 56
        y: card.height - height
        readonly property var pts: {
            var v = card.values, n = v.length
            if (n < 2) return []
            var lo = card.k && card.k.format === "pct" ? Math.min.apply(null, v) : 0
            var hi = Math.max.apply(null, v)
            if (hi - lo <= 0) hi = lo + 1
            // the lowest value sits above the rounded corners
            return v.map(function (x, i) { return [1 + i * (width - 2) / (n - 1), 8 + (1 - (x - lo) / (hi - lo)) * (height - 8 - Ui.radius - 2)] })
        }
        readonly property string line: Ui.pathFwd(pts)
        Shape {
            anchors.fill: parent
            visible: spark.pts.length > 1
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: LinearGradient {
                    x1: 0; y1: 0; x2: 0; y2: spark.height
                    GradientStop { position: 0; color: Ui.alpha(card.hue, Ui.dark ? 0.36 : 0.24) }
                    GradientStop { position: 1; color: Ui.alpha(card.hue, 0) }
                }
                PathSvg {
                    readonly property real r: Ui.radius - 1
                    path: spark.pts.length > 1 ? spark.line + "L" + Ui.f1(spark.width - 1) + "," + Ui.f1(spark.height - 1 - r)
                          + "Q" + Ui.f1(spark.width - 1) + "," + Ui.f1(spark.height - 1) + " " + Ui.f1(spark.width - 1 - r) + "," + Ui.f1(spark.height - 1)
                          + "H" + Ui.f1(1 + r) + "Q1," + Ui.f1(spark.height - 1) + " 1," + Ui.f1(spark.height - 1 - r) + "Z" : ""
                }
            }
            ShapePath {
                strokeColor: Ui.alpha(card.hue, Ui.dark ? 0.22 : 0.0)
                strokeWidth: 6
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: spark.line }
            }
            ShapePath {
                strokeColor: card.hue
                strokeWidth: 2
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: spark.line }
            }
        }
    }

    function showTip() {
        if (!k || !(k.hint || k.prev != null)) return
        tip.title = k.label
        tip.value = Ui.fmt(k.format, k.value)
        tip.rows = k.prev != null ? [{ c: Ui.p.text_faint, name: "Previous period", v: Ui.fmt(k.format, k.prev) }] : []
        tip.note = k.hint || ""
        tip.showAt(card, card.width / 2, 12)
    }
    MouseArea {
        id: hov
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onEntered: card.showTip()
        onExited: if (!card.activeFocus) tip.hide()
    }
    // the readout is reachable from the keyboard too
    activeFocusOnTab: !!k && !!(k.hint || k.prev != null)
    onActiveFocusChanged: activeFocus ? showTip() : tip.hide()
    Focus { show: card.activeFocus; r: Ui.radius }
    ChartTip { id: tip }
    Accessible.role: Accessible.StaticText
    Accessible.name: k ? k.label + " " + Ui.fmt(k.format, k.value) + (delta ? ", " + delta.spoken + " " + k.note : note !== k.note ? ", " + note : "") : ""
}
