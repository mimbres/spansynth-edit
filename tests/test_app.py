"""Web-only tests: MIDI timing, editable data, and the transcription boundary."""
import base64
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
