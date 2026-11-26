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
    // Height a page needs to show all of its content (full-page renders).
    readonly property real pageHeight: [overview.contentHeight, sessions.contentHeight, settings.contentHeight][pages.currentIndex]

    function load() { root.snap = JSON.parse(backend.snapshotJson) }
    Component.onCompleted: load()
    Connections {
        target: backend
        function onSnapshotChanged() { root.load() }
        function onPageRequested(p) { root.page = p }
    }

    onClosing: function (close) {
        if (!backend.quitting) {
            close.accepted = false
            root.hide()
        }
    }

    Timer { interval: 1000; repeat: true; running: root.visible; onTriggered: root.nowMs = Date.now() }
    Shortcut { sequences: ["Ctrl+R", "F5"]; onActivated: backend.refresh() }
    Shortcut { sequence: "Ctrl+1"; onActivated: root.page = "overview" }
    Shortcut { sequence: "Ctrl+2"; onActivated: root.page = "sessions" }
    Shortcut { sequence: "Ctrl+,"; onActivated: root.page = "settings" }

    Sidebar {
        id: side
        height: parent.height
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

        OverviewPage { id: overview; snap: root.snap; nowMs: root.nowMs; onOpenSessions: root.page = "sessions" }
        SessionsPage { id: sessions; snap: root.snap }
        SettingsPage { id: settings; snap: root.snap }
    }
}
