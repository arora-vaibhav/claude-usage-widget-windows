import QtQuick

// The 5-hour window, scoped weekly limits, extra usage and where the weekly
// limit went. Missing values say so; they never read as 0%.
Card {
    id: card
    property var limits: null
    property string plan: ""         // status.plan, when the data layer knows it
    readonly property real natural: col.implicitHeight + 30 + 2 * padding

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
        // never a 0% meter for a window that has not started or does not exist
        if (L.session && (L.session.resets_at || L.session.pct > 0)) {
            var r = Ui.date(L.session.resets_at)
            out.push({ label: "5-hour session", value: Ui.pct(L.session.pct), meter: L.session.pct, sub: r ? "Resets " + Ui.when(r) : "" })
        } else if (L.session) {
            out.push({ label: "5-hour session", pill: "Not started", sub: "Starts on your next prompt." })
        } else {
            out.push({ label: "5-hour session", pill: card.plan === "Ultra" ? "None on Ultra" : "Not reported",
                       sub: L.session_note || "Your plan does not report a 5-hour limit right now." })
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
        y: 30
        width: parent.width
        spacing: 9
        Repeater {
            model: card.rows
            Item {
                width: col.width
                height: row.y + row.implicitHeight
                Rectangle { visible: index > 0; width: parent.width; height: 1; color: Ui.p.line }
                Column {
                    id: row
                    y: index ? 10 : 0
                    width: parent.width
                    spacing: 4
                    Item {
                        width: parent.width; height: 22
                        // the label takes the row unless a value or pill sits at its end
                        TipTxt { height: 22; text: modelData.label; font.weight: 600; width: parent.width - (val.visible ? val.implicitWidth + 12 : tag.visible ? tag.width + 12 : 0) }
                        Txt { id: val; visible: modelData.value !== undefined; anchors.right: parent.right; height: 22; text: modelData.value || ""; font.weight: 600 }
                        Pill { id: tag; visible: !!modelData.pill; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: modelData.pill || ""; kind: modelData.pillKind || "none" }
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
                        lineHeight: 1.1
                    }
                }
            }
        }
    }
}
