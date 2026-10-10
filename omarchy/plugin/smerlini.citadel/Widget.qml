import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Citadel conversion card, bottom-right of the desktop.
//
// It lives on the layer-shell "bottom" layer: above the wallpaper, below every
// window, so it shows on an empty desktop and never covers your work. It takes no
// input. The data comes from $XDG_RUNTIME_DIR/citadel/status.json, which Citadel
// rewrites about once a second while converting (see src/citadel/status.py).
Item {
  id: root

  property var status: null
  property real now: Date.now() / 1000

  // How long the card stays after a run, and when a "converting" file counts as
  // abandoned (Citadel killed mid-run never writes "idle").
  readonly property int lingerSeconds: 60
  readonly property int staleSeconds: 30

  readonly property bool converting: status !== null && status.state === "converting"
    && now - status.updated < staleSeconds
  readonly property bool lingering: status !== null && status.state === "idle"
    && status.finished > 0 && now - status.finished < lingerSeconds
  readonly property bool shown: converting || lingering

  readonly property var gpu: status && status.gpu ? status.gpu : null
  readonly property int pad: Style.space(14)
  readonly property int cardWidth: Style.space(300)

  function fmtDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds))
    if (seconds < 60) return seconds + "s"
    var minutes = Math.floor(seconds / 60)
    if (minutes < 60) return minutes + "m " + (seconds % 60) + "s"
    return Math.floor(minutes / 60) + "h " + (minutes % 60) + "m"
  }

  function gib(mib) { return (mib / 1024).toFixed(1) }

  FileView {
    id: statusFile
    path: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/citadel/status.json"
    watchChanges: false
    printErrors: false
    onLoaded: {
      try { root.status = JSON.parse(text()) } catch (e) { root.status = null }
    }
    onLoadFailed: root.status = null
  }

  // Citadel replaces the file atomically, which breaks file watches; re-reading
  // a few hundred bytes from /run is cheaper than being clever.
  Timer {
    interval: root.shown ? 1000 : 5000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      root.now = Date.now() / 1000
      statusFile.reload()
    }
  }

  IpcHandler {
    target: "citadel"
    function state(): string { return root.shown ? (root.converting ? "converting" : "done") : "hidden" }
    function ping(): string { return "ok" }
  }

  PanelWindow {
    id: panel
    visible: root.shown || card.opacity > 0
    anchors { bottom: true; right: true }
    implicitWidth: card.width + Style.space(24)
    implicitHeight: card.height + Style.space(24)
    color: "transparent"
    WlrLayershell.namespace: "citadel-widget"
    WlrLayershell.layer: WlrLayer.Bottom
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region {}

    BorderSurface {
      id: card
      width: root.cardWidth
      height: card.borderTop + root.pad + content.implicitHeight + root.pad + card.borderBottom
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.rightMargin: Style.space(24)
      anchors.bottomMargin: Style.space(24)
      color: Util.alpha(Color.background, 0.92)
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
      radius: Style.cornerRadius
      opacity: root.shown ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }

      Column {
        id: content
        x: card.borderLeft + root.pad
        y: card.borderTop + root.pad
        width: card.width - card.borderLeft - card.borderRight - 2 * root.pad
        spacing: Style.spacing.md

        // -- header: icon, name, run progress --------------------------------
        Row {
          width: parent.width
          spacing: Style.spacing.lg
          Text {
            text: ""  // nf-fa-university: the Citadel
            color: Color.accent
            font.family: Style.font.family
            font.pixelSize: Style.font.iconLarge
          }
          Text {
            text: root.converting ? "Citadel · converting" : "Citadel · done"
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
          }
        }

        Item {
          width: parent.width
          height: Style.space(6)
          Rectangle {
            anchors.fill: parent
            color: Util.alpha(Color.popups.text, 0.2)
          }
          Rectangle {
            height: parent.height
            width: root.status && root.status.total > 0
              ? parent.width * root.status.done / root.status.total : 0
            color: Color.accent
            Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
          }
        }

        Text {
          width: parent.width
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          text: {
            if (!root.status) return ""
            var s = root.status
            var line = s.done + " of " + s.total + " papers"
            if (s.failed > 0) line += " · " + s.failed + " failed"
            if (root.converting && s.eta_seconds > 0) line += " · " + root.fmtDuration(s.eta_seconds) + " left"
            if (!root.converting) line += " in " + root.fmtDuration(s.finished - s.started)
            return line
          }
        }

        // -- the paper being converted ----------------------------------------
        Text {
          visible: root.converting && root.status.current_title !== ""
          width: parent.width
          color: Util.alpha(Color.popups.text, 0.8)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
          maximumLineCount: 2
          wrapMode: Text.Wrap
          text: root.status
            ? root.status.current_title + "  (" + root.status.current_pages + " p, "
              + root.fmtDuration(root.now - root.status.current_started) + ")"
            : ""
        }

        // -- GPU ------------------------------------------------------------------
        Item {
          visible: root.gpu !== null
          width: parent.width
          height: Style.space(28)

          Row {
            id: spark
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            height: parent.height
            spacing: 1
            Repeater {
              model: root.status ? root.status.gpu_history : []
              Rectangle {
                width: Style.space(3)
                height: Math.max(1, spark.height * modelData / 100)
                anchors.bottom: parent.bottom
                color: Util.alpha(Color.accent, 0.35 + 0.65 * modelData / 100)
              }
            }
          }

          Text {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.heading
            font.bold: true
            text: root.gpu ? Math.round(root.gpu.load) + "%" : ""
          }
        }

        Text {
          visible: root.gpu !== null
          width: parent.width
          color: Util.alpha(Color.popups.text, 0.8)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          text: root.gpu
            ? "GPU " + root.gib(root.gpu.memory_used) + "/" + root.gib(root.gpu.memory_total) + " GB · "
              + Math.round(root.gpu.temperature) + "°C · " + Math.round(root.gpu.power) + " W"
            : ""
        }

        // -- throughput and library ---------------------------------------------
        Text {
          width: parent.width
          color: Util.alpha(Color.popups.text, 0.65)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          text: {
            if (!root.status) return ""
            var s = root.status
            var parts = []
            if (s.papers_per_minute > 0) parts.push(s.papers_per_minute.toFixed(1) + " papers/min")
            if (s.seconds_per_page > 0) parts.push(s.seconds_per_page.toFixed(1) + " s/page")
            if (s.library_references > 0)
              parts.push(s.library_converted + "/" + s.library_references + " in Markdown")
            return parts.join(" · ")
          }
        }
      }
    }
  }
}
