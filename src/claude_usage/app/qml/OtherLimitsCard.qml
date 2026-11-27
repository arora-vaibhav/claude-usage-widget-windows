import QtQuick

// The 5-hour window, scoped weekly limits, extra usage and where the weekly
// limit went. Missing values say so; they never read as 0%.
Card {
    id: card
    property var limits: null
    readonly property real natural: col.implicitHeight + 24 + 8 + 2 * padding

    // The API sends codes like "out_of_credits"; show them as a short sentence.
    function reasonText(code) {
        var known = { out_of_credits: "Out of credits.", org_disabled: "Turned off by your organization." }
        var t = known[code] || String(code).replace(/_/g, " ")
        return known[code] ? t : t.charAt(0).toUpperCase() + t.slice(1) + "."
    }
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
            var s = ex.enabled ? [cap ? "Pays past the limit, up to " + cap + " a month." : "Pays past the limit."]
                               : [ex.reason ? card.reasonText(ex.reason) : "", cap ? "Cap " + cap + " a month." : ""]
            // the billing note only matters once it can charge
            if (ex.enabled || ex.used)
                s.push((ex.used ? ex.currency + " " + ex.used.toFixed(2) : "Nothing") + " used this month, billed in " + ex.currency + " apart from the API-equivalent costs here.")
            out.push({ label: "Extra usage", pill: ex.enabled ? "On" : "Off", pillKind: ex.enabled ? "ok" : "none",
                       sub: s.filter(function (x) { return x }).join(" ") })
        }
        // one surface with all of the use says nothing the weekly card does not
        var sf = L.surfaces.filter(function (x) { return x.pct_of_limit > 0 })
        if (sf.length > 1 || sf.length === 1 && sf[0].pct < 0.995)
            out.push({ label: "Where the weekly limit went", sub: sf.map(function (x) { return x.name + " " + Ui.pct(x.pct_of_limit) }).join(", ") + "." })
        return out
    }

    Txt { id: title; height: 24; text: "Other limits"; color: Ui.p.text_muted }
    Column {
        id: col
        y: 32
        width: parent.width
        Repeater {
            model: card.rows
            Column {
                width: col.width
                bottomPadding: index < card.rows.length - 1 ? 10 : 0
                spacing: 5
                Rectangle { visible: index > 0; width: parent.width; height: 1; color: Ui.p.line }
                Item { width: 1; height: index ? 3 : 0 }
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
    }
}
