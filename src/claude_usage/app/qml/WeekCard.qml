import QtQuick

// Weekly limit, the binding one: big percentage, reset countdown, pace chart
// and the four numbers that say whether this week will run out.
Card {
    id: card
    property var w: null           // snapshot.limits.weekly
    property real nowMs: Date.now()
    property bool stale: false
    // kept short so the KPI row still shows below the limits at 1280x820
    readonly property real natural: head.height + 8 + big.height + 8 + verdict.height + 8 + 140 + 16 + facts.height + 2 * padding

    readonly property bool ok: !!w
    readonly property var reset: ok && w.resets_at ? Ui.date(w.resets_at) : null
    readonly property bool projecting: ok && w.pct < 1 && w.hourly_rate != null && w.hourly_rate > 0
    readonly property var status: {
        if (!ok) return ["none", "Not reported"]
        if (w.pct >= 1) return ["bad", "Limit reached"]
        if (w.hits_before_reset && w.eta) return ["bad", "On pace to hit the limit"]
        if (w.pct >= Ui.caution) return ["warn", "Close to the limit"]
        return ["ok", "Comfortably under"]
    }

    Item {
        id: head
        width: parent.width; height: 24
        Txt { height: 24; text: "Weekly limit, all models"; color: Ui.p.text_muted }
        Pill { anchors.right: parent.right; kind: card.status[0]; text: card.status[1] }
    }
    Item {
        id: big
        y: head.height + 8
        width: parent.width; height: 52
        Txt {
            id: pctText
            anchors.bottom: parent.bottom
            text: card.ok ? Ui.pct(card.w.pct) : "n/a"; display: true; font.pixelSize: 44; font.weight: 600; height: 52
            // values from an old reading step back; the stale pill says why
            color: card.stale ? Ui.p.text_muted : Ui.p.text
        }
        Txt { x: pctText.implicitWidth + 8; anchors.baseline: pctText.baseline; text: "used"; font.pixelSize: 15; color: Ui.p.text_muted; verticalAlignment: Text.AlignTop }
        Column {
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: 4
            visible: !!card.reset
            Txt {
                anchors.right: parent.right
                text: card.reset ? "Resets in " + Ui.span(card.reset.getTime() - card.nowMs) : ""
                font.pixelSize: 15; font.weight: 600
            }
            Txt { anchors.right: parent.right; text: card.reset ? Ui.when(card.reset) : ""; font.pixelSize: 12; color: Ui.p.text_muted }
        }
    }
    Text {
        id: verdict
        y: big.y + big.height + 8
        width: parent.width
        wrapMode: Text.WordWrap
        textFormat: Text.StyledText
        font.family: Ui.fontUi; font.pixelSize: 13; font.variableAxes: Ui.axes(false)
        color: Ui.p.text_muted
        linkColor: Ui.p.text
        text: {
            if (!card.ok) return "Your plan has not reported a weekly limit yet."
            if (card.w.pct >= 1) return "You are at the weekly limit until it resets."
            if (card.w.hits_before_reset && card.w.eta) {
                var e = Ui.date(card.w.eta)
                return "At this pace you reach 100% on <b><font color='" + Ui.p.text + "'>" + Ui.when(e) + "</font></b>"
                     + (card.w.locked_hours != null ? ", " + Ui.span(card.w.locked_hours * 3600000) + " before the reset." : ".")
            }
            if (card.projecting && card.reset) {
                var end = Math.min(1, card.w.pct + card.w.hourly_rate * Math.max(0, card.reset.getTime() - card.nowMs) / 3600000)
                return "At this pace you finish the week near <b><font color='" + Ui.p.text + "'>" + Ui.pct(end) + "</font></b>."
            }
            return "Not enough use this week to project a pace."
        }
    }
    WeekChart {
        id: chart
        y: verdict.y + verdict.height + 8
        width: parent.width
        height: Math.max(120, facts.y - y - 16)
        w: card.w
    }
    Rectangle { y: facts.y - 16; width: parent.width; height: 1; color: Ui.p.line }
    Grid {
        id: facts
        y: parent.height - height
        width: parent.width
        // four across down to a narrow card, else two by two
        readonly property int cols: width >= 340 ? 4 : 2
        columns: cols
        rowSpacing: 6
        readonly property var items: card.ok ? [
            ["Pace vs even", card.w.pace != null ? card.w.pace.toFixed(1) + "x" : "n/a", "Speed against an even spread over the week"],
            ["Usual by now", Ui.pct(card.w.usual), "Median of past weeks at this point"],
            ["Daily budget", card.w.pct < 1 && card.w.daily_budget != null ? Ui.pct(card.w.daily_budget, 1) : "n/a", "Use per day that stays under 100%"],
            ["Burn per hour", card.w.hourly_rate != null ? Ui.pct(card.w.hourly_rate, 1) : "n/a", "Share of the weekly limit used per hour"]
        ] : []
        Repeater {
            model: facts.items
            Column {
                width: facts.width / facts.cols
                height: 42
                spacing: 2
                Accessible.role: Accessible.StaticText
                Accessible.name: modelData[0] + " " + modelData[1] + ". " + modelData[2]
                Txt { width: parent.width - 8; text: modelData[0]; font.pixelSize: 12; color: Ui.p.text_faint }
                Txt { width: parent.width - 8; text: modelData[1]; font.pixelSize: 14; font.weight: 600 }
            }
        }
    }
}
