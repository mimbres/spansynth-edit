// The same piano roll runs in Spaces and in this isolated ComfyUI dialog.
const compact = value => ({clip: value.clip, duration: value.duration, source: value.source,
                           notes: value.notes, view: value.view});
const contents = value => JSON.stringify({clip: value.clip, source: value.source,
  notes: value.notes?.map(({id, ...note}) => note), extraTracks: value.view?.extraTracks || []});
const viewUrl = file => `/view?${new URLSearchParams(file)}`;

if (document.documentElement.hasAttribute("data-spansynth-editor")) {
  let current = {}, media = {}, bounds = [0, 0], listener, loaded = false, applied = "";
  const status = document.getElementById("status");
  const send = (action, value) => parent.postMessage({spansynth: action, value}, location.origin);
  window.element = document.getElementById("editor");
  element.spansynthHost = {region: () => bounds, audio: id => media[id],
    gmBase: "/spansynth/sounds/", drumUrl: "/spansynth/sounds/drums.js", soundLibrary: "/spansynth/sounds/smplr.mjs"};
  window.props = {};
  Object.defineProperty(props, "value", {
    get: () => JSON.stringify(current),
    set: value => {
      current = JSON.parse(value);
      const dirty = contents(current) !== applied;
      status.textContent = dirty ? "Edits not applied" : "Edits saved in workflow";
      document.getElementById("midi-download").hidden = dirty;
      send("dirty", dirty);
    },
  });
  window.watch = (_key, callback) => {listener = callback;};
  const download = (id, url) => {const a = document.getElementById(id); a.href = url; a.hidden = false;};
  window.addEventListener("message", async event => {
    if (event.origin !== location.origin || event.source !== parent) return;
    const {spansynth, value} = event.data || {};
    if (spansynth === "load" || spansynth === "applied") {
      media["source-audio"] = viewUrl(value.original_audio[0]);
      bounds = value.region[0];
      applied = contents(value.score[0]);
      if (spansynth === "load") {
        current = value.score[0];
        if (!loaded) {
          loaded = true;
          const script = document.createElement("script"); script.src = "/spansynth/assets/editor.js";
          document.body.append(script);
        } else listener?.();
      }
      const dirty = contents(current) !== applied;
      status.textContent = dirty ? "Edits not applied" : "Edits saved in workflow";
      download("midi-download", `data:audio/midi;base64,${value.midi[0]}`);
      document.getElementById("midi-download").hidden = dirty;
      document.getElementById("apply").disabled = false;
      send("dirty", dirty);
      window.dispatchEvent(new Event("spansynth-region"));
    }
    if (spansynth === "generated") {
      bounds = value.region[0];
      media["result-audio"] = viewUrl(value.generated_audio[0]);
      download("audio-download", media["result-audio"]);
      status.textContent = value.text[0];
      window.dispatchEvent(new Event("spansynth-generated"));
    }
    if (spansynth === "error") {
      status.textContent = value;
      document.getElementById("apply").disabled = false;
    }
  });
  document.getElementById("apply").onclick = () => {
    if (!current.clip) return;
    document.getElementById("apply").disabled = true;
    status.textContent = "Applying edits…";
    send("apply", compact(current));
  };
  document.getElementById("close").onclick = () => send("close");
  document.getElementById("theme").onclick = () => document.body.classList.toggle("dark");
  document.body.classList.toggle("dark", matchMedia("(prefers-color-scheme: dark)").matches);
  send("ready");
} else {
  const {app} = await import("../../scripts/app.js");
  const {api} = await import("../../scripts/api.js");
  let opened = null;
  const scoreWidget = node => node.widgets.find(w => w.name === "score");
  const post = (action, value) => opened?.frame.contentWindow?.postMessage({spansynth: action, value}, location.origin);
  const close = () => {
    if (opened?.dirty && !confirm("Close without applying your latest edits?")) return;
    opened?.dialog.remove(); opened = null;
  };
  function open(node) {
    if (opened?.node === node) return;
    if (opened) {close(); if (opened) return;}
    const dialog = document.createElement("dialog");
    dialog.style.cssText = "width:96vw;height:94vh;max-width:1500px;padding:0;border:1px solid #a99bab;border-radius:14px;background:#211e29;overflow:hidden";
    const frame = document.createElement("iframe");
    frame.src = "/spansynth/editor"; frame.title = "SpanSynth-Edit piano roll";
    frame.style.cssText = "width:100%;height:100%;border:0";
    dialog.append(frame); document.body.append(dialog);
    opened = {node, dialog, frame, dirty: false};
    dialog.addEventListener("cancel", event => {event.preventDefault(); close();});
    dialog.showModal();
  }
  async function prepare(node, reset = false) {
    if (reset && scoreWidget(node).value && !confirm("Replace the edited score with the original MIDI?")) return;
    if (reset) {scoreWidget(node).value = ""; node.spansynthAudio = null;}
    node.spansynthPreparing = true;
    await app.queuePrompt(-1, 1, [String(node.id)]);
  }
  window.addEventListener("message", async event => {
    if (!opened || event.origin !== location.origin || event.source !== opened.frame.contentWindow) return;
    const {spansynth, value} = event.data || {}, node = opened.node;
    if (spansynth === "ready") {
      post("load", node.spansynthScore);
      if (node.spansynthAudio) post("generated", node.spansynthAudio);
    }
    if (spansynth === "dirty") opened.dirty = value;
    if (spansynth === "close") close();
    if (spansynth === "apply") {
      scoreWidget(node).value = JSON.stringify(value);
      node.spansynthApplying = true;
      app.graph.setDirtyCanvas(true, true);
      const ok = await app.queuePrompt(-1, 1, [String(node.id)]);
      if (ok === false) {node.spansynthApplying = false; post("error", "Could not apply. Check the ComfyUI error message.");}
    }
  });
  api.addEventListener("execution_error", event => {
    if (opened) post("error", event.detail.exception_message || "Execution failed. Check the ComfyUI error message.");
  });
  app.registerExtension({
    name: "SpanSynth.Edit",
    async beforeRegisterNodeDef(nodeType, nodeData) {
      if (!["SpanSynthEditScore", "SpanSynthGenerate", "SpanSynthLoadMidi"].includes(nodeData.name)) return;
      const created = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function(...args) {
        created?.apply(this, args);
        if (nodeData.name === "SpanSynthEditScore") {
          const widget = scoreWidget(this);
          widget.type = "hidden"; widget.computeSize = () => [0, -4];
          if (widget.element) widget.element.style.display = "none";
          this.addWidget("button", "Prepare / Open score", null, () => prepare(this));
          this.addWidget("button", "Reset score from inputs", null, () => prepare(this, true));
        }
        if (nodeData.name === "SpanSynthLoadMidi") this.addWidget("button", "Upload MIDI", null, () => {
          const input = document.createElement("input"); input.type = "file"; input.accept = ".mid,.midi";
          input.onchange = async () => {
            const file = input.files[0]; if (!file) return;
            const body = new FormData(); body.append("image", file); body.append("type", "input");
            const response = await api.fetchApi("/upload/image", {method: "POST", body});
            if (!response.ok) {alert("MIDI upload failed."); return;}
            const result = await response.json(), path = result.subfolder ? `${result.subfolder}/${result.name}` : result.name;
            const widget = this.widgets.find(w => w.name === "midi");
            if (!widget.options.values.includes(path)) widget.options.values.push(path);
            widget.value = path; app.graph.setDirtyCanvas(true, true);
          };
          input.click();
        });
        if (nodeData.name === "SpanSynthGenerate") this.addWidget("button", "Download latest WAV", null, () => {
          if (!this.spansynthAudio) {alert("Generate audio first."); return;}
          const link = document.createElement("a"); link.href = viewUrl(this.spansynthAudio.generated_audio[0]);
          link.download = "spansynth-edit.wav"; link.click();
        });
      };
      const executed = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function(message) {
        executed?.apply(this, arguments);
        if (message.score) {
          if (this.spansynthScore?.original_audio[0].filename !== message.original_audio[0].filename) this.spansynthAudio = null;
          this.spansynthScore = message;
          // Keep the original score in saved workflows before the first edit, too.
          if (!scoreWidget(this).value) scoreWidget(this).value = JSON.stringify(compact(message.score[0]));
          if (this.spansynthPreparing) {this.spansynthPreparing = false; open(this);}
          else if (opened?.node === this) post(this.spansynthApplying ? "applied" : "load", message);
          this.spansynthApplying = false;
        }
        if (message.generated_audio) {
          this.spansynthAudio = message;
          const editor = this.getInputNode(this.inputs.findIndex(i => i.name === "midi"));
          if (editor?.type === "SpanSynthEditScore") {
            editor.spansynthAudio = message;
            if (opened?.node === editor) post("generated", message);
          }
        }
      };
    },
  });
}
