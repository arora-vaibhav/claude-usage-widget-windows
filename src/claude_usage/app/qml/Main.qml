import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1280
    height: 820
    minimumWidth: 960
    minimumHeight: 640
    visible: false
    title: snap && snap.project ? snap.project + " - Claude Usage" : "Claude Usage"
    color: Ui.p.bg

    property var snap: null
    property string page: "overview"
    property real nowMs: Date.now()
    // The visible height; full-page renders pin it so the hero chart keeps its on-screen size.
    property real viewH: height
    // Height a page needs to show all of its content (full-page renders).
    readonly property real pageHeight: [overview.contentHeight, sessions.contentHeight, settings.contentHeight][pages.currentIndex]

    function load() { root.snap = Ui.fixColors(JSON.parse(backend.snapshotJson)) }
    // a page opened from a shortcut or the menu takes the keys, so it scrolls
    function go(p) { root.page = p; pages.children[pages.currentIndex].forceActiveFocus() }
    Component.onCompleted: { load(); go(root.page) }
    Connections {
        target: backend
        function onSnapshotChanged() { root.load() }
        function onPageRequested(p) { root.go(p) }
    }

    onClosing: function (close) {
        if (!backend.quitting) {
            close.accepted = false
            root.hide()
        }
    }

    Timer { interval: 1000; repeat: true; running: root.visible; onTriggered: root.nowMs = Date.now() }
    Shortcut { sequences: ["Ctrl+R", "F5"]; onActivated: backend.refresh() }
    Shortcut { sequence: "Ctrl+1"; onActivated: root.go("overview") }
    Shortcut { sequence: "Ctrl+2"; onActivated: root.go("sessions") }
    Shortcut { sequence: "Ctrl+,"; onActivated: root.go("settings") }

    Sidebar {
        id: side
        height: parent.height
        width: root.width < 1400 ? 224 : 248
        snap: root.snap
        page: root.page
        nowMs: root.nowMs
        onNavigate: function (p) { root.page = p }
    }

    StackLayout {
        id: pages
        x: side.width
        width: parent.width - side.width
        height: parent.height
        currentIndex: ["overview", "sessions", "settings"].indexOf(root.page)
        opacity: backend.busy ? 0.55 : 1
        Behavior on opacity { NumberAnimation { duration: Ui.fast } }
        // a page arrives with a short rise and fade, once per switch
        transform: Translate { id: rise }
        onCurrentIndexChanged: { fade.target = children[currentIndex]; enter.restart() }
        ParallelAnimation {
            id: enter
            NumberAnimation { target: rise; property: "y"; from: 10; to: 0; duration: 150; easing.type: Easing.OutCubic }
            NumberAnimation { id: fade; property: "opacity"; from: 0; to: 1; duration: 150; easing.type: Easing.OutCubic }
        }

        OverviewPage { id: overview; snap: root.snap; nowMs: root.nowMs; viewH: root.viewH; onOpenSessions: root.go("sessions") }
        // Bound only while shown: 2,000 rows re-sorted on every refresh were half
        // of each update. Its search, filter and sort live on the page and stay.
        SessionsPage { id: sessions; snap: root.page === "sessions" ? root.snap : null }
        SettingsPage { id: settings; snap: root.snap }
    }
}
