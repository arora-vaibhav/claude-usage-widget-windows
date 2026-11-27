import QtQuick

// The surface every block sits on: hairline border, a soft lift in light mode,
// a faint top highlight and a gentle downward wash in dark mode.
Item {
    id: card
    default property alias content: inner.data
    property int padding: Ui.pad
    readonly property alias inner: inner

    Rectangle {
        visible: !Ui.dark
        anchors.fill: parent
        anchors.topMargin: 2
        anchors.bottomMargin: -2
        radius: Ui.radius + 1
        color: Ui.alpha("#000000", 0.035)
    }
    Rectangle {
        anchors.fill: parent
        radius: Ui.radius
        border.color: Ui.p.line
        border.width: 1
        gradient: Gradient {
            GradientStop { position: 0; color: Ui.p.surface }
            GradientStop { position: 1; color: Ui.dark ? Ui.mix(Ui.p.surface, Ui.p.bg, 0.45) : Ui.p.surface }
        }
    }
    Rectangle {
        visible: Ui.dark
        x: Ui.radius; width: parent.width - 2 * Ui.radius; y: 1; height: 1
        color: Ui.alpha("#FFFFFF", 0.05)
    }
    Item {
        id: inner
        x: card.padding; y: card.padding
        width: card.width - 2 * card.padding
        height: card.height - 2 * card.padding
    }
}
