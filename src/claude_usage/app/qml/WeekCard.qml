import QtQuick

// Weekly limit, the binding one: big percentage, reset countdown, pace chart
// and the four numbers that say whether this week will run out.
Card {
    id: card
    property var w: null           // snapshot.limits.weekly
    property real nowMs: Date.now()
    readonly property real natural: head.height + 8 + big.height + 8 + verdict.height + 8 + 190 + 16 + facts.height + 2 * padding

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
        Row {
            anchors.bottom: parent.bottom
            spacing: 8
            Txt { text: card.ok ? Ui.pct(card.w.pct) : "n/a"; display: true; font.pixelSize: 44; font.weight: 600; height: 52; font.features: ({}) }
            Txt { text: "used"; font.pixelSize: 15; color: Ui.p.text_muted; height: 38 }
        }
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
        font.family: Ui.fontUi; font.pixelSize: 13
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
        height: Math.max(150, facts.y - y - 16)
        w: card.w
    }
    Rectangle { y: facts.y - 16; width: parent.width; height: 1; color: Ui.p.line }
    Row {
        id: facts
        y: parent.height - height
        width: parent.width
        height: 44
        readonly property var items: card.ok ? [
            ["Speed vs an even week", card.w.pace != null ? card.w.pace.toFixed(1) + "x" : "n/a"],
            ["Usual by now", Ui.pct(card.w.usual)],
            ["To stay under 100%", card.w.pct < 1 && card.w.daily_budget != null ? Ui.pct(card.w.daily_budget, 1) + " a day" : "n/a"],
            ["Burn rate", card.w.hourly_rate != null ? Ui.pct(card.w.hourly_rate, 1) + " an hour" : "n/a"]
        ] : []
        Repeater {
            model: facts.items
            Column {
                width: facts.width / 4
                spacing: 2
                Txt { width: parent.width - 8; text: modelData[0]; font.pixelSize: 12; color: Ui.p.text_faint }
                Txt { width: parent.width - 8; text: modelData[1]; font.pixelSize: 15; font.weight: 600 }
            }
        }
    }
}
