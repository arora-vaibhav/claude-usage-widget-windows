import QtQuick
import QtQuick.Shapes
import QtQuick.Templates as T

// Brand, pages, the project filter and the data status.
Rectangle {
    id: side
    property var snap: null
    property string page: "overview"
    property real nowMs: Date.now()
    signal navigate(string page)

    color: Ui.p.sidebar

    // The data layer lists every project for the range whatever the filter.
    readonly property var projects: snap ? snap.projects : []
    readonly property var active: projects.filter(function (p) { return !p.idle }).sort(function (a, b) { return b.cost - a.cost || b.sessions - a.sessions })
    readonly property var idle: projects.filter(function (p) { return p.idle }).sort(function (a, b) { return b.cost - a.cost })
    readonly property string current: snap && snap.project ? snap.project : ""
    // counts are unknown, not 0, for a store from a newer version and before the first index reaches a session
    readonly property bool noCounts: !!snap && (snap.now.error_kind === "store" || !!snap.status.indexing && !snap.sessions_total_all)
    // a session that moved between folders counts once per project, so this is not their sum
    readonly property string total: snap && !noCounts ? Ui.int(snap.sessions_total_all) : ""
    readonly property var status: snap ? snap.status : null
    // A busy month can touch a hundred folders; list the biggest and fold the rest.
    readonly property int topN: 8
    property bool showAll: false
    readonly property var rest: active.slice(topN).concat(idle)
    readonly property var listed: showAll ? active.concat(idle)
        : active.slice(0, topN).concat(rest.filter(function (p) { return p.name === side.current }))

    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Ui.p.line }

    Column {
        id: head
        x: 16; y: 20
        width: parent.width - 32
        spacing: 2

        Row {
            height: 44
            leftPadding: 10
            spacing: 10
            Item {
                width: 24; height: 24
                anchors.verticalCenter: parent.verticalCenter
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: Ui.alpha(Ui.p.brand, 0.28); strokeWidth: 3; fillColor: "transparent"
                        PathAngleArc { centerX: 12; centerY: 12; radiusX: 9.5; radiusY: 9.5; startAngle: 0; sweepAngle: 360 }
                    }
                    ShapePath {
                        strokeColor: Ui.p.brand; strokeWidth: 3; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                        PathAngleArc { centerX: 12; centerY: 12; radiusX: 9.5; radiusY: 9.5; startAngle: 135; sweepAngle: 250 }
                    }
                }
                Rectangle { anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: Ui.p.brand }
            }
            Txt { anchors.verticalCenter: parent.verticalCenter; text: "Claude Usage"; display: true; font.pixelSize: 15; font.weight: 600 }
        }
        Item { width: 1; height: 12 }
        NavItem { width: parent.width; text: "Overview"; glyph: ""; selected: side.page === "overview"; onClicked: side.navigate("overview") }
        NavItem {
            width: parent.width; text: "Sessions"; glyph: ""; selected: side.page === "sessions"
            count: snap && !side.noCounts ? Ui.int(snap.sessions_total) : ""
            onClicked: side.navigate("sessions")
        }
        NavItem { width: parent.width; text: "Settings"; glyph: ""; selected: side.page === "settings"; onClicked: side.navigate("settings") }
        Item { width: 1; height: 20 }
        Item {
            width: parent.width; height: 24
            Txt { x: 12; height: parent.height; text: "PROJECTS"; font.pixelSize: 11; font.weight: 600; font.letterSpacing: 0.9; color: Ui.p.text_faint }
            Txt { anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height; text: side.snap && !side.noCounts ? side.active.length + " active" : ""; font.pixelSize: 12; color: Ui.p.text_faint }
        }
    }

    ListView {
        id: list
        // 3 px margins so a row's keyboard focus ring is not clipped
        x: 13; width: parent.width - 26
        leftMargin: 3; rightMargin: 3; topMargin: 3; bottomMargin: 3
        y: head.y + head.height + 1
        // as tall as its rows, so "Show more" sits right under the last one
        height: Math.min(contentHeight + 6, foot.y - 12 - y - (more.visible ? more.height : 0))
        clip: true
        spacing: 2
        boundsBehavior: Flickable.StopAtBounds
        T.ScrollBar.vertical: Bar {}
        // a count, so rows persist across refreshes and only their bindings update
        model: side.listed.length
        header: NavItem {
            width: list.width - 6; text: "All projects"; selected: side.current === ""
            count: side.total
            onClicked: backend.setProject("")
        }
        delegate: NavItem {
            readonly property var m: side.listed[index]
            width: list.width - 6
            text: m ? m.name : ""
            hasDot: true
            // the band's colour when the chart draws it, "Other" grey when it is
            // part of Other, and a faint dot when it has no use in this range
            dot: !m ? "transparent" : m.idle ? Ui.p.line_strong : Ui.seriesColor(m.color)
            dim: !!m && m.idle
            selected: !!m && side.current === m.name
            count: m && m.sessions ? Ui.int(m.sessions) : ""
            onClicked: backend.setProject(m.name)
        }
    }
    // outside the list, so Tab reaches it after the last project
    T.AbstractButton {
        id: more
        visible: side.rest.length > 0
        x: 16; y: list.y + list.height
        width: list.width - 6; height: 34
        hoverEnabled: true
        text: side.showAll ? "Show fewer" : "Show " + Ui.int(side.rest.length) + " more"
        onClicked: side.showAll = !side.showAll
        contentItem: Txt { leftPadding: 12; text: more.text; font.pixelSize: 12; color: more.hovered ? Ui.p.text : Ui.p.text_muted }
        background: Rectangle { radius: 10; color: more.hovered ? Ui.alpha(Ui.p.text, 0.05) : "transparent"; Focus { show: more.visualFocus } }
    }

    Column {
        id: foot
        x: 16; width: parent.width - 32
        anchors.bottom: parent.bottom; anchors.bottomMargin: 18
        spacing: 6
        Rectangle { width: parent.width; height: 1; color: Ui.p.line }
        Item { width: 1; height: 6 }
        Column {
            visible: !!(side.status && side.status.indexing)
            width: parent.width - 16; x: 8
            spacing: 6
            readonly property var ix: side.status ? side.status.indexing : null
            Txt {
                width: parent.width
                text: parent.ix ? "Indexing " + Ui.int(parent.ix.done) + " of " + Ui.int(parent.ix.total) : ""
                font.pixelSize: 12; color: Ui.p.text_muted
            }
            Rectangle {
                width: parent.width; height: 4; radius: 2; color: Ui.p.track
                Rectangle {
                    height: 4; radius: 2; color: Ui.p.accent
                    width: parent.parent.ix && parent.parent.ix.total ? parent.width * Math.min(1, parent.parent.ix.done / parent.parent.ix.total) : 0
                    Behavior on width { NumberAnimation { duration: Ui.ms(300); easing.type: Easing.OutCubic } }
                }
            }
        }
        Row {
            x: 8; spacing: 8; height: 20
            readonly property string st: side.status ? side.status.state : "loading"
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 8; height: 8; radius: 4
                color: parent.st === "ok" ? (side.snap && side.snap.now.live ? Ui.p.ok : Ui.p.text_faint)
                     : parent.st === "stale" ? Ui.p.warn_fill : parent.st === "loading" ? Ui.p.text_faint : Ui.p.bad_fill
                Rectangle {
                    visible: !!(side.snap && side.snap.now.live) && parent.parent.st === "ok"
                    anchors.centerIn: parent; width: 16; height: 16; radius: 8
                    color: Ui.alpha(Ui.p.ok, 0.18)
                }
            }
            Txt {
                height: 20
                font.pixelSize: 12
                color: Ui.p.text_muted
                text: {
                    var st = side.status
                    if (!st) return "Loading"
                    if (st.state === "signed_out") return "Signed out"
                    var u = Ui.date(backend.updatedAt || st.updated_at)
                    return u ? "Updated " + Ui.ago(side.nowMs - u.getTime()) : "Waiting for data"
                }
            }
        }
        Txt {
            x: 8; width: parent.width - 16
            // nothing while the count is moving, or when the store could not be read
            visible: !!side.status && !side.status.indexing && side.snap.now.error_kind !== "store"
            text: side.status ? "From " + Ui.int(side.status.transcripts) + " local transcripts" : ""
            font.pixelSize: 12; color: Ui.p.text_faint
        }
    }
}
