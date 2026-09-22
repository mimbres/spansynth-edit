const root = element;
const find = (role) => root.querySelector(`[data-role="${role}"]`);
const canvas = find("roll"), scroll = root.querySelector(".roll-scroll"), ctx = canvas.getContext("2d");
const track = find("track"), instrument = find("instrument"), detail = find("detail"), trackMenu = find("track-options");
const row = 13, keys = 54, header = 52;
const clone = (value) => JSON.parse(JSON.stringify(value));
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const noteName = (pitch) => ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][pitch % 12] + (Math.floor(pitch / 12) - 1);
const trackPalette = ["#79a8e8", "#e68c93", "#69c3a6", "#b091dc", "#e5b05f", "#68bed5", "#d68ec1", "#a9ba66",
                      "#df9a70", "#7b9ab7", "#9894e2", "#d9c669", "#66a58e", "#b57f9b", "#aba5be", "#afa293"];
let palette = {}, data = {notes: [], instruments: [], duration: 20.48, waveform: []}, trackColors = new Map();
let selected = new Set(), program = 0, extraTracks = [], undo = [], redo = [], drag = null, tool = "select";
let lastSent = null, width = 900, timeline = 846, cursorTime = 0, timeRange = null;
let playing = null, audioContext = null, previewStarted = 0, previewOffset = 0, playbackEnd = 0, playbackFrame = null;
let viewFrame = null;
const name = (p) => data.instruments.find(i => i.members.includes(p))?.name || `Program ${p}`;
const trackColor = (p) => trackColors.get(p) || trackPalette[0];
const snap = (time) => {const unit = Number(find("snap").value); return unit ? Math.round(time / unit) * unit : time;};
const chosen = () => data.notes.filter(n => selected.has(n.id));
const snapshot = () => ({notes: clone(data.notes), program, extraTracks: [...extraTracks], colors: [...trackColors]});
function remember(before) {undo.push(before); if (undo.length > 50) undo.shift(); redo = [];}
function syncValue() {
  if (!data.clip) return;
  data.view = {program, colors: [...trackColors], extraTracks: [...extraTracks], zoom: Number(find("zoom").value),
               snap: Number(find("snap").value), scrollTop: scroll.scrollTop, scrollLeft: scroll.scrollLeft};
  const value = JSON.stringify(data);
  if (value !== lastSent) {lastSent = value; props.value = value;}
}
function publish() {refresh(); draw(); syncValue();}
function change(fn) {if (!data.clip) return; remember(snapshot()); fn(); publish();}
function options(select, entries, value) {
  select.replaceChildren(...entries.map(([v, label]) => {const item = document.createElement("option"); item.value = v; item.textContent = label; return item;}));
  select.value = String(value);
}
function selectionRange() {
  if (timeRange) return timeRange;
  const notes = chosen();
  return notes.length ? [Math.min(...notes.map(n => n.start)), Math.max(...notes.map(n => n.start+n.duration))] : null;
}
function refresh() {
  const programs = [...new Set([...data.notes.map(n => n.program), ...extraTracks, program])].sort((a,b) => a-b);
  for (const p of trackColors.keys()) if (!programs.includes(p)) trackColors.delete(p);
  for (const p of programs) if (!trackColors.has(p)) {
    const used = new Set(trackColors.values());
    trackColors.set(p, trackPalette.find(color => !used.has(color)) || trackPalette[programs.indexOf(p) % trackPalette.length]);
  }
  find("track-name").textContent = `${name(program)} · ${data.notes.filter(n => n.program === program).length}`;
  track.style.setProperty("--track-color", trackColor(program));
  trackMenu.replaceChildren(...programs.map(p => {
    const item = document.createElement("button");
    item.type = "button"; item.tabIndex = -1; item.dataset.program = p;
    item.setAttribute("role", "option"); item.setAttribute("aria-selected", String(p === program));
    item.style.setProperty("--track-color", trackColor(p));
    const swatch = document.createElement("span"); swatch.className = "track-swatch"; swatch.setAttribute("aria-hidden", "true");
    const label = document.createElement("span"); label.textContent = `${name(p)} · ${data.notes.filter(n => n.program === p).length}`;
    item.append(swatch, label); return item;
  }));
  const choices = data.instruments.map(i => [i.program, i.name]);
  const entry = data.instruments.find(i => i.members.includes(program));
  if (!entry) choices.push([program, `Imported program ${program} · choose an instrument`]);
  options(instrument, choices, entry ? entry.program : program);
  find("count").textContent = `${data.notes.length} notes · ${data.duration.toFixed(2)} s`;
  for (const action of ["undo", "redo"]) root.querySelector(`[data-action="${action}"]`).disabled = !(action === "undo" ? undo : redo).length;
  const notes = chosen();
  root.querySelector('[data-action="delete"]').disabled = !notes.length;
  if (notes.length) find("velocity").value = notes[0].velocity;
  const range = selectionRange();
  find("listen-range").textContent = find("listen").value === "selection" && range ? `${range[0].toFixed(2)}–${range[1].toFixed(2)} s` : `Cursor · ${cursorTime.toFixed(2)} s`;
  if (!data.clip) detail.textContent = "Load a clip to begin.";
  else if (notes.length === 1) {const n = notes[0]; detail.textContent = `${name(n.program)} · ${noteName(n.pitch)} · ${n.start.toFixed(2)}–${(n.start+n.duration).toFixed(2)} s · velocity ${n.velocity}`;}
  else if (notes.length) detail.textContent = `${notes.length} notes selected · drag to move together, ↑ ↓ to transpose, Del to remove.`;
  else detail.textContent = `${name(program)} · ${tool === "pencil" ? "Click or drag to draw a note." : tool === "erase" ? "Click or drag over notes to erase." : "Click any note to edit it. Drag empty space to select a group."}`;
}
function region() {return ["edit-start", "edit-end"].map(id => Number(document.querySelector(`#${id} input`)?.value || 0));}
function draw(playhead = cursorTime) {
  if (!scroll.clientWidth) return;
  const scale = window.devicePixelRatio || 1;
  width = Math.max(620, scroll.clientWidth) * Number(find("zoom").value); timeline = width-keys;
  const height = header+row*128;
  if (canvas.width !== Math.round(width*scale) || canvas.height !== Math.round(height*scale)) {
    canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
    canvas.style.width = width+"px"; canvas.style.height = height+"px";
  }
  ctx.setTransform(scale,0,0,scale,0,0); ctx.clearRect(0,0,width,height); ctx.font = "10px system-ui";
  for (let pitch=127; pitch>=0; pitch--) {
    const y = header+(127-pitch)*row, black = [1,3,6,8,10].includes(pitch%12);
    ctx.fillStyle = black ? palette["grid-black"] : palette["grid-white"]; ctx.fillRect(keys,y,timeline,row);
    ctx.fillStyle = black ? palette["piano-black"] : palette["piano-white"]; ctx.fillRect(0,y,keys,row);
    ctx.strokeStyle = pitch%12 === 0 ? palette["octave-line"] : palette["grid-line"]; ctx.beginPath(); ctx.moveTo(0,y+row); ctx.lineTo(width,y+row); ctx.stroke();
    if (pitch%12 === 0) {ctx.fillStyle = palette.muted; ctx.fillText(noteName(pitch),10,y+10);}
  }
  const [start,end] = region(), range = selectionRange();
  if (end>start) {ctx.fillStyle = palette.region; ctx.fillRect(keys+start/data.duration*timeline,header,(end-start)/data.duration*timeline,row*128);}
  const tick = timeline/data.duration<60 ? 2 : 1;
  for (let t=0; t<=data.duration; t+=tick) {const x=keys+t/data.duration*timeline; ctx.strokeStyle=palette["grid-line"]; ctx.beginPath(); ctx.moveTo(x,header); ctx.lineTo(x,height); ctx.stroke();}
  [...data.notes].sort((a,b) => Number(a.program===program)-Number(b.program===program)).forEach(n => {
    const x=keys+n.start/data.duration*timeline, y=header+(127-n.pitch)*row+1, w=Math.max(3,n.duration/data.duration*timeline);
    ctx.globalAlpha = n.program===program || selected.has(n.id) ? 1 : .6;
    ctx.fillStyle=trackColor(n.program); ctx.fillRect(x,y,w,row-2);
    ctx.strokeStyle=selected.has(n.id) ? palette["note-outline"] : "#7f91b077"; ctx.lineWidth=selected.has(n.id) ? 2 : 1; ctx.strokeRect(x,y,w,row-2);
    if ((n.program===program || selected.has(n.id)) && w>25) {ctx.fillStyle="#283747"; ctx.fillText(noteName(n.pitch),x+4,y+9);}
  });
  ctx.globalAlpha=1; ctx.lineWidth=1;
  if (drag?.mode === "box") {
    ctx.fillStyle="#79a8e82b"; ctx.strokeStyle="#79a8e8";
    const x=Math.min(drag.pointer.x,drag.current.x), y=Math.min(drag.pointer.y,drag.current.y);
    const w=Math.abs(drag.current.x-drag.pointer.x), h=Math.abs(drag.current.y-drag.pointer.y);
    ctx.fillRect(x,y,w,h); ctx.strokeRect(x,y,w,h);
  }
  const top=scroll.scrollTop;
  ctx.fillStyle=palette.soft; ctx.fillRect(0,top,width,header);
  const wave=data.waveform || []; ctx.strokeStyle=palette.wave; ctx.beginPath();
  wave.forEach((pair,i) => {const x=keys+i/wave.length*timeline; ctx.moveTo(x,top+28+pair[0]*19); ctx.lineTo(x,top+28+pair[1]*19);}); ctx.stroke();
  if (end>start) {ctx.fillStyle=palette["region-head"]; ctx.fillRect(keys+start/data.duration*timeline,top,(end-start)/data.duration*timeline,header);}
  if (range) {ctx.fillStyle="#79a8e830"; ctx.fillRect(keys+range[0]/data.duration*timeline,top,(range[1]-range[0])/data.duration*timeline,header); ctx.strokeStyle="#79a8e8"; ctx.strokeRect(keys+range[0]/data.duration*timeline,top+1,(range[1]-range[0])/data.duration*timeline,header-2);}
  for (let t=0;t<=data.duration;t+=tick) {ctx.fillStyle=palette.muted; ctx.fillText(`${t}s`,keys+t/data.duration*timeline+4,top+13);}
  const x=keys+playhead/data.duration*timeline; ctx.strokeStyle=palette.playhead; ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,height); ctx.stroke();
}
function updatePalette() {
  const style=getComputedStyle(root);
  for (const key of ["text","muted","soft","grid-white","grid-black","piano-white","piano-black","grid-line","octave-line","wave","region","region-head","note-outline","playhead"]) palette[key]=style.getPropertyValue(`--ss-${key}`).trim();
  draw();
}
function centerTrack() {const pitches=data.notes.filter(n=>n.program===program).map(n=>n.pitch); const pitch=pitches.length ? pitches.reduce((a,b)=>a+b,0)/pitches.length : 60; scroll.scrollTop=Math.max(0,header+(127-pitch)*row-scroll.clientHeight/2);}
function position(event) {const box=canvas.getBoundingClientRect(), x=event.clientX-box.left, y=event.clientY-box.top; return {x,y,time:clamp((x-keys)/timeline*data.duration,0,data.duration),pitch:clamp(127-Math.floor((y-header)/row),0,127)};}
function hit(p, activeOnly=false) {
  const matches=data.notes.filter(n=>(!activeOnly || n.program===program) && n.pitch===p.pitch && p.time>=n.start && p.time<=n.start+n.duration);
  return matches.findLast(n=>n.program===program) || matches.at(-1);
}
function setTool(next) {tool=next; root.dataset.tool=next; for (const button of root.querySelectorAll("[data-tool]")) button.setAttribute("aria-pressed", String(button.dataset.tool===next)); refresh();}
function createNote(p) {
  const start=clamp(snap(p.time),0,data.duration-.01), id=Math.max(-1,...data.notes.map(n=>n.id))+1;
  const note={id,start,duration:Math.min(.4,data.duration-start),pitch:p.pitch,velocity:Math.round(clamp(Number(find("velocity").value)||90,1,127)),program};
  data.notes.push(note); selected=new Set([id]); timeRange=null; find("listen").value="selection";
  return note;
}
function moveNotes(originals, seconds, semitones) {
  const dt=clamp(seconds,-Math.min(...originals.map(n=>n.start)),data.duration-Math.max(...originals.map(n=>n.start+n.duration)));
  const dp=clamp(semitones,-Math.min(...originals.map(n=>n.pitch)),127-Math.max(...originals.map(n=>n.pitch)));
  for (const n of originals) {const current=data.notes.find(item=>item.id===n.id); if(current) {current.start=n.start+dt; current.pitch=n.pitch+dp;}}
  timeRange=null;
}
canvas.addEventListener("pointerdown", event => {
  if (!data.clip || event.button !== 0) return;
  event.preventDefault(); stop(); scroll.focus({preventScroll:true});
  const p=position(event); if (p.x<keys) return;
  canvas.setPointerCapture(event.pointerId);
  if (p.y<scroll.scrollTop+header) {
    selected.clear(); timeRange=null; cursorTime=p.time; find("listen").value="cursor";
    drag={mode:"time",pointer:p,moved:false}; refresh(); draw(); return;
  }
  const n=hit(p,tool==="pencil"), before=snapshot();
  if (tool==="erase") {
    drag={mode:"erase",before,moved:!!n}; if(n) data.notes=data.notes.filter(item=>item.id!==n.id); selected.clear(); timeRange=null;
  } else if (tool==="pencil" && !n) {
    const note=createNote(p); drag={mode:"draw",id:note.id,before,pointer:p,moved:true};
  } else if (n) {
    program=n.program; timeRange=null;
    if (event.shiftKey) {selected.has(n.id) ? selected.delete(n.id) : selected.add(n.id);}
    else if (!selected.has(n.id)) selected=new Set([n.id]);
    find("listen").value="selection";
    if (selected.has(n.id)) drag={mode:selected.size===1 && n.duration/data.duration*timeline>14 && Math.abs(p.x-(keys+(n.start+n.duration)/data.duration*timeline))<7 ? "resize" : "move",id:n.id,before,originals:clone(chosen()),pointer:p,moved:false};
  } else {
    const base=event.shiftKey ? [...selected] : []; selected=new Set(base); timeRange=null;
    drag={mode:"box",pointer:p,current:p,base,moved:false};
  }
  refresh(); draw();
});
canvas.addEventListener("pointermove", event => {
  const p=position(event);
  if (!drag) {const n=hit(p); canvas.style.cursor=tool==="select" ? (n ? "grab" : "crosshair") : ""; return;}
  if (drag.pointer && Math.abs(p.x-drag.pointer.x)+Math.abs(p.y-drag.pointer.y)<3 && !drag.moved) return;
  if (drag.mode==="erase") {const n=hit(p); if(n) {data.notes=data.notes.filter(item=>item.id!==n.id); drag.moved=true;}}
  else {
    drag.moved=true;
    if (drag.mode==="time") {timeRange=[Math.min(p.time,drag.pointer.time),Math.max(p.time,drag.pointer.time)]; find("listen").value="selection";}
    if (drag.mode==="box") {
      drag.current=p;
      const low=Math.min(p.pitch,drag.pointer.pitch), high=Math.max(p.pitch,drag.pointer.pitch), first=Math.min(p.time,drag.pointer.time), last=Math.max(p.time,drag.pointer.time);
      selected=new Set([...drag.base,...data.notes.filter(n=>(find("scope").value==="all" || n.program===program) && n.pitch>=low && n.pitch<=high && n.start<last && n.start+n.duration>first).map(n=>n.id)]);
      find("listen").value="selection";
    }
    if (drag.mode==="move") moveNotes(drag.originals,snap(drag.originals[0].start+p.time-drag.pointer.time)-drag.originals[0].start,p.pitch-drag.pointer.pitch);
    if (drag.mode==="draw" || drag.mode==="resize") {const n=data.notes.find(n=>n.id===drag.id); if(n) n.duration=clamp(snap(p.time)-n.start,.01,data.duration-n.start); timeRange=null;}
  }
  refresh(); draw();
});
function finishDrag() {
  if (!drag) return;
  const previous=drag; drag=null;
  if (previous.moved && previous.before) remember(previous.before);
  publish();
}
for (const event of ["pointerup","pointercancel","lostpointercapture"]) canvas.addEventListener(event,finishDrag);
canvas.addEventListener("dblclick", event=>{const p=position(event); if(data.clip && tool==="select" && p.x>=keys && p.y>=scroll.scrollTop+header && !hit(p)) change(()=>createNote(p));});
function closeTrackMenu(focus=false) {trackMenu.hidden=true; track.setAttribute("aria-expanded","false"); if(focus) track.focus({preventScroll:true});}
function openTrackMenu() {trackMenu.hidden=false; track.setAttribute("aria-expanded","true"); const item=trackMenu.querySelector('[aria-selected="true"]'); item?.focus({preventScroll:true}); item?.scrollIntoView({block:"nearest"});}
track.addEventListener("click",()=>trackMenu.hidden ? openTrackMenu() : closeTrackMenu(true));
track.addEventListener("keydown",event=>{if(["ArrowDown","ArrowUp"].includes(event.key)) {event.preventDefault(); openTrackMenu();}});
trackMenu.addEventListener("click",event=>{const item=event.target.closest('[role="option"]'); if(!item)return; stop(); program=Number(item.dataset.program); selected.clear(); timeRange=null; closeTrackMenu(true); refresh(); centerTrack(); draw(); syncValue();});
trackMenu.addEventListener("keydown",event=>{
  if(["Escape","Tab"].includes(event.key)) {closeTrackMenu(true); return;}
  const items=[...trackMenu.querySelectorAll('[role="option"]')], index=items.indexOf(document.activeElement);
  const next={ArrowDown:(index+1)%items.length,ArrowUp:(index+items.length-1)%items.length,Home:0,End:items.length-1}[event.key];
  if(next!==undefined) {event.preventDefault(); items[next]?.focus({preventScroll:true}); items[next]?.scrollIntoView({block:"nearest"});}
});
instrument.addEventListener("change",()=>change(()=>{const next=Number(instrument.value); if(!trackColors.has(next))trackColors.set(next,trackColor(program)); data.notes.filter(n=>n.program===program).forEach(n=>n.program=next); extraTracks=extraTracks.filter(p=>p!==program); program=next; extraTracks.push(next);}));
find("zoom").addEventListener("input",()=>{draw(); syncValue();});
find("snap").addEventListener("change",syncValue);
find("listen").addEventListener("change",()=>{stop(); refresh(); draw();});
find("audio-source").addEventListener("change",stop);
find("velocity").addEventListener("change",()=>{if(selected.size)change(()=>{for(const n of chosen())n.velocity=Math.round(clamp(Number(find("velocity").value)||90,1,127));});});
function travel(from,to) {if(!from.length)return; stop(); to.push(snapshot()); const previous=from.pop(); data.notes=previous.notes; program=previous.program; extraTracks=previous.extraTracks; trackColors=new Map(previous.colors); selected.clear(); timeRange=null; publish();}
function remove() {if(selected.size)change(()=>{data.notes=data.notes.filter(n=>!selected.has(n.id)); selected.clear(); timeRange=null;});}
function transportState(action) {for(const name of ["play","preview"])root.querySelector(`[data-action="${name}"]`).setAttribute("aria-pressed",String(name===action));}
function stop() {
  if (playing) cursorTime=clamp(playing.currentTime,0,data.duration);
  if (audioContext) cursorTime=clamp(audioContext.currentTime-previewStarted+previewOffset,0,data.duration);
  cancelAnimationFrame(playbackFrame); playbackFrame=null;
  playing?.pause(); playing=null;
  const context=audioContext; audioContext=null; if(context && context.state!=="closed")context.close().catch(()=>{});
  transportState(null); draw();
}
function animate() {
  if(!root.isConnected){stop();return;}
  const time=audioContext ? audioContext.currentTime-previewStarted+previewOffset : playing?.currentTime;
  if(time===undefined)return;
  if(time>=playbackEnd || playing?.ended) {stop(); cursorTime=playbackEnd; refresh(); draw(); return;}
  find("listen-range").textContent=`Playing · ${Math.max(0,time).toFixed(2)} s`;
  draw(Math.max(0,time)); playbackFrame=requestAnimationFrame(animate);
}
function playbackRange() {
  const range=selectionRange();
  if(find("listen").value==="selection" && range)return range;
  return [find("listen").value==="whole" || cursorTime>=data.duration ? 0 : cursorTime,data.duration];
}
async function play(action) {
  stop(); if(!data.clip)return;
  const [start,end]=playbackRange(); if(end<=start)return;
  playbackEnd=end;
  document.querySelectorAll("audio").forEach(a=>a.pause());
  document.querySelectorAll('#upload-audio button[aria-label="Pause"], #source-audio button[aria-label="Pause"], #result-audio button[aria-label="Pause"]').forEach(button=>button.click());
  if(action==="play") {
    const id=find("audio-source").value==="generated" ? "result-audio" : "source-audio";
    const url=document.querySelector(`#${id} a[download]`)?.href;
    const audio=find("player");
    if(!url) {detail.textContent="Generate audio first, or choose Original.";return;}
    if(audio.src!==url)audio.src=url;
    playing=audio; audio.currentTime=start;
    try {await audio.play(); if(playing!==audio)return; transportState("play"); animate();}
    catch {stop(); detail.textContent="Playback could not start. Try the audio player below.";}
  } else {
    const context=new AudioContext(); audioContext=context; await context.resume(); if(audioContext!==context)return;
    previewStarted=context.currentTime+.03; previewOffset=start;
    const onlySelected=find("listen").value==="selection" && selected.size && !timeRange;
    const notes=data.notes.filter(n=>(onlySelected ? selected.has(n.id) : n.program===program) && n.start<end && n.start+n.duration>start);
    for(const n of notes) {
      const oscillator=context.createOscillator(), gain=context.createGain(); oscillator.type="triangle"; oscillator.frequency.value=440*Math.pow(2,(n.pitch-69)/12);
      const onset=previewStarted+Math.max(0,n.start-start), finish=previewStarted+Math.min(end,n.start+n.duration)-start;
      gain.gain.setValueAtTime(0,onset); gain.gain.linearRampToValueAtTime(n.velocity/127*.08,Math.min(onset+.008,finish));
      gain.gain.setValueAtTime(n.velocity/127*.08,Math.max(onset+.008,finish-.02)); gain.gain.linearRampToValueAtTime(0,finish+.02);
      oscillator.connect(gain).connect(context.destination); oscillator.start(onset); oscillator.stop(finish+.03);
    }
    transportState("preview"); animate();
  }
}
root.addEventListener("click",event=>{
  const button=event.target.closest("button"); if(!button)return;
  if(button.dataset.tool) {setTool(button.dataset.tool); canvas.style.cursor=""; return;}
  const action=button.dataset.action;
  if(action==="undo")travel(undo,redo);
  if(action==="redo")travel(redo,undo);
  if(action==="delete")remove();
  if(action==="add-track") {
    const used=[...data.notes.map(n=>n.program),...extraTracks];
    const available=data.instruments.filter(i=>!i.members.some(p=>used.includes(p)));
    options(find("new-instrument"),available.map(i=>[i.program,i.name]),available[0]?.program);
    find("add-panel").hidden=false; find("new-instrument").focus();
    root.querySelector('[data-action="confirm-track"]').disabled=!available.length || !data.clip;
  }
  if(action==="cancel-track")find("add-panel").hidden=true;
  if(action==="confirm-track")change(()=>{program=Number(find("new-instrument").value); extraTracks.push(program); selected.clear(); timeRange=null; find("add-panel").hidden=true; setTool("pencil"); canvas.style.cursor=""; centerTrack();});
  if(action==="stop") {stop();refresh();}
  if(action==="restart") {stop();cursorTime=0;find("listen").value="cursor";for(const id of ["source-audio","result-audio"]){const a=document.querySelector(`#${id} audio`);if(a)a.currentTime=0;}refresh();draw();}
  if(action==="play" || action==="preview")play(action).catch(()=>{stop();detail.textContent="Playback could not start. Please try again.";});
});
scroll.addEventListener("keydown",event=>{
  const key=event.key.toLowerCase(), modifier=event.metaKey||event.ctrlKey;
  if(modifier && key==="z") {event.preventDefault();event.shiftKey?travel(redo,undo):travel(undo,redo);return;}
  if(modifier && key==="a") {event.preventDefault();selected=new Set(data.notes.filter(n=>find("scope").value==="all"||n.program===program).map(n=>n.id));timeRange=null;find("listen").value="selection";refresh();draw();return;}
  if(event.key==="Escape") {selected.clear();timeRange=null;find("listen").value="cursor";refresh();draw();}
  if(["Delete","Backspace"].includes(event.key)) {event.preventDefault();remove();}
  if(["v","d","e"].includes(key) && !modifier) {setTool({v:"select",d:"pencil",e:"erase"}[key]);canvas.style.cursor="";}
  if(event.key===" ") {event.preventDefault();playing||audioContext?stop():play("play");}
  if(["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(event.key) && selected.size) {
    event.preventDefault(); const unit=Number(find("snap").value)||.04;
    change(()=>moveNotes(clone(chosen()),event.key==="ArrowLeft"?-unit:event.key==="ArrowRight"?unit:0,event.key==="ArrowUp"?(event.shiftKey?12:1):event.key==="ArrowDown"?-(event.shiftKey?12:1):0));
  }
});
const events=new AbortController();
document.addEventListener("pointerdown",event=>{if(!track.parentElement.contains(event.target))closeTrackMenu();},{signal:events.signal});
document.addEventListener("input",event=>{if(event.target.closest?.("#edit-start, #edit-end"))draw();},{signal:events.signal});
document.addEventListener("click",event=>{
  const button=event.target.closest("#upload-audio button, #source-audio button, #result-audio button");
  const label=button?.getAttribute("aria-label");
  if(label==="Play" || label==="Go to start") {stop();if(label==="Go to start"){cursorTime=0;find("listen").value="cursor";}refresh();draw();}
},{capture:true,signal:events.signal});
document.addEventListener("pause",event=>{if(event.target===playing){stop();refresh();}},{capture:true,signal:events.signal});
window.addEventListener("spansynth-generated",()=>{stop();find("audio-source").value=document.querySelector("#result-audio a[download]")?"generated":"original";refresh();draw();},{signal:events.signal});
window.addEventListener("spansynth-region",()=>requestAnimationFrame(()=>draw()),{signal:events.signal});
const observer=new ResizeObserver(()=>draw());observer.observe(scroll);
const themeObserver=new MutationObserver(updatePalette);
for(let node=root;node;node=node.parentElement)themeObserver.observe(node,{attributes:true,attributeFilter:["class"]});
scroll.addEventListener("scroll",()=>{draw();cancelAnimationFrame(viewFrame);viewFrame=requestAnimationFrame(syncValue);});
const lifetime=new MutationObserver(()=>{if(!root.isConnected){stop();events.abort();observer.disconnect();themeObserver.disconnect();lifetime.disconnect();cancelAnimationFrame(viewFrame);}});
lifetime.observe(root.parentElement,{childList:true});
function receive() {
  if(props.value===lastSent)return;
  try {
    const next=JSON.parse(props.value||"{}");
    if(!next.clip){refresh();draw();centerTrack();return;}
    stop();closeTrackMenu();drag=null;timeRange=null;find("listen").value="cursor";
    if(next.clip!==data.clip){trackColors.clear();cursorTime=0;timeRange=null;}
    data=next;data.notes=data.notes.map((n,i)=>({...n,id:i}));
    const view=data.view||{};if(view.colors)trackColors=new Map(view.colors);
    program=view.program??data.notes[0]?.program??0;extraTracks=view.extraTracks||[];
    find("zoom").value=view.zoom??1;find("snap").value=view.snap??.04;
    selected.clear();undo=[];redo=[];setTool("select");refresh();draw();centerTrack();
    if(view.scrollTop!==undefined)scroll.scrollTop=view.scrollTop;scroll.scrollLeft=view.scrollLeft??0;
    syncValue();
  } catch {detail.textContent="Unable to read MIDI. Please load the clip again.";}
}
watch("value",receive);updatePalette();setTool("select");receive();
