"use strict";

const examples = JSON.parse(document.getElementById("demo-data").textContent);
const demo = document.getElementById("demo");
const crop = document.getElementById("crop");
const appearance = document.getElementById("appearance");
const scheme = window.matchMedia("(prefers-color-scheme: dark)");
let section = "early-editing";
let current = null;
let players = [];
let active = null;
let frame = null;
let keepPosition = true;
let midiPositions = [];
const svgNS = "http://www.w3.org/2000/svg";
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const timeLabel = n => `${Math.floor(Math.max(0,n)/60)}:${String(Math.floor(Math.max(0,n)%60)).padStart(2,"0")}`;
const decimal = n => Number(n.toFixed(3)).toString();

function formatInstruction(instruction) {
  return escapeHTML(instruction).replace(/Rewrite \d+ \w+ notes|(?:ascending|descending) chromatic line \([\d–-]+\)|Synthesize the highlighted section|(?:Insert|Remove) the (?:missing|indicated) instrument track|(?:Insert|Remove) the coral notes|Render the \d+ s piano passage inpainted by Modulator|(?:before|after)-edit MIDI|\bMIDI\b|surrounding recording/g, "<strong>$&</strong>");
}

function setTheme() {
  const theme = appearance.value === "system" ? (scheme.matches ? "dark" : "light") : appearance.value;
  document.documentElement.dataset.theme = theme;
}
try { appearance.value = localStorage.getItem("spansynth-appearance") || "system"; } catch {}
appearance.addEventListener("change", () => {setTheme();try {localStorage.setItem("spansynth-appearance", appearance.value);} catch {}});
scheme.addEventListener("change", setTheme);
setTheme();

function svgElement(tag, attrs = {}, label) {
  const node = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (label !== undefined) node.textContent = label;
  return node;
}

function noteIdentity(n) { return [n.start.toFixed(4),n.end.toFixed(4),n.pitch,n.program,n.drum,n.velocity].join(":"); }
function changedNotes(notes, other) {
  const counts = new Map();
  other.forEach(n => {const key=noteIdentity(n);counts.set(key,(counts.get(key)||0)+1);});
  return notes.map(n => {const key=noteIdentity(n), count=counts.get(key)||0;if(count){counts.set(key,count-1);return false;}return true;});
}
function drawMidi(instrument = "all") {
  const arrays = current.midi;
  const all = arrays.flatMap(m => m.notes).filter(n => instrument === "all" || String(n.program)+(n.drum?"d":"") === instrument);
  const low = Math.max(0,(all.length?Math.min(...all.map(n=>n.pitch)):48)-3);
  const high = Math.min(127,(all.length?Math.max(...all.map(n=>n.pitch)):72)+3);
  const height = Math.min(260,Math.max(145,(high-low+1)*4+32));
  const width = 720, left = 38, right=10, top=8, bottom=25;
  const plotWidth=width-left-right, plotHeight=height-top-bottom;
  const x = t => left+t/current.duration*plotWidth;
  const y = pitch => top+(high-pitch)/(high-low+1)*plotHeight;
  document.querySelectorAll(".piano-scroll").forEach((container,index)=>{
    const scrollLeft=container.scrollLeft;
    container.replaceChildren();
    const midi=arrays[index];
    const changes=arrays.length>1?changedNotes(midi.notes,arrays[1-index].notes):midi.notes.map(()=>false);
    const svg=svgElement("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":`${midi.label} for ${current.title}. Target ${decimal(current.start)} to ${decimal(current.end)} seconds.`});
    svg.append(svgElement("title",{},`${midi.label}. ${current.instruction}`));
    svg.append(svgElement("rect",{x:x(current.start),y:top,width:x(current.end)-x(current.start),height:plotHeight,fill:"var(--gold-fill)"}));
    for(let p=Math.ceil(low/12)*12;p<=high;p+=12){svg.append(svgElement("line",{x1:left,x2:width-right,y1:y(p),y2:y(p),class:"pitch-line"}));svg.append(svgElement("text",{x:left-6,y:y(p)+4,"text-anchor":"end",class:"pitch-label"},`C${p/12-1}`));}
    for(let t=0;t<=current.duration;t+=4){svg.append(svgElement("line",{x1:x(t),x2:x(t),y1:top,y2:height-bottom,class:"pitch-line"}));svg.append(svgElement("text",{x:x(t),y:height-7,"text-anchor":"middle",class:"tick-label"},`${t}s`));}
    midi.notes.forEach((n,i)=>{
      if(instrument!=="all" && String(n.program)+(n.drum?"d":"")!==instrument) return;
      const first=Math.max(0,n.start),end=Math.min(current.duration,n.end);if(end<=first)return;
      const parts=changes[i]?[[first,Math.min(end,current.start),false],[Math.max(first,current.start),Math.min(end,current.end),true],[Math.max(first,current.end),end,false]]:[[first,end,false]];
      parts.forEach(([a,b,changed])=>{if(b<=a)return;const note=svgElement("rect",{x:x(a),y:y(n.pitch),width:Math.max(1.2,x(b)-x(a)),height:Math.max(2,plotHeight/(high-low+1)-.5),rx:.7,fill:changed?"var(--coral-note)":"var(--teal-note)",stroke:changed?"var(--coral)":"var(--teal)","stroke-width":.7});note.append(svgElement("title",{},`${n.instrument} · MIDI ${n.pitch} · ${decimal(n.start)}–${decimal(n.end)} s${changed?" · changed":""}`));svg.append(note);});
    });
    [current.start,current.end].forEach(t=>svg.append(svgElement("line",{x1:x(t),x2:x(t),y1:top,y2:height-bottom,class:"region-edge"})));
    svg.append(svgElement("line",{x1:left,x2:left,y1:top,y2:height-bottom,class:`playhead ${arrays.length>1?(index?"edited":"before"):""}`}));
    container.append(svg);
    container.scrollLeft=scrollLeft;
    updatePlayhead(midiPositions[index]||0,index,false);
  });
}
function updatePlayhead(t,index,follow=true){
  midiPositions[index]=Math.max(0,Math.min(current.duration,t));
  const roll=demo.querySelectorAll(".piano-scroll")[index];
  const line=roll?.querySelector(".playhead");
  if(!line)return;
  const x=38+midiPositions[index]/current.duration*672;
  line.setAttribute("x1",x);line.setAttribute("x2",x);
  if(follow){
    const pixel=x*roll.querySelector("svg").getBoundingClientRect().width/720;
    if(pixel<roll.scrollLeft+18||pixel>roll.scrollLeft+roll.clientWidth-18){
      roll.scrollLeft=Math.max(0,Math.min(roll.scrollWidth-roll.clientWidth,pixel-roll.clientWidth*.65));
    }
  }
}
function tick(){if(!active||active.audio.paused)return;active.render();updatePlayhead(active.position,active.midiIndex);frame=requestAnimationFrame(tick);}
function stopPlayers(){cancelAnimationFrame(frame);players.forEach(p=>{p.audio.pause();p.audio.removeAttribute("src");p.audio.load();});players=[];active=null;}

function mountPlayer(container, item, midiIndex) {
  const label=`${current.title}: ${item.label}`;
  container.innerHTML=`<button class="play" type="button" aria-label="Play ${escapeHTML(label)}">▶</button><div class="timeline"><div class="rail"><span class="target-span"></span><span class="cursor"></span></div><input class="seek" type="range" min="0" max="${current.duration}" step="0.01" value="0" aria-label="Seek ${escapeHTML(label)}"><span class="target-label">TARGET ${decimal(current.start)}–${decimal(current.end)} s</span></div><span class="time">0:00 / ${timeLabel(current.duration)}</span><span class="audio-error" role="status" hidden></span>`;
  container.style.setProperty("--start",`${current.start/current.duration*100}%`);
  container.style.setProperty("--span",`${(current.end-current.start)/current.duration*100}%`);
  const audio=document.createElement("audio");audio.preload="none";audio.src=item.src;audio.setAttribute("aria-label",label);audio.hidden=true;container.append(audio);
  const button=container.querySelector(".play"),seek=container.querySelector(".seek"),display=container.querySelector(".time"),error=container.querySelector(".audio-error");
  let pending=null, loading=false, manuallySought=false;
  const state={audio,seek,midiIndex,get position(){return pending??audio.currentTime;},render(){const t=pending??audio.currentTime;seek.value=t;seek.setAttribute("aria-valuetext",`${decimal(t)} of ${decimal(current.duration)} seconds`);container.style.setProperty("--progress",`${Math.min(100,t/current.duration*100)}%`);display.textContent=`${timeLabel(t)} / ${timeLabel(current.duration)}`;},setTime(t){const v=Math.max(0,Math.min(current.duration,t));if(audio.readyState){audio.currentTime=v;pending=null;}else{pending=v;if(!loading){loading=true;audio.load();}}state.render();}};
  const showError=()=>{error.hidden=false;error.textContent="Audio could not be loaded. Try playing it again.";button.textContent="▶";button.setAttribute("aria-label",`Play ${label}`);};
  button.addEventListener("click",()=>{
    if(!audio.paused){audio.pause();return;}
    error.hidden=true;
    if(audio.error){loading=false;audio.load();}
    const previous=active;
    if(keepPosition&&!manuallySought&&previous&&previous!==state){state.setTime(previous.audio.ended?0:previous.position);}
    manuallySought=false;
    players.forEach(p=>{if(p!==state)p.audio.pause();});
    active=state;
    if(audio.ended||audio.currentTime>=current.duration-.02)state.setTime(0);
    audio.play().catch(e=>{if(e.name!=="AbortError")showError();});
  });
  seek.addEventListener("input",()=>{manuallySought=true;state.setTime(Number(seek.value));updatePlayhead(state.position,midiIndex);});
  audio.addEventListener("loadedmetadata",()=>{loading=false;if(pending!==null){audio.currentTime=Math.min(pending,audio.duration);pending=null;}state.render();});
  audio.addEventListener("play",()=>{players.forEach(p=>{if(p!==state)p.audio.pause();});active=state;button.textContent="Ⅱ";button.setAttribute("aria-label",`Pause ${label}`);cancelAnimationFrame(frame);tick();});
  audio.addEventListener("pause",()=>{button.textContent="▶";button.setAttribute("aria-label",`Play ${label}`);if(active===state)cancelAnimationFrame(frame);state.render();});
  audio.addEventListener("timeupdate",()=>{state.render();if(active===state)updatePlayhead(state.position,midiIndex);});
  audio.addEventListener("ended",()=>{button.textContent="▶";button.setAttribute("aria-label",`Play ${label}`);state.render();});
  audio.addEventListener("error",showError);players.push(state);state.render();
}

function showExample(id, updateURL=true){
  stopPlayers();current=examples.find(e=>e.id===id);
  if(!current){demo.innerHTML='<p class="empty">The exact audio and model conditions for these examples are being checked.</p>';return;}
  const e=current;
  midiPositions=e.midi.map(()=>0);
  crop.value=e.id;
  if(updateURL)history.replaceState(null,"",`#${e.id}`);
  const instruments=new Map();e.midi.flatMap(m=>m.notes).forEach(n=>instruments.set(String(n.program)+(n.drum?"d":""),n.instrument));
  const referenceHTML=e.references.map((r,i)=>`<section class="reference-audio ${r.emphasis?"emphasis":""} ${e.midi.length>1?(i?"edited":"before"):"synthesis"}"><p class="audio-title">${escapeHTML(r.label)}</p><p class="audio-detail">${escapeHTML(r.detail||"")}</p><div class="player" data-reference="${i}"></div></section>`).join("");
  demo.innerHTML=`<div class="example-heading"><div><h3>${escapeHTML(e.title)}</h3><p class="meta">Source ${decimal(e.sourceStart)}–${decimal(e.sourceStart+e.duration)} s · ${decimal(e.duration)} s excerpt</p></div><span class="task">${escapeHTML(e.task)}</span></div><p class="instruction"><strong>Task:</strong> ${formatInstruction(e.instruction)}</p><div class="workspace ${e.task==="Synthesis"?"synthesis":""}"><div class="reference-panel"><div class="midi-panel"><h3 class="panel-heading">MIDI ${e.midi.length>1?"edit":"instruction"}</h3><div class="midi-tools"><div class="legend"><span class="preserved">${e.midi.length>1?"Preserved notes":"MIDI notes"}</span>${e.midi.length>1?'<span class="changed">Changed notes</span>':''}<span class="target">Target</span></div>${instruments.size>1?`<label>Instrument <select id="instrument"><option value="all">All instruments</option>${[...instruments].map(([k,v])=>`<option value="${k}">${escapeHTML(v)}</option>`).join("")}</select></label>`:""}</div>${e.midi.map((m,i)=>`<figure><figcaption class="piano-label ${e.midi.length>1?(i?"edited":"before"):""}">${escapeHTML(m.label)}</figcaption><div class="piano-scroll" tabindex="0" role="region" aria-label="${escapeHTML(m.label)} piano roll"></div></figure>`).join("")}<p class="midi-caption">${e.midi.length>1?"Coral notes mark changes. Teal notes are preserved. The two views share time and pitch axes. Original audio follows the before-edit view. Edited audio and model outputs follow the after-edit view.":"The highlighted interval is synthesized from this MIDI and the surrounding reference audio."}</p><p class="mobile-hint">Swipe the MIDI horizontally to see the full excerpt.</p><div class="midi-links">${e.midi.map(m=>`<a href="${escapeHTML(m.src)}" download>${escapeHTML(m.label)} (.mid) ↓</a>`).join("")}</div></div>${referenceHTML}<div class="transport"><button type="button" id="target-jump">Jump to target</button><label><input type="checkbox" id="keep-position" ${keepPosition?"checked":""}>Keep position when switching audio</label></div>${e.referenceNote?`<p class="availability">${escapeHTML(e.referenceNote)}</p>`:""}</div><section class="output-panel" aria-label="Model outputs"><h3 class="panel-heading">Model outputs</h3><div class="model-list">${e.models.map((m,i)=>`<article class="model ${m.ours?"ours":""}"><div class="model-heading"><h3 class="audio-title">${escapeHTML(m.label)}</h3>${m.ours?'<span class="ours-tag">(ours)</span>':""}</div><p class="audio-detail">${escapeHTML(m.detail||"")}</p><div class="player" data-model="${i}"></div></article>`).join("")}</div>${e.availability?`<p class="availability">${escapeHTML(e.availability)}</p>`:""}</section></div>`;
  demo.querySelectorAll("[data-reference]").forEach(el=>{const i=Number(el.dataset.reference);mountPlayer(el,e.references[i],Math.min(i,e.midi.length-1));});
  demo.querySelectorAll("[data-model]").forEach(el=>mountPlayer(el,e.models[Number(el.dataset.model)],e.midi.length-1));
  drawMidi();
  demo.querySelector("#instrument")?.addEventListener("change",event=>drawMidi(event.target.value));
  demo.querySelector("#keep-position").addEventListener("change",event=>keepPosition=event.target.checked);
  demo.querySelector("#target-jump").addEventListener("click",()=>{players.forEach(p=>p.setTime(e.start));e.midi.forEach((_,i)=>updatePlayhead(e.start,i));});
  const choices=examples.filter(x=>x.section===section);document.getElementById("previous").disabled=choices[0]?.id===e.id;document.getElementById("next").disabled=choices.at(-1)?.id===e.id;
}
function selectSection(key,id,updateURL=true){
  section=key;
  document.querySelectorAll("[data-section]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.section===key)));
  document.getElementById("section-kicker").textContent=key==="ai-assisted-edit"?"POP909 · MODULATOR":key==="comparison"?"SAME-CROP COMPARISONS":"EARLY DEMO";
  document.getElementById("section-title").textContent=key==="ai-assisted-edit"?"AI-assisted edit":key==="comparison"?"Model comparison":key==="early-editing"?"Editing":"Synthesis";
  document.getElementById("ai-edit-intro").hidden=key!=="ai-assisted-edit";
  const choices=examples.filter(e=>e.section===key);
  crop.replaceChildren();
  let group=null;
  for(const e of choices){
    if(key==="comparison"&&group?.label!==e.task){group=document.createElement("optgroup");group.label=e.task;crop.append(group);}
    const option=document.createElement("option");option.value=e.id;option.textContent=e.title;
    (key==="comparison"?group:crop).append(option);
  }
  showExample(id||choices[0]?.id,updateURL);
}
document.querySelectorAll("[data-section]").forEach(b=>b.addEventListener("click",()=>selectSection(b.dataset.section)));
crop.addEventListener("change",()=>showExample(crop.value));
for(const [id,direction] of [["previous",-1],["next",1]])document.getElementById(id).addEventListener("click",()=>{const choices=examples.filter(e=>e.section===section);const position=choices.findIndex(e=>e.id===current?.id);const next=choices[position+direction];if(next)showExample(next.id);});
window.addEventListener("hashchange",()=>{const e=examples.find(x=>x.id===decodeURIComponent(location.hash.slice(1)));if(e)selectSection(e.section,e.id);});
const initial=examples.find(e=>e.id===decodeURIComponent(location.hash.slice(1)))||examples[0];
selectSection(initial?.section||section,initial?.id,false);
if(examples.some(e=>`#${e.id}`===location.hash))requestAnimationFrame(()=>document.getElementById("examples").scrollIntoView());
