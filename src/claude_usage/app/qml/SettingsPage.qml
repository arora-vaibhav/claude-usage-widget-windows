import QtQuick

// Every setting the app has, applied as soon as it changes.
Page {
    id: page
    readonly property var s: backend.settings
    readonly property var st: snap ? snap.status : null
    // the stored pair when it is one (config allows 0 < caution < critical <= 1)
    readonly property var th: {
        var t = s.notify_thresholds
        return t && t.length === 2 && t[0] > 0 && t[0] < t[1] && t[1] <= 1 ? t : [Ui.caution, Ui.critical]
    }
    // the overlay never goes below this while it carries text (osd.MIN_OPACITY)
    readonly property real opacityFloor: 0.85
    readonly property real colW: Math.min(cw, 820)

    function setThreshold(i, v) {
        var t = [th[0], th[1]]
        t[i] = Math.round(v * 100) / 100
        // caution stays below critical
        if (i === 0 && t[0] >= t[1]) t[0] = Math.round((t[1] - 0.05) * 100) / 100
        if (i === 1 && t[1] <= t[0]) t[1] = Math.round((t[0] + 0.05) * 100) / 100
        backend.setSetting("notify_thresholds", t)
    }

    title: "Settings"
    subtitle: "Changes apply right away."

    SettingsGroup {
        width: page.colW
        title: "Appearance"
        SettingRow {
            first: true
            label: "Theme"
            hint: "System follows the Windows light or dark setting."
            Seg {
                name: "Theme"
                options: [["system", "System"], ["light", "Light"], ["dark", "Dark"]]
                current: page.s.appearance || "system"
                onPicked: function (k) { backend.setSetting("appearance", k) }
            }
        }
    }

    SettingsGroup {
        width: page.colW
        title: "Overlay"
        SettingRow {
            first: true
            label: "Style"
            hint: "Pill is a slim bar with the limit that runs out first. Card shows every limit your plan reports, when each resets, and today's cost."
            Seg {
                name: "Overlay style"
                options: [["pill", "Pill"], ["card", "Card"]]
                current: page.s.osd_style || "card"
                onPicked: function (k) { backend.setSetting("osd_style", k) }
            }
        }
        SettingRow {
            label: "Opacity when idle"
            hint: Ui.dark ? "Down to 85%, so its text stays readable. The overlay turns fully opaque when you point at it or a limit needs attention."
                          : "The light overlay stays fully opaque so its text stays readable. Switch to dark to let it fade when idle."
            // what the overlay applies: an older config may hold a value under the floor
            Txt { height: 24; width: 44; horizontalAlignment: Text.AlignRight; text: Ui.pct(Ui.dark ? op.value : 1); color: op.enabled ? Ui.p.text : Ui.p.text_faint }
            SliderX {
                id: op
                width: 180
                enabled: Ui.dark
                from: page.opacityFloor; to: 1; stepSize: 0.01
                value: page.s.osd_opacity !== undefined ? Math.max(page.opacityFloor, page.s.osd_opacity) : 1
                Accessible.name: "Overlay opacity"
                onMoved: backend.setSetting("osd_opacity", Math.round(value * 100) / 100)
            }
        }
        SettingRow {
            label: "Keep on top"
            hint: "Stay above other windows."
            Toggle {
                checked: page.s.osd_always_on_top !== false
                Accessible.name: "Keep the overlay on top"
                onToggled: backend.setSetting("osd_always_on_top", checked)
            }
        }
    }

    SettingsGroup {
        width: page.colW
        title: "Startup"
        SettingRow {
            first: true
            label: "Launch at startup"
            hint: "Start Claude Usage when you sign in to Windows."
            Toggle {
                checked: !!page.s.autostart
                Accessible.name: "Launch at startup"
                onToggled: backend.setSetting("autostart", checked)
            }
        }
    }

    SettingsGroup {
        width: page.colW
        title: "Alerts and thresholds"
        SettingRow {
            first: true
            label: "Limit alerts"
            hint: "A notification when a limit crosses a threshold."
            Toggle {
                checked: page.s.notifications_enabled !== false
                Accessible.name: "Limit alerts"
                onToggled: backend.setSetting("notifications_enabled", checked)
            }
        }
        // the thresholds also colour the meters, so they stay live with alerts off
        SettingRow {
            label: "Caution at"
            hint: "Meters turn amber, and an alert fires if alerts are on."
            Txt { height: 24; width: 44; horizontalAlignment: Text.AlignRight; text: Ui.pct(page.th[0]) }
            SliderX {
                id: ca
                width: 180
                from: 0.05; to: 0.95; stepSize: 0.05
                value: page.th[0]
                Accessible.name: "Caution threshold"
                onMoved: page.setThreshold(0, value)
            }
        }
        SettingRow {
            label: "Critical at"
            hint: "Meters turn red, and an alert fires if alerts are on."
            Txt { height: 24; width: 44; horizontalAlignment: Text.AlignRight; text: Ui.pct(page.th[1]) }
            SliderX {
                id: cr
                width: 180
                from: 0.1; to: 1; stepSize: 0.01
                value: page.th[1]
                Accessible.name: "Critical threshold"
                onMoved: page.setThreshold(1, value)
            }
        }
    }

    SettingsGroup {
        width: page.colW
        title: "Data"
        SettingRow {
            first: true
            label: "Transcripts indexed"
            hint: "Claude Code transcripts read from your .claude folder. Kept after Claude Code cleans them up."
            Txt { height: 24; text: page.st ? Ui.int(page.st.transcripts) : "n/a"; font.weight: 600 }
        }
        SettingRow {
            label: "Coverage"
            hint: page.st && page.st.legacy_before ? "Earlier days come from the old daily rollup, with cost and tokens only." : "Every day comes from your transcripts."
            Txt {
                height: 24
                text: page.st && page.st.coverage_from ? "Full detail from " + Ui.md(Ui.date(page.st.coverage_from + "T12:00:00")) + ", " + Ui.date(page.st.coverage_from + "T12:00:00").getFullYear() : "n/a"
                font.weight: 600
            }
        }
        SettingRow {
            label: "Store"
            hint: page.s.store_path || "%LOCALAPPDATA%\\claude-usage\\usage.db"
        }
        SettingRow {
            label: "Re-index"
            hint: "Read every transcript again. Takes a few minutes; the app stays usable."
            Btn {
                text: page.st && page.st.indexing ? "Indexing" : "Re-index"
                glyph: ""
                spin: !!(page.st && page.st.indexing)
                enabled: !(page.st && page.st.indexing)
                onClicked: backend.reindex()
            }
        }
    }

    SettingsGroup {
        width: page.colW
        title: "About"
        SettingRow {
            first: true
            label: "Claude Usage " + backend.version
            hint: "Cost is API-equivalent: tokens priced at published per-token rates. It is not your bill."
            Btn { text: "Check for updates"; onClicked: backend.checkUpdates() }
        }
    }
}
