import QtQuick

// Where the money went: share by model, a model table, cost by token type
// and what prompt caching saved.
Card {
    id: card
    property var models: []
    property var split: null
    property var range: null
    readonly property bool wide: width >= 900
    implicitHeight: 2 * padding + head.height + 16 + (wide ? Math.max(tbl.height, tiles.height) : tbl.height + 20 + tiles.height)

    readonly property real total: split ? split.input.cost + split.output.cost + split.cache_read.cost + split.cache_write.cost : 0
    readonly property var cols: [["Cost", 92], ["Share", 70], ["Output", 76], ["Cache hit", 80], ["Calls", 72]]

    Column {
        id: head
        spacing: 2
        Txt { height: 22; text: "Models and cost"; font.pixelSize: 15; font.weight: 600; display: true }
        Txt { text: "Share of cost in the " + (card.range ? card.range.label : "range"); font.pixelSize: 12; color: Ui.p.text_muted }
    }

    Column {
        id: tbl
        y: head.height + 16
        width: card.wide ? parent.width * 0.58 : parent.width
        Row {
            width: parent.width; height: 8
            spacing: 2
            Repeater {
                model: card.models.filter(function (m) { return m.share > 0 })
                Rectangle {
                    width: Math.max(3, (tbl.width - 2 * (card.models.length - 1)) * modelData.share)
                    height: 8
                    radius: 4
                    color: Ui.seriesColor(modelData.color)
                }
            }
        }
        Item { width: 1; height: 14 }
        Item {
            width: parent.width; height: 30
            Txt { height: 30; text: "Model"; font.pixelSize: 12; color: Ui.p.text_faint }
            Row {
                anchors.right: parent.right
                Repeater { model: card.cols; Txt { width: modelData[1]; height: 30; horizontalAlignment: Text.AlignRight; text: modelData[0]; font.pixelSize: 12; color: Ui.p.text_faint } }
            }
        }
        Repeater {
            model: card.models
            Item {
                width: tbl.width; height: 42
                Rectangle { width: parent.width; height: 1; color: Ui.p.line }
                Row {
                    height: parent.height
                    spacing: 10
                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 10; height: 10; radius: 3; color: Ui.seriesColor(modelData.color) }
                    Txt { height: parent.height; text: modelData.name; width: Math.min(implicitWidth, tbl.width - 390 - 80) }
                    Tag { visible: modelData.priced !== "exact"; anchors.verticalCenter: parent.verticalCenter; text: modelData.priced === "family" ? "estimated" : "no price" }
                }
                Row {
                    anchors.right: parent.right
                    height: parent.height
                    Repeater {
                        model: [Ui.cents(modelData.cost), Ui.share(modelData.share), Ui.tok(modelData.output), Ui.pct(modelData.cache_hit, 1), Ui.int(modelData.calls)]
                        Txt {
                            width: card.cols[index][1]; height: 42
                            horizontalAlignment: Text.AlignRight
                            text: modelData
                            font.weight: index === 0 ? 600 : 400
                            color: modelData === "n/a" ? Ui.p.text_faint : Ui.p.text
                        }
                    }
                }
            }
        }
        Txt {
            visible: card.models.some(function (m) { return m.priced !== "exact" })
            width: parent.width
            topPadding: 10
            wrapMode: Text.WordWrap; elide: Text.ElideNone
            font.pixelSize: 12; color: Ui.p.text_faint
            text: "Estimated: priced at its model family's published rate. No price: the model is not in the price table yet."
        }
    }

    Column {
        id: tiles
        x: card.wide ? tbl.width + 32 : 0
        y: card.wide ? tbl.y : tbl.y + tbl.height + 20
        width: card.wide ? parent.width - tbl.width - 32 : parent.width
        spacing: 12
        Grid {
            columns: 2
            columnSpacing: 12; rowSpacing: 12
            width: parent.width
            Repeater {
                model: card.split ? [["Input", card.split.input], ["Output", card.split.output], ["Cache read", card.split.cache_read], ["Cache write", card.split.cache_write]] : []
                Rectangle {
                    width: (tiles.width - 12) / 2; height: 92
                    radius: 12
                    color: Ui.p.track
                    border.color: Ui.p.line
                    Column {
                        x: 16; y: 12; width: parent.width - 32
                        spacing: 1
                        Txt { text: modelData[0]; font.pixelSize: 12; color: Ui.p.text_muted }
                        Txt { text: Ui.money(modelData[1].cost); font.pixelSize: 20; font.weight: 600; height: 28; display: true }
                        Txt {
                            width: parent.width
                            text: Ui.tok(modelData[1].tokens) + " tokens, " + (card.total ? Ui.share(modelData[1].cost / card.total) : "0%") + " of cost"
                            font.pixelSize: 12; color: Ui.p.text_faint
                        }
                        Item { width: 1; height: 6 }
                        Rectangle {
                            width: parent.width; height: 3; radius: 1.5; color: Ui.p.line
                            Rectangle { width: card.total ? parent.width * modelData[1].cost / card.total : 0; height: 3; radius: 1.5; color: Ui.p.text_muted }
                        }
                    }
                }
            }
        }
        Rectangle {
            visible: !!card.split && card.split.caching_saved > 0
            width: parent.width; height: 46
            radius: 12
            color: Ui.p.ok_bg
            Row {
                x: 16; height: parent.height
                spacing: 10
                Icon { height: parent.height; glyph: ""; font.pixelSize: 13; color: Ui.p.ok }
                Text {
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    textFormat: Text.StyledText
                    font.family: Ui.fontUi; font.pixelSize: 13
                    font.features: { "tnum": 1 }
                    color: Ui.p.text
                    text: card.split ? "Prompt caching saved <b><font color='" + Ui.p.ok + "'>" + Ui.money(card.split.caching_saved) + "</font></b> in the " + (card.range ? card.range.label : "range") + ", at API prices." : ""
                }
            }
        }
    }
}
