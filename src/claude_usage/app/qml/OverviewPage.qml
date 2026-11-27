import QtQuick
import QtQuick.Templates as T

// How am I doing right now, and over this range.
Page {
    id: page
    property real nowMs: Date.now()
    property real viewH: height      // the visible height, for the hero chart
    signal openSessions()

    readonly property var st: snap ? snap.status : null
    readonly property var range: snap ? snap.range : null
    readonly property bool signedOut: !!st && st.state === "signed_out"
    readonly property bool stale: !!st && st.state === "stale"
    // nothing to show for the limits: say what failed instead of reading as plan facts
    readonly property bool failed: !!st && st.state === "error" && !(snap.limits && snap.limits.weekly)
    // the first limits fetch has not answered yet: placeholders, not "not reported"
    readonly property bool pending: !!st && st.state === "loading" && !(snap.limits && snap.limits.weekly)
    readonly property bool empty: !!snap && snap.sessions_total === 0
    // a store from a newer version: nothing is read, and neither refreshing nor waiting helps
    readonly property bool storeErr: !!snap && snap.now.error_kind === "store"
    // a binding 5-hour limit leads in the plan limits' header (the layout stays weekly-first)
    readonly property var hot: snap && snap.limits.session && (snap.limits.session.level === "critical" || snap.limits.session.level === "limited") ? snap.limits.session : null
    // the 5-hour forecast, when it runs out before the reset (as the menu and tray say it)
    readonly property var hotEta: hot && hot.level !== "limited" && snap.now.session && snap.now.session.eta
                                  && (!hot.resets_at || Ui.date(snap.now.session.eta) < Ui.date(hot.resets_at)) ? Ui.date(snap.now.session.eta) : null
    readonly property var ix: st ? st.indexing : null
    readonly property var limitsAsOf: st && st.limits_as_of ? Ui.date(st.limits_as_of) : null
    readonly property string plan: st && st.plan ? st.plan : ""

    // indexing speed seen since this window first saw it, for a time-left estimate
    property var ixStart: null
    onIxChanged: {
        if (!ix) ixStart = null
        else if (!ixStart || ix.done < ixStart.done) ixStart = { done: ix.done, t: Date.now() }
    }
    readonly property string ixLeft: {
        // early transcripts vary too much in size for a fair rate: wait for 5% and 30 s of progress
        if (!ix || !ixStart || ix.done - ixStart.done < ix.total * 0.05 || nowMs - ixStart.t < 30000) return ""
        var left = (ix.total - ix.done) / (ix.done - ixStart.done) * (nowMs - ixStart.t)
        return left < 60000 ? "Under a minute left" : "About " + Ui.span(left) + " left"
    }

    title: snap && snap.project ? snap.project : "All projects"
    subtitle: {
        if (!snap) return "Loading your usage"
        if (storeErr) return ""
        // no count while the first index has not reached a session yet
        var r = snap.range, txt = ix && !snap.sessions_total ? "Counting sessions" : Ui.plural(snap.sessions_total, "session")
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
        Btn { glyph: "\ue72c"; tip: "Refresh"; spin: backend.busy; onClicked: backend.refresh() }
    ]

    // Placeholders in the page's shape until the first snapshot; no animation.
    component Ghost: Card {
        Rectangle { width: Math.min(140, parent.width * 0.5); height: 12; radius: 6; color: Ui.p.track }
        Rectangle { y: 30; width: Math.min(110, parent.width * 0.4); height: 26; radius: 8; color: Ui.p.track }
        Rectangle { y: 76; width: parent.width; height: Math.max(0, parent.height - 76); radius: 10; color: Ui.alpha(Ui.p.track, 0.5) }
    }
    // Stands in for the limits when there are none to show, and says why.
    component Notice: Card {
        id: nc
        property string kind: "warn"
        property string glyph: ""
        property string head: ""
        property string body: ""
        property bool canRefresh: true
        height: Math.max(112, txt.implicitHeight + 2 * padding)
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 48; height: 48; radius: 24; color: nc.kind === "bad" ? Ui.p.bad_bg : Ui.p.warn_bg
            Icon { anchors.centerIn: parent; glyph: nc.glyph; font.pixelSize: 20; color: nc.kind === "bad" ? Ui.p.bad : Ui.p.warn }
        }
        Column {
            id: txt
            x: 68; anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 68 - (refresh.visible ? refresh.width + 24 : 0)
            spacing: 6
            Txt { width: parent.width; text: nc.head; font.pixelSize: 15; font.weight: 600 }
            Txt { width: parent.width; text: nc.body; wrapMode: Text.WordWrap; elide: Text.ElideNone; color: Ui.p.text_muted }
        }
        Btn { id: refresh; visible: nc.canRefresh; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "Refresh"; glyph: "\ue72c"; primary: true; onClicked: backend.refresh() }
    }

    // ---- first run: building the history
    Card {
        id: indexing
        readonly property real frac: page.ix && page.ix.total ? Math.min(1, page.ix.done / page.ix.total) : 0
        visible: !!page.ix
        width: page.cw; height: 132
        Rectangle {
            width: 40; height: 40; radius: 20; color: Ui.alpha(Ui.p.accent, Ui.dark ? 0.2 : 0.12)
            Icon { anchors.centerIn: parent; glyph: "\ue81c"; font.pixelSize: 18; color: Ui.p.accent_text }
        }
        Column {
            x: 56; width: parent.width - 56 - pctBig.width - 16
            spacing: 2
            Txt { width: parent.width; height: 22; text: "Building your history"; font.pixelSize: 15; font.weight: 600 }
            Txt {
                width: parent.width; height: 18; color: Ui.p.text_muted
                text: "Reading your Claude Code transcripts once, so your usage stays after Claude Code cleans them up."
            }
        }
        Txt { id: pctBig; anchors.right: parent.right; height: 40; text: Math.floor(indexing.frac * 100) + "%"; display: true; font.pixelSize: 28; font.weight: 600 }  // floored, as the OSD and menu say it
        Rectangle {
            y: 56; width: parent.width; height: 6; radius: 3; color: Ui.p.track
            Rectangle {
                height: 6; radius: 3; color: Ui.p.accent
                width: Math.max(6, parent.width * indexing.frac)
                Behavior on width { NumberAnimation { duration: Ui.ms(300); easing.type: Easing.OutCubic } }
            }
        }
        Txt {
            y: 68; height: 16; width: parent.width - left.implicitWidth - 16
            text: page.ix ? Ui.int(page.ix.done) + " of " + Ui.int(page.ix.total) + " transcripts. The numbers below grow as they are read." : ""
            font.pixelSize: 12; color: Ui.p.text_muted
        }
        Txt { id: left; anchors.right: parent.right; y: 68; height: 16; text: page.ixLeft; font.pixelSize: 12; color: Ui.p.text_muted }
    }

    // ---- plan limits
    Column {
        width: page.cw
        spacing: 8
        Eyebrow {
            width: page.cw
            title: "PLAN LIMITS"
            sub: page.signedOut || page.failed || !page.snap ? ""
                 : page.pending ? "Checking your plan limits."
                 : (page.plan ? page.plan + " plan, account-wide" : "Account-wide")
                   + (page.limitsAsOf && !page.stale ? ", as of " + Ui.clock(page.limitsAsOf) : "")
                   + (page.snap && page.snap.project ? ". Limits are not split by project." : ".")
            pill: page.stale ? "Not refreshed since " + (page.limitsAsOf ? Ui.whenShort(page.limitsAsOf) : "a while")
                  : page.st && page.st.state === "error" && !page.failed ? "Could not refresh"
                  : page.hot ? (page.hot.level === "limited" ? "5-hour limit reached" : "5-hour " + Ui.pct(page.hot.pct))  // as the menu says it
                               + (page.hotEta ? (page.hotEta - page.nowMs < 60000 ? ", limit any minute" : ", limit in ~" + Ui.span(page.hotEta - page.nowMs))
                                  : page.hot.resets_at ? ", resets " + Ui.clock(Ui.date(page.hot.resets_at)) : "") : ""
            pillKind: page.stale || page.st && page.st.state === "error" ? "warn" : "bad"
            note: pill !== "" && page.st && page.st.message ? Ui.sentence(page.st.message) : ""
        }
        Notice {
            visible: page.signedOut
            width: page.cw
            kind: "warn"; glyph: "\ue77b"
            head: "Sign in to Claude Code to see your plan limits"
            body: (page.st && page.st.message ? Ui.sentence(page.st.message) : "Run claude in a terminal and sign in, then refresh.") + " Your usage history below still works."
        }
        Notice {
            visible: page.failed
            width: page.cw
            kind: "bad"; glyph: "\ue783"
            head: "Couldn't load your plan limits"
            body: (page.st && page.st.message ? Ui.sentence(page.st.message) : "The usage service did not answer.") + (page.storeErr ? "" : " Your usage history below still works.")
            canRefresh: !page.storeErr
        }
        Item {
            id: limitsRow
            visible: !page.signedOut && !page.failed && !page.pending && !!page.snap
            width: page.cw
            readonly property bool wide: page.cw >= 900
            readonly property real colW: (page.cw - 2 * Ui.gap) / 3.6   // split 1.55 : 0.95 : 1.1
            readonly property real rowH: Math.max(week.natural, past.natural, other.natural)
            height: wide ? rowH : week.natural + Ui.gap + Math.max(past.natural, other.natural)
            WeekCard {
                id: week
                w: page.snap ? page.snap.limits.weekly : null
                nowMs: page.nowMs
                stale: page.stale
                width: limitsRow.wide ? limitsRow.colW * 1.55 : page.cw
                height: limitsRow.wide ? limitsRow.rowH : natural
            }
            PastWeeksCard {
                id: past
                limits: page.snap ? page.snap.limits : null
                x: limitsRow.wide ? week.width + Ui.gap : 0
                y: limitsRow.wide ? 0 : week.height + Ui.gap
                width: limitsRow.wide ? limitsRow.colW * 0.95 : (page.cw - Ui.gap) / 2
                height: limitsRow.wide ? limitsRow.rowH : Math.max(natural, other.natural)
            }
            OtherLimitsCard {
                id: other
                limits: page.snap ? page.snap.limits : null
                plan: page.plan
                session: page.snap ? page.snap.now.session : null
                nowMs: page.nowMs
                x: past.x + past.width + Ui.gap
                y: past.y
                width: limitsRow.wide ? page.cw - week.width - past.width - 2 * Ui.gap : (page.cw - Ui.gap) / 2
                height: past.height
            }
        }
        Row {
            visible: !page.snap || page.pending
            spacing: Ui.gap
            Repeater {
                model: limitsRow.wide ? [1.55, 0.95, 1.1] : [0]
                Ghost { width: modelData ? limitsRow.colW * modelData : page.cw; height: 344 }
            }
        }
    }

    // ---- usage for the range
    Column {
        width: page.cw
        spacing: 8
        Eyebrow {
            width: page.cw
            title: "USAGE"
            sub: page.snap ? (page.snap.project || "All projects") + ", " + page.snap.range.label + "."
                 + (page.snap.range.key === "all" ? "" : " Changes compare with the same length of time just before, to the hour.") : ""
            pill: page.ix && !page.empty ? "Partial until indexing finishes" : ""
        }
        Card {
            id: emptyCard
            visible: page.empty
            width: page.cw; height: 150
            Column {
                anchors.centerIn: parent
                spacing: 6
                // nothing yet because the first index is still running, not because nothing ran
                Txt {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: page.storeErr ? "Usage history unavailable" : page.ix ? "Still reading your transcripts" : "No activity " + Ui.inRange(page.range)
                    font.pixelSize: 15; font.weight: 600
                }
                Txt {
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: Ui.p.text_muted
                    text: page.storeErr ? "Usage shows again after you update Claude Usage."
                        : page.ix ? Ui.int(page.ix.done) + " of " + Ui.int(page.ix.total) + " so far. Usage shows up here as they are indexed."
                        : page.snap && page.snap.project ? page.snap.project + " has no sessions in this range." : "No Claude Code sessions in this range."
                }
                Item { width: 1; height: 6 }
                Btn {
                    visible: !page.ix && !page.storeErr && !!page.range && page.range.key !== "all"
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "Show all time"
                    onClicked: backend.setRange("all")
                }
            }
        }
        Flow {
            id: kpis
            visible: !page.empty && !!page.snap
            width: page.cw
            spacing: Ui.gap
            // five across down to a 700 px page (smaller numbers, no note), then three and two
            readonly property int columns: page.cw >= 700 ? 5 : 3
            function cardW(i) { var c = columns === 5 || i < 3 ? columns : 2; return (page.cw - Ui.gap * (c - 1)) / c - 0.01 }  // a hair short so rounding never wraps a row
            readonly property var hues: ({ cost: Ui.seriesColor(1), tokens: Ui.seriesColor(5), sessions: Ui.seriesColor(0), agent_time: Ui.seriesColor(6), cache_hit: Ui.seriesColor(3) })
            Repeater {
                // a count, not the array, so cards persist across snapshots and their values animate
                model: page.snap ? page.snap.kpis.length : 0
                KpiCard {
                    readonly property var raw: page.snap && page.snap.kpis[index] ? page.snap.kpis[index] : null
                    // Cost KPIs come rounded to cents, which can round a dollar
                    // differently than the timeline total; show both from one number.
                    readonly property var kk: raw && raw.key === "cost" ? Object.assign({}, raw, { value: page.snap.timeline.totals.cost }) : raw
                    width: kpis.cardW(index)
                    height: implicitHeight
                    k: kk
                    hue: kk ? kpis.hues[kk.key] || Ui.p.accent : Ui.p.accent
                    dropLast: !!kk && page.snap.timeline.partial_last && kk.series.length === page.snap.timeline.starts.length
                    smoothing: kk && kk.series.length > 20 ? 1 : 0
                }
            }
        }
        Row {
            visible: !page.snap
            spacing: Ui.gap
            Repeater {
                model: kpis.columns
                Ghost { width: (page.cw - Ui.gap * (kpis.columns - 1)) / kpis.columns - 0.01; height: 156 }
            }
        }
    }
    Ghost { visible: !page.snap; width: page.cw; height: 440 }
    TimelineCard {
        visible: !page.empty && !!page.snap
        width: page.cw
        height: implicitHeight
        tl: page.snap ? page.snap.timeline : null
        range: page.range
        chartH: Math.max(300, Math.min(440, page.viewH * 0.36))
        dataKey: page.snap ? page.snap.range.key + "|" + (page.snap.project || "") : ""
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
            // top_sessions ranks every session in range, past the 2,000-row cap
            readonly property var rows: page.snap ? page.snap.top_sessions : []
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
                    text: !page.snap ? ""
                        : "Showing " + sessCard.rows.length + " of " + Ui.plural(page.snap.sessions_total, "session") + " " + Ui.inRange(page.snap.range) + "."
                    font.pixelSize: 12; color: Ui.p.text_faint
                }
                Btn { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "All sessions"; glyph: ""; onClicked: page.openSessions() }
            }
        }
        ActivityCard {
            id: act
            act: page.snap ? page.snap.activity : null
            range: page.range
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
    // One line on what cost means; the rest of the note and the coverage behind "About this data".
    Column {
        id: foot
        visible: !!page.snap && !page.storeErr
        width: page.cw
        spacing: 4
        topPadding: 4
        property bool open: false
        readonly property string note: backend.costNote
        readonly property int cut: note.indexOf("It is not a bill.") >= 0 ? note.indexOf("It is not a bill.") + 17 : note.length
        Txt {
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: foot.note.slice(0, foot.cut)
        }
        T.AbstractButton {
            id: about
            x: -6
            width: aboutRow.implicitWidth + 12; height: 28
            hoverEnabled: true
            text: "About this data"
            onClicked: foot.open = !foot.open
            Accessible.role: Accessible.Button
            Accessible.name: text + (foot.open ? ", expanded" : ", collapsed")
            contentItem: Item {
                Row {
                    id: aboutRow
                    x: 6; height: parent.height
                    spacing: 6
                    Txt { height: parent.height; text: about.text; font.pixelSize: 12; color: about.hovered ? Ui.p.text : Ui.p.text_muted }
                    Icon { height: parent.height; glyph: foot.open ? "" : ""; font.pixelSize: 10; color: about.hovered ? Ui.p.text : Ui.p.text_muted }
                }
            }
            background: Rectangle { radius: 6; color: about.hovered ? Ui.alpha(Ui.p.text, 0.05) : "transparent"; Focus { show: about.visualFocus; r: 6 } }
        }
        Txt {
            visible: foot.open && text !== ""
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: foot.note.slice(foot.cut).trim()
        }
        Txt {
            visible: foot.open && !!(page.st && (page.st.coverage_from || page.st.legacy_before))
            width: parent.width
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: {
                if (!page.st) return ""
                var s = [], c = page.st.coverage_from ? Ui.date(page.st.coverage_from + "T12:00:00") : null
                if (c) s.push("Full detail from " + Ui.md(c) + ", " + c.getFullYear() + ".")
                if (page.st.legacy_before) s.push((c ? "Earlier days" : "Some days") + " are old daily totals, with cost and tokens only, and usually read lower.")
                return s.join(" ")
            }
        }
    }
}
