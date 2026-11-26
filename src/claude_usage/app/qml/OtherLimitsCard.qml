import QtQuick

// The 5-hour window, scoped weekly limits, extra usage and where the weekly
// limit went. Missing values say so; they never read as 0%.
Card {
    id: card
    property var limits: null
    readonly property real natural: col.implicitHeight + 24 + 8 + 2 * padding

    function meterColor(v) { return v >= 1 ? Ui.p.bad_fill : v >= Ui.critical ? Ui.p.bad_fill : v >= Ui.caution ? Ui.p.warn_fill : Ui.p.accent }

    readonly property var rows: {
        var L = limits, out = []
        if (!L) return out
        if (L.session) {
            var r = Ui.date(L.session.resets_at)
            out.push({ label: "5-hour session", value: Ui.pct(L.session.pct), meter: L.session.pct, sub: r ? "Resets " + Ui.when(r) : "" })
        } else {
            out.push({ label: "5-hour session", pill: "Not reported", sub: L.session_note || "Your plan does not report a 5-hour limit right now." })
        }
        L.scoped.forEach(function (sc) {
            var sub = (sc.active ? "In use this week" : "Not in use this week")
            if (sc.allowance != null) sub += ". Can use up to " + Ui.pct(sc.allowance) + " of the weekly limit"
            out.push({ label: sc.name + ", weekly", value: Ui.pct(sc.pct), meter: sc.pct, sub: sub + "." })
        })
        var ex = L.extra_usage
        if (ex) {
            var cap = ex.monthly_limit != null ? ex.currency + " " + ex.monthly_limit.toFixed(2) : null
            var used = ex.used ? ex.currency + " " + ex.used.toFixed(2) + " used this month. " : "Nothing used this month. "
            var s = ex.enabled ? (cap ? "Pays past the limit, up to " + cap + " a month. " : "Pays past the limit. ") : (cap ? "Monthly cap " + cap + ". " : "")
            out.push({ label: "Extra usage", pill: ex.enabled ? "On" : "Off", pillKind: ex.enabled ? "ok" : "none",
                       sub: s + used + "Billed in " + ex.currency + ", separate from the API-equivalent USD in this app." + (ex.reason ? " " + ex.reason : "") })
        }
        return out
    }
    readonly property var surfaces: limits ? limits.surfaces.filter(function (s) { return s.pct_of_limit > 0 }) : []

    Txt { id: title; height: 24; text: "Other limits"; color: Ui.p.text_muted }
    Column {
        id: col
        y: 32
        width: parent.width
        Repeater {
            model: card.rows
            Column {
                width: col.width
                bottomPadding: 12
                spacing: 6
                Rectangle { visible: index > 0; width: parent.width; height: 1; color: Ui.p.line }
                Item { width: 1; height: index ? 4 : 0 }
                Item {
                    width: parent.width; height: 24
                    Txt { height: 24; text: modelData.label; font.weight: 600; width: parent.width - 90 }
                    Txt { visible: modelData.value !== undefined; anchors.right: parent.right; height: 24; text: modelData.value || ""; font.weight: 600 }
                    Pill { visible: !!modelData.pill; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: modelData.pill || ""; kind: modelData.pillKind || "none" }
                }
                Rectangle {
                    visible: modelData.meter !== undefined
                    width: parent.width; height: 6; radius: 3; color: Ui.p.track
                    Rectangle {
                        width: Math.max(modelData.meter > 0 ? 6 : 0, parent.width * Math.min(1, modelData.meter || 0))
                        height: 6; radius: 3
                        color: card.meterColor(modelData.meter || 0)
                    }
                }
                Txt {
                    visible: modelData.sub !== ""
                    width: parent.width
                    text: modelData.sub
                    wrapMode: Text.WordWrap; elide: Text.ElideNone
                    font.pixelSize: 12; color: Ui.p.text_muted
                    lineHeight: 1.15
                }
            }
        }
        Column {
            visible: card.surfaces.length > 0
            width: col.width
            topPadding: 12
            spacing: 8
            Rectangle { width: parent.width; height: 1; color: Ui.p.line }
            Txt { height: 24; text: "Where the weekly limit went"; font.weight: 600 }
            Flow {
                width: parent.width
                spacing: 8
                Repeater { model: card.surfaces; Tag { text: modelData.name + " " + Ui.pct(modelData.pct_of_limit) } }
            }
        }
    }
}
