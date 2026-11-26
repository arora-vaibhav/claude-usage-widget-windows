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

    width: 248
    color: Ui.p.sidebar

    readonly property var projects: snap ? snap.projects : []
    readonly property var active: projects.filter(function (p) { return !p.idle }).sort(function (a, b) { return b.cost - a.cost || b.sessions - a.sessions })
    readonly property var idle: projects.filter(function (p) { return p.idle }).sort(function (a, b) { return b.cost - a.cost })
    readonly property string current: snap && snap.project ? snap.project : ""
    readonly property int total: projects.reduce(function (a, p) { return a + p.sessions }, 0)
    readonly property var status: snap ? snap.status : null
    property bool showIdle: false
    readonly property var listed: active.concat(showIdle ? idle : idle.filter(function (p) { return p.name === side.current }))

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
            count: snap ? Ui.int(snap.sessions_total) : ""
            onClicked: side.navigate("sessions")
        }
        NavItem { width: parent.width; text: "Settings"; glyph: ""; selected: side.page === "settings"; onClicked: side.navigate("settings") }
        Item { width: 1; height: 20 }
        Item {
            width: parent.width; height: 24
            Txt { x: 12; height: parent.height; text: "PROJECTS"; font.pixelSize: 11; font.weight: 600; font.letterSpacing: 0.9; color: Ui.p.text_faint }
            Txt { anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height; text: side.active.length + " active"; font.pixelSize: 12; color: Ui.p.text_faint }
        }
    }

    ListView {
        id: list
        x: 16; width: parent.width - 32
        anchors.top: head.bottom; anchors.topMargin: 4
        anchors.bottom: foot.top; anchors.bottomMargin: 12
        clip: true
        spacing: 2
        boundsBehavior: Flickable.StopAtBounds
        T.ScrollBar.vertical: Bar {}
        model: side.listed
        header: NavItem {
            width: list.width; text: "All projects"; selected: side.current === ""
            count: Ui.int(side.total)
            onClicked: backend.setProject("")
        }
        delegate: NavItem {
            width: list.width
            text: modelData.name
            hasDot: true
            ring: modelData.idle || modelData.color < 0 || modelData.color >= Ui.s.length
            dot: Ui.seriesColor(modelData.color)
            dim: modelData.idle
            selected: side.current === modelData.name
            count: modelData.sessions ? Ui.int(modelData.sessions) : ""
            onClicked: backend.setProject(modelData.name)
        }
        footer: T.AbstractButton {
            id: more
            visible: side.idle.length > 0
            width: list.width; height: visible ? 34 : 0
            hoverEnabled: true
            text: side.showIdle ? "Hide idle projects" : "Show " + side.idle.length + " idle"
            onClicked: side.showIdle = !side.showIdle
            contentItem: Txt { leftPadding: 12; text: more.text; font.pixelSize: 12; color: more.hovered ? Ui.p.text : Ui.p.text_muted }
            background: Rectangle { radius: 10; color: more.hovered ? Ui.alpha(Ui.p.text, 0.05) : "transparent"; Focus { show: more.visualFocus } }
        }
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
                    Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
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
                    visible: side.snap && side.snap.now.live && parent.parent.st === "ok"
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
                    var u = Ui.date(st.updated_at)
                    return u ? "Updated " + Ui.ago(side.nowMs - u.getTime()) : "Waiting for data"
                }
            }
        }
        Txt {
            x: 8; width: parent.width - 16
            visible: !!side.status
            text: side.status ? "From " + Ui.int(side.status.transcripts) + " local transcripts" : ""
            font.pixelSize: 12; color: Ui.p.text_faint
        }
    }
}
