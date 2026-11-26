import QtQuick

// How am I doing right now, and over this range.
Page {
    id: page
    property real nowMs: Date.now()
    signal openSessions()

    readonly property var st: snap ? snap.status : null
    readonly property var range: snap ? snap.range : null
    readonly property bool signedOut: !!st && st.state === "signed_out"
    readonly property bool stale: !!st && st.state === "stale"
    readonly property bool empty: !!snap && snap.sessions_total === 0
    readonly property var limitsAsOf: st && st.limits_as_of ? Ui.date(st.limits_as_of) : null

    title: snap && snap.project ? snap.project : "All projects"
    subtitle: {
        if (!snap) return "Loading"
        var r = snap.range, txt = Ui.plural(snap.sessions_total, "session")
        if (r.key === "all") return txt + " since " + Ui.md(Ui.date(r.from)) + ", " + Ui.date(r.from).getFullYear()
        var f = Ui.date(r.from), t = Ui.date(r.to)
        return txt + " in the " + r.label + (r.key === "24h" ? "" : ", " + Ui.md(f) + " to " + Ui.md(t))
    }
    tools: [
        Seg {
            name: "Range"
            options: [["24h", "24h"], ["7d", "7d"], ["30d", "30d"], ["90d", "90d"], ["all", "All"]]
            current: backend.rangeKey
            onPicked: function (k) { backend.setRange(k) }
        },
        Btn { glyph: ""; tip: "Refresh"; spin: backend.busy; onClicked: backend.refresh() }
    ]

    // ---- first run: building the history
    Card {
        id: indexing
        readonly property var ix: page.st ? page.st.indexing : null
        readonly property real frac: ix && ix.total ? Math.min(1, ix.done / ix.total) : 0
        visible: !!ix
        width: page.cw; height: 96
        Column {
            width: parent.width
            spacing: 8
            Item {
                width: parent.width; height: 22
                Txt { height: 22; text: "Building your history"; font.weight: 600; font.pixelSize: 14 }
                Txt { anchors.right: parent.right; height: 22; text: Ui.pct(indexing.frac); font.weight: 600 }
            }
            Rectangle {
                width: parent.width; height: 6; radius: 3; color: Ui.p.track
                Rectangle {
                    height: 6; radius: 3; color: Ui.p.accent
                    width: parent.width * indexing.frac
                    Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                }
            }
            Txt {
                width: parent.width
                text: indexing.ix ? "Indexed " + Ui.int(indexing.ix.done) + " of " + Ui.int(indexing.ix.total) + " transcripts. Totals below are partial until it finishes." : ""
                font.pixelSize: 12; color: Ui.p.text_muted
            }
        }
    }

    // ---- errors the data layer reported
    Card {
        visible: !!(page.st && page.st.state === "error")
        width: page.cw; height: 64
        Row {
            spacing: 12; height: parent.height
            Pill { anchors.verticalCenter: parent.verticalCenter; kind: "bad"; text: "Problem" }
            Txt { height: parent.height; text: page.st && page.st.message ? page.st.message : "Something went wrong reading your usage."; width: page.cw - 160 }
        }
    }

    // ---- plan limits
    Eyebrow {
        width: page.cw
        title: "PLAN LIMITS"
        sub: page.signedOut ? "" : "Account-wide" + (page.limitsAsOf ? ", as of " + Ui.clock(page.limitsAsOf) : "") + (page.snap && page.snap.project ? ". Limits are not split by project." : ".")
        pill: page.stale ? "Not refreshed since " + (page.limitsAsOf ? Ui.whenShort(page.limitsAsOf) : "a while") : ""
        pillKind: "warn"
    }
    Card {
        visible: page.signedOut
        width: page.cw; height: 112
        Row {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 20
            Rectangle {
                width: 48; height: 48; radius: 24; color: Ui.p.warn_bg
                Icon { anchors.centerIn: parent; glyph: ""; font.pixelSize: 20; color: Ui.p.warn }
            }
            Column {
                spacing: 6
                width: page.cw - 2 * Ui.pad - 68 - 140
                Txt { text: "Sign in to Claude Code to see your plan limits"; font.pixelSize: 15; font.weight: 600 }
                Txt {
                    width: parent.width
                    wrapMode: Text.WordWrap; elide: Text.ElideNone
                    color: Ui.p.text_muted
                    text: (page.st && page.st.message ? page.st.message + " " : "") + "Your usage history below still works. Run claude in a terminal, sign in, then refresh."
                }
            }
        }
        Btn { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "Refresh"; glyph: ""; primary: true; onClicked: backend.refresh() }
    }
    Item {
        id: limitsRow
        visible: !page.signedOut && !!page.snap
        width: page.cw
        readonly property bool wide: page.cw >= 1120
        readonly property real colW: (page.cw - 2 * Ui.gap) / 3.75
        readonly property real rowH: Math.max(week.natural, past.natural, other.natural)
        height: wide ? rowH : week.natural + Ui.gap + Math.max(past.natural, other.natural)
        opacity: page.stale ? 0.62 : 1
        WeekCard {
            id: week
            w: page.snap ? page.snap.limits.weekly : null
            nowMs: page.nowMs
            width: limitsRow.wide ? limitsRow.colW * 1.75 : page.cw
            height: limitsRow.wide ? limitsRow.rowH : natural
        }
        PastWeeksCard {
            id: past
            limits: page.snap ? page.snap.limits : null
            x: limitsRow.wide ? week.width + Ui.gap : 0
            y: limitsRow.wide ? 0 : week.height + Ui.gap
            width: limitsRow.wide ? limitsRow.colW : (page.cw - Ui.gap) / 2
            height: limitsRow.wide ? limitsRow.rowH : Math.max(natural, other.natural)
        }
        OtherLimitsCard {
            id: other
            limits: page.snap ? page.snap.limits : null
            x: past.x + past.width + Ui.gap
            y: past.y
            width: limitsRow.wide ? limitsRow.colW : (page.cw - Ui.gap) / 2
            height: past.height
        }
    }

    // ---- usage for the range
    Item { width: 1; height: 8 }
    Eyebrow {
        width: page.cw
        title: "USAGE"
        sub: page.snap ? (page.snap.project || "All projects") + ", " + page.snap.range.label + "."
             + (page.snap.range.key === "all" ? "" : " Changes compare with the same length of time just before, to the hour.") : ""
    }
    Card {
        visible: page.empty
        width: page.cw; height: 150
        Column {
            anchors.centerIn: parent
            spacing: 6
            Txt { anchors.horizontalCenter: parent.horizontalCenter; text: "No activity " + Ui.inRange(page.range); font.pixelSize: 15; font.weight: 600 }
            Txt {
                anchors.horizontalCenter: parent.horizontalCenter
                color: Ui.p.text_muted
                text: page.snap && page.snap.project ? page.snap.project + " has no sessions in this range." : "No Claude Code sessions in this range."
            }
            Item { width: 1; height: 6 }
            Btn {
                visible: page.range && page.range.key !== "all"
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Show all time"
                onClicked: backend.setRange("all")
            }
        }
    }
    Row {
        id: kpis
        visible: !page.empty && !!page.snap
        width: page.cw
        spacing: Ui.gap
        readonly property var hues: ({ cost: Ui.seriesColor(1), tokens: Ui.seriesColor(6), sessions: Ui.seriesColor(0), agent_time: Ui.seriesColor(2), cache_hit: Ui.seriesColor(4) })
        Repeater {
            model: page.snap ? page.snap.kpis : []
            KpiCard {
                width: (page.cw - Ui.gap * (page.snap.kpis.length - 1)) / page.snap.kpis.length
                height: 180
                k: modelData
                hue: kpis.hues[modelData.key] || Ui.p.accent
                dropLast: page.snap.timeline.partial_last && modelData.series.length === page.snap.timeline.starts.length
                smoothing: page.snap.timeline.bucket === "6h" || page.snap.timeline.bucket === "hour" ? 1 : modelData.series.length > 40 ? 2 : 0
            }
        }
    }
    TimelineCard {
        visible: !page.empty && !!page.snap
        width: page.cw
        height: implicitHeight
        tl: page.snap ? page.snap.timeline : null
        range: page.range
        scoped: !!(page.snap && page.snap.project)
    }
    Item {
        id: lower
        visible: !page.empty && !!page.snap
        width: page.cw
        readonly property bool wide: page.cw >= 1240
        height: wide ? Math.max(sessCard.height, act.height) : sessCard.height + Ui.gap + act.height
        Card {
            id: sessCard
            padding: 0
            width: lower.wide ? (page.cw - Ui.gap) * 0.6 : page.cw
            height: 24 + 40 + 12 + top.implicitHeight + 56
            readonly property var rows: page.snap ? page.snap.sessions.slice().sort(function (a, b) { return b.cost - a.cost }).slice(0, 10) : []
            Item {
                x: Ui.pad; y: 24
                width: parent.width - 2 * Ui.pad; height: 40
                Row {
                    spacing: 8; height: 28
                    Txt { height: 28; text: "Top sessions"; font.pixelSize: 15; font.weight: 600; display: true }
                    Txt { height: 28; text: "by cost"; color: Ui.p.text_faint }
                }
            }
            SessionTable {
                id: top
                y: 24 + 40
                width: parent.width
                rows: sessCard.rows
                sortable: false
            }
            Item {
                x: Ui.pad; y: parent.height - 52
                width: parent.width - 2 * Ui.pad; height: 40
                Rectangle { width: parent.width; height: 1; color: Ui.p.line; y: -1 }
                Txt {
                    height: 40
                    text: page.snap ? "Showing " + sessCard.rows.length + " of " + Ui.plural(page.snap.sessions_total, "session") + " " + Ui.inRange(page.snap.range) + "." : ""
                    font.pixelSize: 12; color: Ui.p.text_faint
                }
                Btn { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "All sessions"; glyph: ""; onClicked: page.openSessions() }
            }
        }
        ActivityCard {
            id: act
            act: page.snap ? page.snap.activity : null
            x: lower.wide ? sessCard.width + Ui.gap : 0
            y: lower.wide ? 0 : sessCard.height + Ui.gap
            width: lower.wide ? page.cw - sessCard.width - Ui.gap : page.cw
            height: implicitHeight
        }
    }
    ModelsCard {
        visible: !page.empty && !!page.snap
        width: page.cw
        height: implicitHeight
        models: page.snap ? page.snap.models : []
        split: page.snap ? page.snap.cost_split : null
        range: page.range
    }
    Column {
        width: page.cw
        spacing: 4
        topPadding: 4
        Txt {
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: "Cost is API-equivalent: tokens from your local Claude Code transcripts priced at published per-token rates. It is not your bill."
        }
        Txt {
            visible: !!(page.st && (page.st.coverage_from || page.st.legacy_before))
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: {
                if (!page.st) return ""
                var s = [], c = page.st.coverage_from ? Ui.date(page.st.coverage_from + "T12:00:00") : null
                if (c) s.push("Full detail from " + Ui.md(c) + ", " + c.getFullYear() + ".")
                if (page.st.legacy_before) s.push((c ? "Earlier days" : "Some days") + " come from the old daily rollup, with cost and tokens only.")
                return s.join(" ")
            }
        }
    }
}
