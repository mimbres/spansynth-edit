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
from spansynth.midi import parse_midi_notes


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
    assert len(cropped) == 3
    assert all(n["start"] == 0 and n["duration"] == pytest.approx(.1) for n in cropped)


def test_export_rejects_channel_collision(tmp_path):
    with pytest.raises(ValueError, match="15 melodic"):
        app.write_midi([note(program) for program in range(16)], tmp_path / "score.mid")


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


def test_web_app_registers_workflow_endpoints():
    demo = app.build_app()
    names = {fn.api_name for fn in demo.fns.values()}
    assert {"example", "load_clip", "load_midi", "transcribe", "export_midi", "generate"} <= names


def profile(username):
    return app.gr.OAuthProfile(dict(name=username, preferred_username=username,
                                   profile=f"https://huggingface.co/{username}", picture=""))


def test_gallery_rejects_anonymous_and_other_publishers(monkeypatch):
    def unexpected_write(*args, **kwargs):
        pytest.fail("An unauthorized user must not reach storage")
    monkeypatch.setattr(app, "HfApi", unexpected_write)
    for visitor in (None, profile("visitor")):
        with pytest.raises(app.gr.Error, match="limited to mimbres"):
            app.save_work("My edit", "", True, {}, visitor)


def test_saved_work_restores_the_generated_take_and_can_be_edited(tmp_path, monkeypatch):
    import soundfile as sf
    rate = app.SAMPLE_RATE
    audio = np.sin(np.arange(rate * 3) * 2 * np.pi * 220 / rate).astype(np.float32) * 1.2
    path = tmp_path / "audio.wav"
    sf.write(path, audio, rate, subtype="FLOAT")
    session = app.load_clip(str(path), .5, 1, None)[0]
    source = [note()]
    session = app.install_source_notes(session, source, "Loaded")[0]
    target = [dict(note(), pitch=72)]
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
        link, _ = app.save_work("Piano edit", "An octave up", True, session, profile("mimbres"))
        work_id = link.split("?work=")[1]
        saved = json.loads(uploads[f"{work_id}/project.json"])
        assert saved["notes"][0]["pitch"] == 72
        assert saved["source_notes"][0]["pitch"] == 60
        assert len(uploads) == 6
        app.cleanup(session)
        loaded = app.load_work(work_id, None)
        restored = loaded[0]
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
