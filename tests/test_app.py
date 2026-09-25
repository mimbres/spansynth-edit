"""Web-only tests: MIDI timing, editable data, and the transcription boundary."""
import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("gradio")
pytest.importorskip("spaces")
from app import app
from spansynth.midi import parse_midi_notes, read_notes


def test_shared_editor_midi_recording():
    """Run the shipped editor with MIDI ports and clocks supplied by the test."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the shared editor test")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const elements = new Map();
const context2d = new Proxy({}, {get: (target, name) => target[name] || (() => {})});
class Element {
  constructor() {this.value="";this.hidden=true;this.disabled=false;this.checked=false;this.dataset={};
    this.style={setProperty(){}};this.clientWidth=900;this.clientHeight=380;this.scrollTop=0;this.scrollLeft=0;this.isConnected=true;}
  querySelector(key) {if(!elements.has(key))elements.set(key,new Element());return elements.get(key);}
  querySelectorAll() {return [];}
  addEventListener() {} setAttribute() {} replaceChildren() {} focus() {} append() {}
  getContext() {return context2d;}
}
const element=new Element();element.parentElement=new Element();element.spansynthHost={region:()=>[0,4],audio:()=>null};
element.querySelector('[data-role="zoom"]').value="1";
element.querySelector('[data-role="snap"]').value="0.04";
element.querySelector('[data-role="audio-source"]').value="original";
class Observer {observe() {} disconnect() {}}
let milliseconds=0;
const browser={element,props:{value:"{}"},watch(){},document:{hidden:false,createElement:()=>new Element(),
  querySelectorAll:()=>[],querySelector:()=>null,getElementById:()=>null,addEventListener(){}},
  window:{devicePixelRatio:1,isSecureContext:true,addEventListener(){}},navigator:{},
  ResizeObserver:Observer,MutationObserver:Observer,AbortController,
  getComputedStyle:()=>({getPropertyValue:()=>"#000"}),requestAnimationFrame:()=>1,cancelAnimationFrame(){},
  setTimeout,clearTimeout,performance:{now:()=>milliseconds},console,assert,
  location:{href:"https://example.test/"}};
vm.createContext(browser);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),browser);
vm.runInContext(`(async()=>{
  const approx=(a,b)=>assert.ok(Math.abs(a-b)<1e-8, a+" != "+b);
  const original={id:0,start:.2,duration:.3,pitch:48,velocity:75,program:0};
  data={clip:"test",duration:4,notes:[{...original}],instruments:[{program:0,name:"Piano",members:[0]},{program:16,name:"Organ",members:[16]}]};
  program=16;extraTracks=[16];cursorTime=1;timeRange=[1,2.2];
  let released=0;
  soundfonts.set(16,{start:()=>()=>{released++;}});
  audioContext={currentTime:0,resume:async()=>{}};
  const input={id:"keyboard",name:"Test keyboard",state:"connected",open:async()=>{},close:async()=>{}};
  midiAccess={inputs:new Map([[input.id,input]])};
  await selectMidiInput(input.id);
  const stopRecording=root.querySelector('[data-action="stop-recording"]');
  assert.equal(stopRecording.disabled,true);
  const send=(bytes,time)=>{audioContext.currentTime=time;input.onmidimessage({data:bytes,timeStamp:performance.now()});};
  await recordMidi();assert.ok(recording.ready);assert.equal(track.disabled,true);
  assert.equal(stopRecording.disabled,false);
  send([0x90,60,103],.1);send([0x91,64,87],.1);
  send([0xb0,64,127],.2);send([0x80,60,0],.3);send([0x91,64,0],.4);
  assert.equal(midiNotes.size,1);
  send([0xb0,64,0],.6);
  send([0x90,67,100],.7);await Promise.resolve();await Promise.resolve();
  audioContext.currentTime=.9;stop();
  assert.equal(recording,null);assert.equal(midiNotes.size,0);assert.equal(midiSustain.size,0);
  assert.equal(stopRecording.disabled,true);
  assert.equal(track.disabled,false);assert.equal(data.notes.length,4);assert.deepEqual(data.notes[0],original);
  const [a,b,c]=data.notes.slice(1);approx(a.start,1.1);approx(a.duration,.5);approx(b.duration,.3);approx(c.duration,.2);
  assert.deepEqual([a.velocity,b.velocity,c.velocity],[103,87,100]);assert.ok(data.notes.slice(1).every(n=>n.program===16));
  assert.equal(undo.length,1);assert.equal(JSON.parse(props.value).notes.length,4);
  travel();assert.deepEqual(data.notes,[original]);travel(false);assert.equal(data.notes.length,4);
  // No performance should create an undo entry.
  const history=undo.length;timeRange=null;await recordMidi();stop();assert.equal(undo.length,history);
  // Snap is optional and uses the current grid. Repeated pitches finish the prior note.
  cursorTime=0;audioContext.currentTime=0;find("snap").value="0.1";find("record-snap").checked=true;
  await recordMidi();send([0x90,72,99],.14);send([0x90,72,91],.26);send([0x80,72,0],.39);stop();
  const last=data.notes.slice(-2);approx(last[0].start,.1);approx(last[0].duration,.2);approx(last[1].start,.3);approx(last[1].duration,.1);
  // A take ends at the waveform range, including keys still held.
  find("record-snap").checked=false;timeRange=[3.5,4];audioContext.currentTime=0;
  await recordMidi();send([0x90,76,88],.2);audioContext.currentTime=.7;animate();
  assert.equal(recording,null);approx(data.notes.at(-1).start,3.7);approx(data.notes.at(-1).duration,.3);
  // A new performance replaces only same-pitch overlaps; one Undo restores both parts.
  const long={id:0,start:0,duration:2,pitch:60,velocity:70,program:16,source_onset:-.2};
  data.notes=[{...long},{...original,id:1}];cursorTime=0;timeRange=null;audioContext.currentTime=0;undo=[];redo=[];
  await recordMidi();send([0x90,60,110],.5);send([0x80,60,0],1);stop();
  const fragments=data.notes.filter(n=>n.program===16).sort((a,b)=>a.start-b.start);
  assert.deepEqual(fragments.map(n=>[n.start,n.duration,n.velocity]),[[0,.5,70],[.5,.5,110],[1,1,70]]);
  assert.equal(fragments[0].source_onset,-.2);assert.equal(fragments[2].source_onset,undefined);
  assert.ok(data.notes.some(n=>n.program===0 && n.pitch===48));
  travel();assert.deepEqual(data.notes,[long,{...original,id:1}]);
  // Input loss closes active notes and preserves the take.
  cursorTime=0;timeRange=null;audioContext.currentTime=0;await recordMidi();send([0x90,65,80],.1);
  audioContext.currentTime=.3;input.state="disconnected";midiInputsChanged();
  assert.equal(recording,null);assert.equal(midiInput,null);approx(data.notes.at(-1).duration,.2);
  assert.match(find("midi-status").textContent,/disconnected/);
  // Browser denial and lack of support leave the editor usable.
  disconnectMidi();navigator.requestMIDIAccess=async()=>{throw Error("denied");};await connectMidi();
  assert.match(find("midi-status").textContent,/not granted/);assert.equal(midiConnecting,false);
  delete navigator.requestMIDIAccess;await connectMidi();assert.match(find("midi-status").textContent,/unavailable/);
  assert.equal(track.disabled,false);
  // Backing-audio position, not elapsed JS time, defines the recorded timeline.
  input.state="connected";midiAccess={inputs:new Map([[input.id,input]])};await selectMidiInput(input.id);
  const audio=find("player");audio.play=async()=>{};audio.pause=()=>{};host.audio=()=>"/clip.wav";
  data.notes=[];cursorTime=.8;timeRange=[.8,1.5];await recordMidi();
  audio.currentTime=1.1;send([0x90,70,100],900);audio.currentTime=1.3;send([0x80,70,0],901);stop();
  approx(data.notes[0].start,1.1);approx(data.notes[0].duration,.2);host.audio=()=>null;
  // Stop during sound loading must not start a late recording.
  const loader=loadInstrument;let finishLoading;
  loadInstrument=()=>new Promise(resolve=>{finishLoading=resolve;});
  const pending=recordMidi();await Promise.resolve();stop();finishLoading();await pending;
  assert.equal(recording,null);assert.equal(track.disabled,false);loadInstrument=loader;
  // Monitoring failure does not discard an otherwise usable MIDI take.
  loadInstrument=async()=>{throw Error("offline");};cursorTime=0;await recordMidi();
  assert.ok(recording.ready);stop();loadInstrument=loader;
})()`,browser).catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(
        [node, "-e", script, str(Path(app.__file__).with_name("editor.js"))],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def note(program=0, **changes):
    return dict(start=0.25, duration=0.5, pitch=60, velocity=90, program=program, **changes)


def test_midi_export_preserves_timing_instruments_and_drums(tmp_path):
    notes = [note(0), note(40), note(128)]
    path = app.write_midi(notes, tmp_path / "score.mid")
    parsed = parse_midi_notes(path, program=None).notes
    assert {n.program for n in parsed} == {0, 40, 128}
    for n in parsed:
        assert n.onset == pytest.approx(.25)
        assert n.offset == pytest.approx(.75)
        assert n.velocity == 90
    cropped = app.midi_notes(path, .5, .1)
    assert {n["program"] for n in cropped} == {0, 40}
    assert all(n["start"] == 0 and n["duration"] == pytest.approx(.1) for n in cropped)


def test_export_rejects_channel_collision(tmp_path):
    with pytest.raises(ValueError, match="15 melodic"):
        app.write_midi([note(program) for program in range(16)], tmp_path / "score.mid")


def test_crop_boundary_keeps_sustains_and_does_not_retrigger_drums(tmp_path):
    path = Path(app.write_midi([note(), note(128)], tmp_path / "score.mid"))
    source = app.midi_notes(path, .5, 1)
    session = {"clip": "current", "duration": 1}
    target = app.validate_score(json.dumps({"clip": "current", "notes": source}), session)
    expected = read_notes(path, crop_start=.5)
    assert len(source) == 1 and target[0]["source_onset"] == -.25
    assert np.array_equal(app.note_array(source), expected)
    assert np.array_equal(app.note_array(target), expected)
    mask = app.torch.ones(512, dtype=app.torch.bool)
    actual_rows = app.prepare_midi(app.note_array(target), app.note_array(source), mask, context_midi=True)
    expected_rows = app.prepare_midi(expected, expected, mask, context_midi=True)
    assert all(app.torch.equal(actual_rows[key], expected_rows[key]) for key in expected_rows)
    assert actual_rows["kind_id"][0, 0, 0].item() == 2
    assert app.changed_region(source, target, 1) is None
    for start, onset in ((.25, 12000), (0, -12000)):
        target[0]["start"] = start
        validated = app.validate_score(json.dumps({"clip": "current", "notes": target}), session)
        assert app.note_array(validated)[0]["onset"] == onset
        assert validated[0]["source_onset"] == -.25
    retriggered = [dict(target[0])]
    retriggered[0].pop("source_onset")
    assert app.changed_region(source, retriggered, 1) == (0, .28)


@pytest.mark.parametrize("onset", [True, float("nan"), .1, -3601])
def test_invalid_source_onset_is_rejected(onset):
    session = {"clip": "current", "duration": 1}
    with pytest.raises(ValueError, match="Source note start"):
        app.validate_score(json.dumps({"clip": "current", "notes": [note(source_onset=onset)]}), session)


def test_zero_length_drum_at_crop_start_survives_and_tiny_end_fragment_is_skipped(tmp_path):
    path = tmp_path / "score.mid"
    midi = app.MidiFile(ticks_per_beat=1000)
    midi.tracks.append(app.MidiTrack([
        app.MetaMessage("set_tempo", tempo=1_000_000),
        app.Message("note_on", channel=9, note=38, velocity=90, time=500),
        app.Message("note_off", channel=9, note=38, time=0),
    ]))
    midi.save(path)
    cropped = app.midi_notes(path, .5, 1)
    assert len(cropped) == 1 and cropped[0]["start"] == 0
    assert cropped[0]["duration"] == .04
    path = app.write_midi([dict(note(), start=.999)], path)
    assert app.midi_notes(path, 0, .9995) == []


def test_editor_is_validated_and_source_is_independent():
    source = [note()]
    session = {"clip": "current", "duration": 1, "source_notes": source}
    data = {"clip": "current", "notes": source}
    result = app.validate_score(json.dumps(data), session)
    result[0]["pitch"] = 72
    assert source[0]["pitch"] == 60
    data["clip"] = "old"
    with pytest.raises(ValueError, match="different clip"):
        app.validate_score(json.dumps(data), session)
    data["clip"] = "current"
    data["notes"][0]["duration"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        app.validate_score(json.dumps(data), session)


def test_transcription_extracts_only_midi(tmp_path):
    payload = Path(app.write_midi([note()], tmp_path / "transcribed.mid")).read_bytes()
    html = '<script>untrusted()</script><a href="data:audio/midi;base64,' + base64.b64encode(payload).decode() + '">MIDI</a>'
    assert app.midi_from_html(html) == payload
    with pytest.raises(ValueError, match="no readable MIDI"):
        app.midi_from_html('<a href="https://example.com/file.mid">not inline MIDI</a>')


def test_crop_and_export_keep_clip_relative_alignment(tmp_path):
    import soundfile as sf
    rate = 48000
    samples = np.sin(np.arange(rate * 2) * 2 * np.pi * 220 / rate).astype(np.float32) * .1
    path = tmp_path / "audio.wav"
    sf.write(path, samples, rate, subtype="FLOAT")
    loaded = app.load_clip(str(path), .5, 1, None)
    session = loaded[0]
    try:
        assert np.array_equal(session["crop"].original, samples[rate // 2:rate * 3 // 2])
        midi = app.write_midi([note()], tmp_path / "full.mid")
        imported = app.load_midi(midi, session)
        notes = json.loads(imported[1])["notes"]
        assert notes[0]["start"] == 0
        assert notes[0]["duration"] == .25
        assert app.note_array(notes)[0]["offset"] == 12000
    finally:
        app.cleanup(session)


@pytest.mark.parametrize("length,crop_start", [(.1, 0), (1, .9)])
def test_too_short_audio_crop_preserves_current_work(tmp_path, length, crop_start):
    path = tmp_path / "short.wav"
    app.sf.write(path, np.zeros(round(app.SAMPLE_RATE * length), dtype=np.float32), app.SAMPLE_RATE)
    directory = tmp_path / "current"
    directory.mkdir()
    with pytest.raises(app.gr.Error, match="at least 0.2"):
        app.load_clip(str(path), crop_start, 1, {"directory": str(directory)})
    assert directory.is_dir()


def test_minimum_clip_duration_is_measured_in_audio_samples(tmp_path):
    path = tmp_path / "minimum.wav"
    app.sf.write(path, np.zeros(round(app.SAMPLE_RATE * .3), dtype=np.float32), app.SAMPLE_RATE)
    session = app.load_clip(str(path), .1, .2, None)[0]
    try:
        assert session["duration"] == .2
    finally:
        app.cleanup(session)


def test_failed_clip_and_sample_preparation_preserve_current_work(tmp_path, monkeypatch):
    assets = tmp_path / "demo" / "assets"
    assets.mkdir(parents=True)
    app.sf.write(assets / "sample.wav", np.zeros(app.SAMPLE_RATE, dtype=np.float32), app.SAMPLE_RATE)
    (assets / "sample.mid").write_bytes(b"invalid MIDI")
    monkeypatch.setattr(app, "HERE", tmp_path / "app")
    monkeypatch.setattr(app, "EXAMPLE_FILES", {"Slakh": ("sample.wav", "sample.mid")})
    directory = tmp_path / "current"
    directory.mkdir()
    session = {"directory": str(directory)}
    created = []
    mkdtemp = app.tempfile.mkdtemp
    def track_directory(*args, **kwargs):
        result = mkdtemp(*args, **kwargs)
        created.append(Path(result))
        return result
    monkeypatch.setattr(app.tempfile, "mkdtemp", track_directory)
    with pytest.raises((EOFError, OSError)):
        app.load_example(session)
    assert directory.is_dir()
    assert created and all(not path.exists() for path in created)
    def failed_preview(*args):
        raise ValueError("preview failed")
    monkeypatch.setattr(app, "editor_value", failed_preview)
    with pytest.raises(ValueError, match="preview failed"):
        app.load_clip(str(assets / "sample.wav"), 0, 1, session)
    assert directory.is_dir()
    assert all(not path.exists() for path in created)


def test_web_app_registers_workflow_endpoints():
    demo = app.build_app()
    names = {fn.api_name for fn in demo.fns.values()}
    assert {"example", "load_clip", "load_midi", "transcribe", "export_midi", "generate"} <= names


def test_startup_listening_does_not_download_or_replace_an_open_work(monkeypatch):
    def unexpected_sample(*args, **kwargs):
        pytest.fail("The listening screen must not prepare an editing session")
    monkeypatch.setattr(app, "load_example", unexpected_sample)
    monkeypatch.setattr(app, "urlopen", unexpected_sample)
    for session in (None, {"clip": "current"}):
        assert app.open_shared_work("", session) == tuple(app.gr.skip() for _ in range(22))
    preview = app.instant_demo_html()
    assert "early-kraisler-track01-original.mp3" in preview
    assert "early-kraisler-track01-base-sq-steps16.mp3" in preview
    assert preview.count("<audio ") == 2
    shared = ("saved work",)
    monkeypatch.setattr(app, "load_work", lambda work_id, old: shared if work_id == "saved-jazz" else None)
    assert app.open_shared_work("saved-jazz", None) is shared


def test_prepared_violin_edit_keeps_original_midi_and_matches_the_listening_region():
    loaded = app.load_violin_edit(None)
    session = loaded[0]
    try:
        assert len(loaded) == 19
        assert loaded[3:5] == (6.4, 14.08)
        assert loaded[13:] == (False, "ordinary", 16, 2., False, False)
        assert loaded[5] is None and "take" not in session
        score = json.loads(loaded[2])
        selected = [score["notes"][i] for i in score["view"]["selected"]]
        assert score["view"]["program"] == 40
        assert [n["pitch"] for n in selected] == [68, 67, 66, 65, 64, 63]
        source = app.midi_notes(loaded[6], 0, session["duration"])
        np.testing.assert_array_equal(app.note_array(source), app.note_array(session["source_notes"]))
        assert [session["source_notes"][n["id"]]["pitch"] for n in selected] == [68, 77, 75, 72, 70, 68]
        assert app.midi_notes(loaded[7], 0, session["duration"]) == score["notes"]
        for before, after in zip(session["source_notes"], score["notes"]):
            if before != after:
                assert before["program"] == after["program"] == 40
                assert {k: v for k, v in before.items() if k != "pitch"} == {k: v for k, v in after.items() if k != "pitch"}
        assert "Descending violin" in session["suggested_title"]
    finally:
        app.cleanup(session)


def profile(username):
    return app.gr.OAuthProfile(dict(name=username, preferred_username=username,
                                   profile=f"https://huggingface.co/{username}", picture=""))


def test_gallery_rejects_anonymous_and_other_publishers(monkeypatch):
    def unexpected_write(*args, **kwargs):
        pytest.fail("An unauthorized user must not reach storage")
    monkeypatch.setattr(app, "HfApi", unexpected_write)
    for visitor, message in ((None, "Sign in to this Space"), (profile("visitor"), "limited to mimbres")):
        with pytest.raises(app.gr.Error, match=message):
            app.save_work("My edit", "", True, {}, visitor)


def test_gallery_keeps_valid_works_when_one_project_is_unavailable(monkeypatch):
    class Storage:
        def __init__(self, **kwargs):
            pass
        def list_repo_tree(self, *args, **kwargs):
            return [app.RepoFolder(path=name, oid="") for name in ("good-work", "broken-work")]
    def project(work_id):
        if work_id == "broken-work":
            raise OSError("unavailable")
        return dict(listed=True, title="A piano idea", settings={"method": "ordinary"}, duration=1, notes=[note()])
    monkeypatch.setattr(app, "HfApi", Storage)
    monkeypatch.setattr(app, "read_project", project)
    html = app.gallery_html()
    assert "A piano idea" in html and "?work=good-work" in html
    assert "Some works could not be loaded" in html
    assert "temporarily unavailable" not in html


def test_blank_title_uses_clip_name_and_updates_the_visible_title(monkeypatch):
    saved_titles = []
    def saved_work(title, *args):
        saved_titles.append(title)
        return "saved-link", "gallery"
    monkeypatch.setattr(app, "save_work", saved_work)
    session = {"take": {"directory": "still-here"}, "suggested_title": "Jazz intro · edit"}
    link, gallery, message, title = app.publish_work("  ", "", True, session, profile("mimbres"))
    assert (link, gallery, title) == ("saved-link", "gallery", "Jazz intro · edit")
    assert saved_titles == [title] and message.startswith("**Saved.**")
    assert session["take"] == {"directory": "still-here"}


def test_clip_title_suggestions_preserve_user_titles():
    session = {"suggested_title": "Slakh · edit"}
    automatic = "Kraisler · edit"
    assert app.suggest_work_title(automatic, session, automatic) == ("Slakh · edit", "Slakh · edit")
    # Typing and immediately loading another clip needs no separate input callback.
    assert app.suggest_work_title("My clarinet version", session, automatic) == ("My clarinet version", automatic)
    assert app.suggest_work_title("  ", session, automatic) == ("Slakh · edit", "Slakh · edit")
    title, reference = app.initial_work_title()
    assert title == reference and title.startswith("Music edit · ")
    assert app.resolved_work_title("", None).startswith("Music edit · ")


def test_save_feedback_survives_preparation_errors(monkeypatch):
    def failed_save(*args):
        raise OSError("private server detail")
    monkeypatch.setattr(app, "save_work", failed_save)
    link, gallery, message, title = app.publish_work("My edit", "", True, {}, profile("mimbres"))
    assert link == gallery == app.gr.skip()
    assert title == "My edit"
    assert "files could not be prepared" in message
    assert "private server detail" not in message


def test_publishing_status_reflects_current_account():
    message, button = app.publishing_status(None)
    assert button["visible"] and "Sign in to this Space" in message["value"]
    message, button = app.publishing_status(profile("mimbres"))
    assert not button["visible"] and "Signed in as **mimbres**" in message["value"]
    message, button = app.publishing_status(profile("visitor"))
    assert not button["visible"] and "limited to **mimbres**" in message["value"]


def test_open_editing_session_survives_an_hour_but_closed_sessions_expire():
    from datetime import datetime, timedelta
    from gradio.state_holder import SessionState
    demo = app.build_app()
    state = next(block for block in demo.blocks.values() if isinstance(block, app.gr.State))
    session = SessionState(demo)
    session[state._id] = {"take": {}}
    ttl, _ = session._state_ttl[state._id]
    session._state_ttl[state._id] = (ttl, datetime.now() - timedelta(hours=2))
    session[state._id]["take"]["notes"] = [note()]
    assert not list(session.state_components)[0][2]
    session.is_closed = True
    assert list(session.state_components)[0][2]


@pytest.mark.parametrize("title,expected_title", [("Piano edit", "Piano edit"), ("  ", "audio · edit")])
def test_saved_work_restores_the_generated_take_and_can_be_edited(tmp_path, monkeypatch, title, expected_title):
    import soundfile as sf
    rate = app.SAMPLE_RATE
    audio = np.sin(np.arange(rate * 3) * 2 * np.pi * 220 / rate).astype(np.float32) * 1.2
    path = tmp_path / "audio.wav"
    sf.write(path, audio, rate, subtype="FLOAT")
    session = app.load_clip(str(path), .5, 1, None)[0]
    source = [dict(note(), start=0, duration=.25, source_onset=-.25)]
    session = app.install_source_notes(session, source, "Loaded")[0]
    target = [dict(source[0], pitch=72)]
    value = json.loads(app.editor_value(session, target))
    value["view"] = dict(program=0, zoom=2, snap=.1, scrollTop=500, colors=[[0, "#79a8e8"]])
    expected = session["crop"].original.copy()
    expected[rate // 4:rate // 2] *= -1
    monkeypatch.setattr(app, "generate_audio", lambda *args: expected.copy())
    uploads = {}
    class Storage:
        def __init__(self, **kwargs):
            pass
        def create_commit(self, **kwargs):
            for operation in kwargs["operations"]:
                uploads[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
    monkeypatch.setattr(app, "HfApi", Storage)
    monkeypatch.setattr(app, "gallery_html", lambda: "gallery")
    monkeypatch.setenv("SPANSYNTH_GALLERY_TOKEN", "test-credential")
    monkeypatch.setattr(app, "urlopen", lambda url, **kwargs: io.BytesIO(uploads[url.split("/resolve/main/")[1]]))
    restored = None
    try:
        app.generate(json.dumps(value), session, .25, .5, "flowedit", 16, 2., True, False)
        # A later Apply edits changes the download, but must not change the saved take.
        value["notes"][0]["pitch"] = 84
        app.export_midi(json.dumps(value), session)
        link, _ = app.save_work(title, "An octave up", True, session, profile("mimbres"))
        work_id = link.split("?work=")[1]
        saved = json.loads(uploads[f"{work_id}/project.json"])
        assert saved["title"] == expected_title
        assert saved["notes"][0]["pitch"] == 72
        assert saved["source_notes"][0]["pitch"] == 60
        assert saved["notes"][0]["source_onset"] == saved["source_notes"][0]["source_onset"] == -.25
        assert len(uploads) == 6
        app.cleanup(session)
        loaded = app.load_work(work_id, None)
        restored = loaded[0]
        assert loaded[14] == restored["suggested_title"] == expected_title
        assert np.array_equal(restored["crop"].original, audio[rate // 2:rate * 3 // 2])
        assert np.array_equal(restored["crop"].waveform.numpy(), session["crop"].waveform.numpy())
        assert restored["crop"].gain == session["crop"].gain < 1
        assert np.array_equal(sf.read(loaded[5], dtype="float32")[0], expected)
        assert loaded[3:5] == (.25, .5)
        assert loaded[9] == "flowedit"
        assert loaded[10]["value"] == 16
        assert loaded[11:14] == (2., True, False)
        assert loaded[18] is False
        assert loaded[19:] == (loaded[1], 0, 1.)
        assert json.loads(loaded[2])["view"]["colors"] == [[0, "#79a8e8"]]
        assert parse_midi_notes(loaded[7], program=None).notes[0].pitch == 72
        assert restored["source_notes"][0]["pitch"] == 60
        assert restored["source_notes"][0]["source_onset"] == -.25
        assert restored["take"]["notes"][0]["source_onset"] == -.25
        assert app.note_array(restored["take"]["notes"])[0]["onset"] == -12000
        # The upload shown by a reopened work is the cropped clip. Its MIDI starts at zero.
        imported = app.load_midi(loaded[7], restored)
        imported_note = json.loads(imported[1])["notes"][0]
        assert imported_note["start"] == 0 and imported_note["duration"] == .25
        # A late failure while opening another work must not delete the active one.
        broken = dict(saved)
        broken.pop("title")
        uploads[f"{work_id}/project.json"] = json.dumps(broken).encode()
        with pytest.raises(app.gr.Error, match="could not be opened"):
            app.load_work(work_id, restored)
        assert Path(restored["directory"]).is_dir()
        assert Path(loaded[5]).is_file()
        # Imported copies cannot modify the shared work or its saved MIDI by exporting.
        editing = json.loads(loaded[2])
        editing["notes"][0]["pitch"] = 84
        app.export_midi(json.dumps(editing), restored)
        assert parse_midi_notes(loaded[7], program=None).notes[0].pitch == 72
        assert "work_id" not in restored["take"]
        app.generate(json.dumps(editing), restored, .25, .5, "ordinary", 16, 2., False, False)
        assert restored["take"]["notes"][0]["pitch"] == 84
        assert app.install_source_notes(restored, source, "Reloaded")[0].get("take") is None
    finally:
        app.cleanup(session)
        app.cleanup(restored)


def test_invalid_shared_link_keeps_current_session(tmp_path, monkeypatch):
    directory = tmp_path / "current"
    directory.mkdir()
    session = {"directory": str(directory)}
    def no_download(*args, **kwargs):
        pytest.fail("An invalid link must not make a network request")
    monkeypatch.setattr(app, "urlopen", no_download)
    with pytest.raises(app.gr.Error, match="could not be opened"):
        app.load_work("../private", session)
    assert directory.is_dir()


def test_editor_view_drops_invalid_colors_and_values():
    view = app.editor_view(dict(colors=[[0, "red;bad"], [1], None, [2, "#79a8e8"]],
                                zoom=float("nan"), snap=.2, extraTracks="bad"))
    assert view == {"colors": [[2, "#79a8e8"]], "extraTracks": []}


@pytest.mark.parametrize("source,target,expected", [
    ([note()], [dict(note(), pitch=72)], (.24, .76)),
    ([note()], [], (.24, .76)),
    ([note()], [dict(note(), start=2.)], (.24, 2.52)),
    ([dict(note(), duration=2.)], [note()], (.24, 2.28)),
    ([], [note()], (.24, .76)),
    ([note()], [dict(note(), velocity=64)], (.24, .76)),
    ([note()], [note(40)], (.24, .76)),
    ([note(), note()], [note()], (.24, .76)),
    ([note(), dict(note(), start=12.)], [note(), dict(note(), start=13.)], (12., 13.52)),
    ([], [dict(note(), start=20.47, duration=.01)], (20.44, 20.48)),
    ([note(), note(40)], [dict(note(40), id=99), dict(note(), id=1)], None),
])
def test_changed_region_covers_only_actual_note_edits(source, target, expected):
    assert app.changed_region(source, target, 20.48) == expected


def test_auto_region_allows_manual_selection_and_handles_undo():
    session = {"clip": "current", "duration": 20.48, "source_notes": [note()]}
    changed = json.dumps({"clip": "current", "notes": [dict(note(), pitch=72)]})
    first, last, _ = app.update_region(changed, session, True)
    assert first["value"] == .24 and last["value"] == .76
    assert not first["interactive"] and not last["interactive"]
    for value, enabled in ((changed, False), (json.dumps({"clip": "current", "notes": [note()]}), True)):
        first, last, _ = app.update_region(value, session, enabled)
        assert first["interactive"] and last["interactive"]
        assert "value" not in first and "value" not in last


def test_generation_recalculates_region_before_using_stale_controls(tmp_path, monkeypatch):
    import soundfile as sf
    path = tmp_path / "audio.wav"
    sf.write(path, np.zeros(app.SAMPLE_RATE * 3, dtype=np.float32), app.SAMPLE_RATE)
    session = app.load_clip(str(path), 0, 3, None)[0]
    session["source_notes"] = [note()]
    value = app.editor_value(session, [dict(note(), start=2.1)])
    calls = []
    def generate_audio(crop, target, source, start, end, *args):
        calls.append((start, end))
        return crop.original.copy()
    monkeypatch.setattr(app, "generate_audio", generate_audio)
    try:
        for auto, expected in ((True, (.24, 2.6)), (False, (1., 1.5))):
            app.generate(value, session, 1., 1.5, "ordinary", 16, 2., False, False, auto)
            assert calls[-1] == expected
    finally:
        app.cleanup(session)


def test_failed_generation_keeps_last_take_and_removes_partial_files(tmp_path, monkeypatch):
    path = tmp_path / "audio.wav"
    app.sf.write(path, np.zeros(app.SAMPLE_RATE, dtype=np.float32), app.SAMPLE_RATE)
    session = app.load_clip(str(path), 0, 1, None)[0]
    monkeypatch.setattr(app, "generate_audio", lambda crop, *args: crop.original.copy())
    try:
        value = app.editor_value(session, [note()])
        output = app.generate(value, session, 0, 1, "ordinary", 16, 2., False, False)
        previous = session["take"]
        def failed_export(*args):
            raise OSError("MIDI export failed")
        monkeypatch.setattr(app, "write_midi", failed_export)
        with pytest.raises(app.gr.Error, match="MIDI export failed"):
            app.generate(value, session, 0, 1, "ordinary", 16, 2., False, False)
        assert session["take"] is previous and Path(output[0]).is_file()
        assert list(Path(session["directory"]).glob("take-*")) == [Path(previous["directory"])]
    finally:
        app.cleanup(session)


def test_added_organ_reaches_generation_and_midi_export(tmp_path, monkeypatch):
    import soundfile as sf
    import torch
    from spansynth.vocabulary import PROGRAM_TO_CATEGORY
    path = tmp_path / "input.wav"
    sf.write(path, np.zeros(app.SAMPLE_RATE * 3, dtype=np.float32), app.SAMPLE_RATE)
    session = app.load_clip(str(path), 0, 3, None)[0]
    source = [note()]
    session["source_notes"] = source
    target = source + [dict(note(16), start=1.2, duration=.8, pitch=72)]
    observed = []
    monkeypatch.setattr(app, "MODEL", object())
    monkeypatch.setattr(app, "CODEC", object())
    monkeypatch.setattr(app, "DEVICE", torch.device("cpu"))
    monkeypatch.setattr(app, "encode_audio", lambda *args: torch.zeros(1, 562, 128))
    def sample(model, codes, rows, mask, **kwargs):
        valid = rows["event_valid"] & (rows["category_id"] == PROGRAM_TO_CATEGORY[16])
        assert valid.any()
        observed.append(valid.nonzero()[:, 1].unique().tolist())
        assert kwargs["steps"] == 16 and kwargs["cfg"] == 2.
        assert not kwargs["context_midi"] and not kwargs["drop_context_audio"]
        return codes
    monkeypatch.setattr(app, "sample", sample)
    monkeypatch.setattr(app, "decode_audio", lambda *args: np.zeros(app.PAYLOAD_SAMPLES, dtype=np.float32))
    try:
        result = app.generate(app.editor_value(session, target), session, 0, 3,
                              "ordinary", 16, 2., False, False, True)
        exported = parse_midi_notes(result[1], program=None).notes
        assert {(n.program, n.pitch) for n in exported} == {(0, 60), (16, 72)}
        assert min(observed[0]) >= 30 and max(observed[0]) < 50
        # A second edit reaches the same production path and leaves the source independent.
        target[-1]["pitch"] = 74
        second = app.generate(app.editor_value(session, target), session, 0, 3,
                              "ordinary", 16, 2., False, False, True)
        assert second[0] != result[0]
        assert parse_midi_notes(second[1], program=None).notes[-1].pitch == 74
        assert source == [note()]
    finally:
        app.cleanup(session)
