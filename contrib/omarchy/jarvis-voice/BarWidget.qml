import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons as Commons
import qs.Commons
import qs.Ui

// Left click: start/stop jarvis.service. Right click: talk now (no wake word).
// Middle click: follow the transcript in a terminal.
//
// Feedback is immediate: a click shows an hourglass at once, then the widget polls fast
// until Jarvis confirms (state file says listening / talking, or the unit is inactive).
BarWidget {
  id: root
  moduleName: "jarvis-voice.jarvis"

  readonly property string unit: "jarvis.service"
  readonly property string stateFile: Quickshell.env("XDG_RUNTIME_DIR") + "/jarvis/state"
  property string service: "inactive"   // `systemctl is-active` output
  property string phase: ""             // starting | listening | talking | stopped (written by Jarvis)
  property string pending: ""           // start | stop | poke, while waiting for confirmation

  readonly property bool running: service === "active" || service === "activating"
  readonly property bool ready: running && (phase === "listening" || phase === "talking")
  readonly property bool talking: running && phase === "talking"
  readonly property bool busy: pending !== "" || (running && !ready)

  function refresh() {
    statusProc.running = true
    stateView.reload()
  }

  function settle() {
    if (pending === "start" && ready) pending = ""
    else if (pending === "stop" && !running) pending = ""
    else if (pending === "poke" && (talking || !running)) pending = ""
  }

  onServiceChanged: settle()
  onPhaseChanged: settle()

  function act(what, args) {
    pending = what
    giveUp.restart()
    root.bar.run("systemctl --user " + args + " " + root.unit)
    refresh()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: statusProc
    command: ["systemctl", "--user", "is-active", root.unit]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.service = String(text || "").trim() || "inactive"
    }
  }

  FileView {
    id: stateView
    path: root.stateFile
    watchChanges: true
    printErrors: false
    onLoaded: root.phase = String(text() || "").trim()
    onLoadFailed: root.phase = ""
    onFileChanged: reload()
  }

  Timer {  // fast while something is changing, slow otherwise (catches crashes, CLI use)
    interval: root.busy ? 250 : 3000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {  // never leave the hourglass up forever
    id: giveUp
    interval: 15000
    onTriggered: root.pending = ""
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.busy ? "󰔟" : root.talking ? "󰍬" : "󰗋"  // busy · talking · listening (robot is omarchy.agents)
    active: root.talking
    activeColor: Commons.Color.accent
    dimmed: !root.running && root.pending === ""
    horizontalMargin: 8
    tooltipText: root.pending === "start" || (root.running && !root.ready) ? "Jarvis is starting…"
      : root.pending === "stop" ? "Stopping Jarvis…"
      : root.pending === "poke" ? "Waking Jarvis…"
      : !root.running
        ? (root.service === "failed" ? "Jarvis crashed: click to restart" : "Jarvis is off: click to start")
      : root.talking
        ? "Jarvis is talking. Click to stop Jarvis"
        : "Jarvis is listening for “hey jarvis”. Click to stop, right-click to talk now"
    onPressed: function(b) {
      if (!root.bar) return
      if (b === Qt.MiddleButton) {
        root.bar.run("xdg-terminal-exec journalctl --user -fu " + root.unit + " -o cat")
      } else if (b === Qt.RightButton) {
        if (root.running) root.act("poke", "kill --kill-whom=main -s USR1")
        else root.act("start", "start")
      } else {
        root.act(root.running ? "stop" : "start", root.running ? "stop" : "start")
      }
    }
  }
}
