const root = element;
const find = (role) => root.querySelector(`[data-role="${role}"]`);
const canvas = find("roll"), scroll = root.querySelector(".roll-scroll"), ctx = canvas.getContext("2d");
const track = find("track"), instrument = find("instrument"), detail = find("detail");
const trackMenu = find("track-options");
const row = 13, keys = 54, header = 52;
let palette = {};
function updatePalette() {
  const style = getComputedStyle(root);
  for (const key of ["text", "muted", "soft", "grid-white", "grid-black", "piano-white", "piano-black", "grid-line", "octave-line", "wave", "region", "region-head", "note-outline", "playhead"])
    palette[key] = style.getPropertyValue(`--ss-${key}`).trim();
  draw();
}
let data = {notes: [], instruments: [], duration: 20.48, waveform: []};
const trackPalette = ["#79a8e8", "#e68c93", "#69c3a6", "#b091dc", "#e5b05f", "#68bed5", "#d68ec1", "#a9ba66",
                      "#df9a70", "#7b9ab7", "#9894e2", "#d9c669", "#66a58e", "#b57f9b", "#aba5be", "#afa293"];
let trackColors = new Map();
let selected = null, program = 0, extraTracks = [], undo = [], redo = [], drag = null;
let lastSent = null, width = 900, timeline = 846, playing = null, clipAudio = null, audioContext = null, previewStarted = 0, playbackFrame = null;
function originalPlayer() {
  const url = document.querySelector('#source-audio a[download]')?.href;
  if (!url) return null;
  if (!clipAudio || clipAudio.src !== url) clipAudio = new Audio(url);
  return clipAudio;
}
const clone = (value) => JSON.parse(JSON.stringify(value));
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const noteName = (pitch) => ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][pitch % 12] + (Math.floor(pitch / 12) - 1);
const name = (p) => data.instruments.find(i => i.members.includes(p))?.name || `Program ${p}`;
const trackColor = (p) => trackColors.get(p) || trackPalette[0];
const snap = (time) => { const unit = Number(find("snap").value); return unit ? Math.round(time / unit) * unit : time; };
const snapshot = () => ({notes: clone(data.notes), program, extraTracks: [...extraTracks], colors: [...trackColors]});
function remember(before) { undo.push(before); if (undo.length > 50) undo.shift(); redo = []; }
function publish() {
  lastSent = JSON.stringify(data);
  props.value = lastSent;
  refresh(); draw();
}
function change(fn) { if (!data.clip) return; remember(snapshot()); fn(); publish(); }
function options(select, entries, value) {
  select.replaceChildren(...entries.map(([v, label]) => { const item = document.createElement("option"); item.value = v; item.textContent = label; return item; }));
  select.value = String(value);
}
function refresh() {
  const programs = [...new Set([...data.notes.map(n => n.program), ...extraTracks, program])].sort((a,b) => a-b);
  // Preserve colors while editing; reuse vacant slots for newly added tracks.
  for (const p of trackColors.keys()) if (!programs.includes(p)) trackColors.delete(p);
  for (const p of programs) if (!trackColors.has(p)) {
    const used = new Set(trackColors.values());
    trackColors.set(p, trackPalette.find(color => !used.has(color)) || trackPalette[programs.indexOf(p) % trackPalette.length]);
  }
  const trackLabel = (p) => `${name(p)} · ${data.notes.filter(n => n.program === p).length}`;
  find("track-name").textContent = trackLabel(program);
  track.style.setProperty("--track-color", trackColor(program));
  trackMenu.replaceChildren(...programs.map(p => {
    const item = document.createElement("button");
    item.type = "button"; item.tabIndex = -1; item.dataset.program = p;
    item.setAttribute("role", "option"); item.setAttribute("aria-selected", String(p === program));
    item.style.setProperty("--track-color", trackColor(p));
    const swatch = document.createElement("span"); swatch.className = "track-swatch"; swatch.setAttribute("aria-hidden", "true");
    const label = document.createElement("span"); label.textContent = trackLabel(p);
    item.append(swatch, label); return item;
  }));
  const choices = data.instruments.map(i => [i.program, i.name]);
  const entry = data.instruments.find(i => i.members.includes(program));
  if (!entry) choices.push([program, name(program)]);
  options(instrument, choices, entry ? entry.program : program);
  find("count").textContent = `${data.notes.length} notes · ${data.duration.toFixed(2)} s`;
  root.querySelector('[data-action="undo"]').disabled = !undo.length;
  root.querySelector('[data-action="redo"]').disabled = !redo.length;
  const note = data.notes.find(n => n.id === selected);
  if (note) find("velocity").value = note.velocity;
  detail.textContent = note ? `${name(note.program)} · ${noteName(note.pitch)} · ${note.start.toFixed(2)}–${(note.start + note.duration).toFixed(2)} s · velocity ${note.velocity}` : (data.clip ? "Choose a track. Double-click an empty cell to add a note." : "Load a clip to begin.");
}
function region() {
  return ["edit-start", "edit-end"].map(id => Number(document.querySelector(`#${id} input`)?.value || 0));
}
function draw(playhead) {
  const scale = window.devicePixelRatio || 1;
  width = Math.max(620, scroll.clientWidth) * Number(find("zoom").value);
  timeline = width - keys;
  const height = header + row * 128;
  if (canvas.width !== Math.round(width * scale) || canvas.height !== Math.round(height * scale)) {
    canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
    canvas.style.width = width + "px"; canvas.style.height = height + "px";
  }
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.font = "10px system-ui";
  for (let pitch = 127; pitch >= 0; pitch--) {
    const y = header + (127 - pitch) * row;
    const black = [1,3,6,8,10].includes(pitch % 12);
    ctx.fillStyle = black ? palette["grid-black"] : palette["grid-white"]; ctx.fillRect(keys,y,timeline,row);
    ctx.fillStyle = black ? palette["piano-black"] : palette["piano-white"]; ctx.fillRect(0,y,keys,row);
    ctx.strokeStyle = pitch % 12 === 0 ? palette["octave-line"] : palette["grid-line"]; ctx.beginPath(); ctx.moveTo(0,y+row); ctx.lineTo(width,y+row); ctx.stroke();
    if (pitch % 12 === 0 || pitch === data.notes.find(n => n.id === selected)?.pitch) {ctx.fillStyle = palette.muted; ctx.fillText(noteName(pitch),10,y+10);}
  }
  const [start, end] = region();
  if (end > start) { ctx.fillStyle = palette.region; ctx.fillRect(keys + start / data.duration * timeline,header,(end-start)/data.duration*timeline,row*128); }
  const tick = timeline / data.duration < 60 ? 2 : 1;
  for (let t = 0; t <= data.duration; t += tick) {
    const x = keys + t / data.duration * timeline;
    ctx.strokeStyle = palette["grid-line"]; ctx.beginPath(); ctx.moveTo(x,header); ctx.lineTo(x,height); ctx.stroke();
  }
  // Draw other instruments first so the selected track remains directly editable.
  [...data.notes].sort((a,b) => Number(a.program === program)-Number(b.program === program)).forEach(n => {
    const x = keys+n.start/data.duration*timeline, y = header+(127-n.pitch)*row+1;
    const w = Math.max(3,n.duration/data.duration*timeline);
    ctx.globalAlpha = n.program === program ? 1 : .6;
    ctx.fillStyle = trackColor(n.program); ctx.fillRect(x,y,w,row-2);
    ctx.strokeStyle = n.id === selected ? palette["note-outline"] : "#7f91b077"; ctx.lineWidth = n.id === selected ? 2 : 1; ctx.strokeRect(x,y,w,row-2);
    if (n.program === program && w > 25) {ctx.fillStyle = "#3b4d6c"; ctx.fillText(noteName(n.pitch),x+4,y+9);}
  });
  ctx.globalAlpha = 1; ctx.lineWidth = 1;
  const top = scroll.scrollTop;
  ctx.fillStyle = palette.soft; ctx.fillRect(0,top,width,header);
  const wave = data.waveform || [];
  ctx.strokeStyle = palette.wave; ctx.beginPath();
  wave.forEach((pair,i) => {const x = keys+i/wave.length*timeline; ctx.moveTo(x,top+28+pair[0]*19); ctx.lineTo(x,top+28+pair[1]*19);}); ctx.stroke();
  if (end > start) {ctx.fillStyle = palette["region-head"]; ctx.fillRect(keys+start/data.duration*timeline,top,(end-start)/data.duration*timeline,header);}
  for(let t=0; t<=data.duration; t+=tick) {ctx.fillStyle = palette.muted; ctx.fillText(`${t}s`,keys+t/data.duration*timeline+4,top+13);}
  if (playhead !== undefined) { const x = keys+playhead/data.duration*timeline; ctx.strokeStyle = palette.playhead; ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,height); ctx.stroke(); }
}
function centerTrack() {
  const pitches = data.notes.filter(n => n.program === program).map(n => n.pitch);
  const pitch = pitches.length ? pitches.reduce((a,b) => a+b,0)/pitches.length : 60;
  scroll.scrollTop = Math.max(0,header+(127-pitch)*row-scroll.clientHeight/2);
}
function position(event) {
  const box = canvas.getBoundingClientRect();
  const x = event.clientX-box.left, y = event.clientY-box.top;
  return {x,y,time: clamp((x-keys)/timeline*data.duration,0,data.duration),pitch:clamp(127-Math.floor((y-header)/row),0,127)};
}
function hit(p) { return [...data.notes].reverse().find(n => n.program === program && n.pitch === p.pitch && p.time >= n.start && p.time <= n.start+n.duration); }
canvas.addEventListener("pointerdown", event => {
  if (!data.clip) return;
  const p = position(event);
  scroll.focus({preventScroll:true});
  if (p.y < scroll.scrollTop+header) { const audio = originalPlayer(); if(audio) audio.currentTime = p.time; return; }
  const note = hit(p); selected = note?.id ?? null;
  if (note) {
    drag = {id:note.id, before:snapshot(), original:clone(note), pointer:p, resize: Math.abs(p.x-(keys+(note.start+note.duration)/data.duration*timeline))<7, moved:false};
    canvas.setPointerCapture(event.pointerId);
  }
  refresh(); draw();
});
canvas.addEventListener("pointermove", event => {
  const p = position(event), note = hit(p);
  canvas.style.cursor = note ? (Math.abs(p.x-(keys+(note.start+note.duration)/data.duration*timeline))<7 ? "ew-resize" : "grab") : "crosshair";
  if (!drag) return;
  const current = data.notes.find(n => n.id === drag.id);
  if (!current) return;
  if (Math.abs(p.x-drag.pointer.x)+Math.abs(p.y-drag.pointer.y)<3 && !drag.moved) return;
  drag.moved = true;
  if (drag.resize) current.duration = clamp(snap(p.time)-current.start,.01,data.duration-current.start);
  else {current.start = clamp(snap(drag.original.start+p.time-drag.pointer.time),0,data.duration-current.duration); current.pitch = clamp(drag.original.pitch+p.pitch-drag.pointer.pitch,0,127);}
  refresh(); draw();
});
function finishDrag() {if (!drag) return; if (drag.moved) {remember(drag.before); publish();} drag = null;}
canvas.addEventListener("pointerup", finishDrag);
canvas.addEventListener("pointercancel", finishDrag);
canvas.addEventListener("dblclick", event => {
  const p = position(event);
  if (!data.clip || p.x<keys || p.y<scroll.scrollTop+header || hit(p)) return;
  change(() => {
    const start = clamp(snap(p.time),0,data.duration-.01);
    selected = Math.max(-1,...data.notes.map(n => n.id))+1;
    data.notes.push({id:selected,start,duration:Math.min(.4,data.duration-start),pitch:p.pitch,velocity:90,program});
  });
});
function closeTrackMenu(focus = false) {
  trackMenu.hidden = true; track.setAttribute("aria-expanded", "false");
  if (focus) track.focus({preventScroll:true});
}
function openTrackMenu() {
  trackMenu.hidden = false; track.setAttribute("aria-expanded", "true");
  trackMenu.querySelector('[aria-selected="true"]')?.focus({preventScroll:true});
  trackMenu.querySelector('[aria-selected="true"]')?.scrollIntoView({block:"nearest"});
}
track.addEventListener("click", () => trackMenu.hidden ? openTrackMenu() : closeTrackMenu(true));
track.addEventListener("keydown", event => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {event.preventDefault(); openTrackMenu();}
});
trackMenu.addEventListener("click", event => {
  const item = event.target.closest('[role="option"]');
  if (!item) return;
  program = Number(item.dataset.program); selected = null;
  closeTrackMenu(true); refresh(); draw(); centerTrack();
});
trackMenu.addEventListener("keydown", event => {
  if (event.key === "Escape") {event.preventDefault(); closeTrackMenu(true); return;}
  if (event.key === "Tab") {closeTrackMenu(true); return;}
  const items = [...trackMenu.querySelectorAll('[role="option"]')];
  const index = items.indexOf(document.activeElement);
  let next;
  if (event.key === "ArrowDown") next = (index + 1) % items.length;
  if (event.key === "ArrowUp") next = (index + items.length - 1) % items.length;
  if (event.key === "Home") next = 0;
  if (event.key === "End") next = items.length - 1;
  if (next !== undefined) {event.preventDefault(); items[next]?.focus({preventScroll:true}); items[next]?.scrollIntoView({block:"nearest"});}
});
document.addEventListener("pointerdown", event => {if (!track.parentElement.contains(event.target)) closeTrackMenu();});
instrument.addEventListener("change", () => change(() => {const next = Number(instrument.value); if (!trackColors.has(next)) trackColors.set(next, trackColor(program)); data.notes.filter(n => n.program === program).forEach(n => n.program = next); extraTracks = extraTracks.filter(p => p !== program); program = next;}));
find("zoom").addEventListener("input", () => draw());
find("velocity").addEventListener("change", () => change(() => {const note = data.notes.find(n => n.id === selected); if(note) note.velocity = Math.round(clamp(Number(find("velocity").value)||90,1,127));}));
function travel(from,to) {if (!from.length) return; to.push(snapshot()); const previous = from.pop(); data.notes = previous.notes; program = previous.program; extraTracks = previous.extraTracks; trackColors = new Map(previous.colors); selected = null; publish();}
function remove() {if (selected !== null) change(() => {data.notes = data.notes.filter(n => n.id !== selected); selected = null;});}
function transportState(action) {
  for (const name of ["play", "preview"])
    root.querySelector(`[data-action="${name}"]`).setAttribute("aria-pressed", String(name === action));
}
function stop() {cancelAnimationFrame(playbackFrame); playbackFrame = null; playing?.pause(); playing = null; if(audioContext) audioContext.close(); audioContext = null; transportState(null); draw();}
function animate() {if (!root.isConnected) {stop(); return;} const time = audioContext ? audioContext.currentTime-previewStarted : playing?.currentTime; if(time === undefined) return; if(time>=data.duration || playing?.ended) {stop(); return;} draw(time); playbackFrame = requestAnimationFrame(animate);}
root.addEventListener("click", async event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "undo") travel(undo,redo);
  if (action === "redo") travel(redo,undo);
  if (action === "delete") remove();
  if (action === "add-track") change(() => {const next = data.instruments.find(i => !data.notes.some(n => n.program === i.program) && !extraTracks.includes(i.program)); if(next) {program = next.program; extraTracks.push(program); selected = null; centerTrack();}});
  if (action === "stop") stop();
  if (action === "play") {stop(); const audio = originalPlayer(); playing = audio; if(audio) {if(audio.ended) audio.currentTime = 0; try {await audio.play(); if(playing !== audio) return; transportState("play"); animate();} catch {if(playing === audio) {detail.textContent = "Playback could not start. Use the Original clip player above."; stop();}}}}
  if (action === "preview" && data.clip) {
    stop(); const context = new AudioContext(); audioContext = context; await context.resume(); if(audioContext !== context) return; previewStarted = context.currentTime+.05;
    const notes = data.notes.filter(n => n.program === program);
    notes.forEach(n => {const oscillator = audioContext.createOscillator(), gain = audioContext.createGain(); oscillator.type = "triangle"; oscillator.frequency.value = 440*Math.pow(2,(n.pitch-69)/12); const start = previewStarted+n.start, end = start+n.duration; gain.gain.setValueAtTime(0,start); gain.gain.linearRampToValueAtTime(n.velocity/127*.08,start+.008); gain.gain.setValueAtTime(n.velocity/127*.08,Math.max(start+.008,end-.02)); gain.gain.linearRampToValueAtTime(0,end+.03); oscillator.connect(gain).connect(audioContext.destination); oscillator.start(start); oscillator.stop(end+.04);});
    transportState("preview"); animate();
  }
});
scroll.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {event.preventDefault(); event.shiftKey ? travel(redo,undo) : travel(undo,redo);}
  if (event.key === "Delete" || event.key === "Backspace") {event.preventDefault(); remove();}
  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key) && selected !== null) {event.preventDefault(); change(() => {const n = data.notes.find(n => n.id === selected); if(!n) return; if(event.key === "ArrowUp") n.pitch = Math.min(127,n.pitch+1); if(event.key === "ArrowDown") n.pitch = Math.max(0,n.pitch-1); if(event.key === "ArrowLeft") n.start = Math.max(0,n.start-(Number(find("snap").value)||.04)); if(event.key === "ArrowRight") n.start = Math.min(data.duration-n.duration,n.start+(Number(find("snap").value)||.04));});}
});
document.addEventListener("input", event => {if (event.target.closest?.("#edit-start, #edit-end")) draw();});
const observer = new ResizeObserver(() => draw()); observer.observe(scroll);
const themeObserver = new MutationObserver(updatePalette);
for (let node = root; node; node = node.parentElement)
  themeObserver.observe(node, {attributes:true, attributeFilter:["class"]});
scroll.addEventListener("scroll", () => draw());
function receive() {
  if (props.value === lastSent) return;
  try {const next = JSON.parse(props.value || "{}"); if(!next.clip) {refresh(); draw(); centerTrack(); return;} stop(); closeTrackMenu(); if (next.clip !== data.clip) trackColors.clear(); data = next; data.notes = data.notes.map((n,i) => ({...n,id:i})); program = data.notes[0]?.program ?? 0; extraTracks = []; selected = null; undo = []; redo = []; refresh(); draw(); centerTrack();} catch {detail.textContent = "Unable to read MIDI. Please load the clip again.";}
}
watch("value", receive);
updatePalette();
receive();
