import QtQuick

// Every session in the range: search, filter, sort, 50 rows at a time.
Page {
    id: page
    property string query: ""
    property string kind: "all"
    property string sortKey: "started"
    property bool sortDesc: true
    property int shown: 50

    readonly property var all: snap ? snap.sessions : []
    readonly property var rows: {
        var q = query.trim().toLowerCase(), k = kind
        var out = all.filter(function (s) {
            if (k === "interactive" && !s.interactive) return false
            if (k === "automated" && s.interactive) return false
            if (!q) return true
            return [s.title || "", s.project, s.model, s.id, s.entrypoint].join(" ").toLowerCase().indexOf(q) >= 0
        })
        var key = sortKey, dir = sortDesc ? -1 : 1
        out.sort(function (a, b) {
            var x = a[key], y = b[key]
            if (key === "started") { x = Date.parse(x); y = Date.parse(y) }
            if (x == null && y == null) return 0
            if (x == null) return 1
            if (y == null) return -1
            return (x < y ? -1 : x > y ? 1 : 0) * dir
        })
        return out
    }
    onQueryChanged: shown = 50
    onKindChanged: shown = 50
    onSnapChanged: shown = 50

    title: "Sessions"
    subtitle: snap ? Ui.plural(snap.sessions_total, "session") + " in the " + snap.range.label + (snap.project ? ", " + snap.project : "")
                     + (snap.sessions.length < snap.sessions_total ? ". Showing the " + Ui.int(snap.sessions.length) + " most recent." : "") : ""
    tools: [
        Seg {
            name: "Range"
            options: [["24h", "24h"], ["7d", "7d"], ["30d", "30d"], ["90d", "90d"], ["all", "All"]]
            current: backend.rangeKey
            onPicked: function (k) { backend.setRange(k) }
        }
    ]

    Card {
        width: page.cw
        padding: 0
        height: 24 + 34 + 16 + table.implicitHeight + (more.visible || page.rows.length === 0 ? 72 : 16)
        Item {
            x: Ui.pad; y: 24
            width: parent.width - 2 * Ui.pad; height: 34
            Row {
                spacing: 10
                SearchField {
                    id: search
                    width: 300
                    placeholderText: "Search title, project, model or id"
                    onTextChanged: page.query = text
                }
                Seg {
                    name: "Session kind"
                    options: [["all", "All"], ["interactive", "Interactive"], ["automated", "Automated"]]
                    current: page.kind
                    onPicked: function (k) { page.kind = k }
                }
            }
            Txt {
                anchors.right: parent.right; height: 34
                text: "Showing " + Ui.int(Math.min(page.shown, page.rows.length)) + " of " + Ui.int(page.rows.length)
                font.pixelSize: 12; color: Ui.p.text_faint
            }
        }
        SessionTable {
            id: table
            y: 24 + 34 + 16
            width: parent.width
            rows: page.rows.slice(0, page.shown)
            sortKey: page.sortKey
            sortDesc: page.sortDesc
            showProject: true
            onSortBy: function (k) {
                if (page.sortKey === k) page.sortDesc = !page.sortDesc
                else { page.sortKey = k; page.sortDesc = k !== "project" }
            }
        }
        Txt {
            visible: page.rows.length === 0
            x: Ui.pad; y: table.y + table.height + 8
            height: 48
            text: page.all.length ? "No sessions match." : "No sessions in this range."
            color: Ui.p.text_muted
        }
        Btn {
            id: more
            visible: page.rows.length > page.shown
            anchors.horizontalCenter: parent.horizontalCenter
            y: table.y + table.height + 18
            text: "Show " + Math.min(50, page.rows.length - page.shown) + " more"
            onClicked: page.shown += 50
        }
    }
    Shortcut { sequence: "Ctrl+F"; enabled: page.visible; onActivated: search.forceActiveFocus() }
}
