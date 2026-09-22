"""Interactive MIDI-guided audio editing, shared by local Gradio and ZeroGPU."""
from __future__ import annotations

import base64
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.request import urlopen

# The Space runs this script directly; share the repository's inference package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gradio as gr
from gradio_client import Client, handle_file
from mido import Message, MetaMessage, MidiFile, MidiTrack
import numpy as np
import soundfile as sf
import spaces
import torch

from spansynth.audio import read_audio, encode_audio, decode_audio, assemble_output, SAMPLE_RATE, HISTORY_FRAMES
from spansynth.checkpoint import resolve_assets, load_model
from spansynth.cli import resolve_device
from spansynth.codec import load_scalar_model
from spansynth.midi import NOTE_DTYPE, parse_midi_notes, prepare_midi
from spansynth.sampling import sample
from spansynth.vocabulary import PROGRAM_GROUPS, PROGRAM_TO_CATEGORY

HERE = Path(__file__).resolve().parent
MODEL = CODEC = DEVICE = None
MAX_NOTES = 10000
INSTRUMENTS = [{"program": programs[0], "name": name, "members": list(programs)}
               for name, programs in PROGRAM_GROUPS.items()]
EXAMPLE_ROOT = "https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/"


def cleanup(session):
    if isinstance(session, dict) and session.get("directory"):
        shutil.rmtree(session["directory"], ignore_errors=True)


def finite_number(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if not lower <= value <= upper:
        raise ValueError(f"{name} must be between {lower:g} and {upper:g}.")
    return value


def validate_score(value, session):
    if not session:
        raise ValueError("Load an audio clip first.")
    if not isinstance(value, str) or len(value) > 4_000_000:
        raise ValueError("Invalid editor data.")
    data = json.loads(value)
    if not isinstance(data, dict) or data.get("clip") != session["clip"]:
        raise ValueError("The editor belongs to a different clip. Load the clip again.")
    notes = data.get("notes")
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ValueError(f"A clip can contain at most {MAX_NOTES} notes.")
    result = []
    duration = session["duration"]
    for note in notes:
        if not isinstance(note, dict):
            raise ValueError("Invalid note.")
        onset = finite_number(note.get("start"), "Note start", 0, duration)
        length = finite_number(note.get("duration"), "Note length", 0.001, duration)
        if onset + length > duration + 1e-6:
            raise ValueError("A note extends beyond the clip.")
        fields = {}
        for name, lower, upper in (("pitch", 0, 127), ("velocity", 1, 127), ("program", 0, 128)):
            number = note.get(name)
            if isinstance(number, bool) or not isinstance(number, int) or not lower <= number <= upper:
                raise ValueError(f"Invalid note {name}.")
            fields[name] = number
        if fields["program"] not in PROGRAM_TO_CATEGORY:
            raise ValueError(f"Program {fields['program']} is not supported. Select that track and choose an instrument.")
        result.append({"start": float(onset), "duration": float(length), **fields})
    if len({note["program"] for note in result if note["program"] != 128}) > 15:
        raise ValueError("Use at most 15 melodic instruments plus drums for MIDI export. Merge a few tracks first.")
    return result


def midi_notes(path, crop_start, duration):
    parsed = parse_midi_notes(path, program=None)
    result = []
    for note in parsed.notes:
        start = max(0.0, note.onset - crop_start)
        end = min(duration, note.offset - crop_start)
        if note.onset >= crop_start + duration or note.offset <= crop_start:
            continue
        if end - start < 0.001:
            end = min(duration, start + 0.04)
        if end <= start:
            continue
        result.append({"id": len(result), "start": start, "duration": end - start,
                       "pitch": note.pitch, "velocity": note.velocity, "program": note.program})
    if len(result) > MAX_NOTES:
        raise ValueError(f"The MIDI clip exceeds {MAX_NOTES} notes.")
    return result


def write_midi(notes, path):
    # At this tempo and tick resolution, one tick is one millisecond.
    midi = MidiFile(type=1, ticks_per_beat=1000)
    midi.tracks.append(MidiTrack([MetaMessage("set_tempo", tempo=1_000_000)]))
    programs = sorted({note["program"] for note in notes})
    if len([program for program in programs if program != 128]) > 15:
        raise ValueError("MIDI export supports up to 15 melodic instruments plus drums. Merge a few instrument tracks first.")
    channels = [channel for channel in range(16) if channel != 9]
    for index, program in enumerate(programs):
        channel = 9 if program == 128 else channels[index]
        track = MidiTrack([MetaMessage("track_name", name=instrument_name(program))])
        if program != 128:
            track.append(Message("program_change", program=program, channel=channel))
        events = []
        for note in notes:
            if note["program"] != program:
                continue
            start = round(note["start"] * 1000)
            end = max(start + 1, round((note["start"] + note["duration"]) * 1000))
            events.extend([(start, 1, note), (end, 0, note)])
        previous = 0
        for tick, is_on, note in sorted(events, key=lambda event: (event[0], event[1])):
            track.append(Message("note_on" if is_on else "note_off", note=note["pitch"],
                                 velocity=note["velocity"] if is_on else 0, channel=channel, time=tick - previous))
            previous = tick
        midi.tracks.append(track)
    midi.save(path)
    return str(path)


def instrument_name(program):
    return next((entry["name"] for entry in INSTRUMENTS if program in entry["members"]), f"Unsupported program {program}")


def note_array(notes):
    return np.array([(round(n["start"] * SAMPLE_RATE), round((n["start"] + n["duration"]) * SAMPLE_RATE),
                      n["pitch"], n["velocity"], n["program"], i) for i, n in enumerate(notes)], dtype=NOTE_DTYPE)


def editor_value(session, notes):
    audio = session["crop"].original
    bins = np.array_split(audio, min(1024, len(audio)))
    wave = [[round(float(part.min()), 4), round(float(part.max()), 4)] for part in bins]
    return json.dumps({"clip": session["clip"], "duration": session["duration"],
                       "notes": notes, "instruments": INSTRUMENTS, "waveform": wave})


def load_clip(audio, crop_start, duration, old_session):
    if not audio:
        raise gr.Error("Upload audio or choose the example first.")
    finite_number(crop_start, "Crop start", 0, 3600)
    finite_number(duration, "Clip duration", 0.2, 20.48)
    info = sf.info(audio)
    if info.duration > 600:
        raise gr.Error("Please upload at most 10 minutes of audio, then select a short clip.")
    if crop_start >= info.duration:
        raise gr.Error("The crop starts after the recording ends.")
    duration = min(duration, info.duration - crop_start)
    crop = read_audio(Path(audio), crop_start, duration)
    directory = Path(tempfile.mkdtemp(prefix="spansynth-session-"))
    session = {"directory": str(directory), "clip": directory.name, "crop": crop,
               "duration": len(crop.original) / SAMPLE_RATE, "crop_start": crop_start,
               "source_notes": None}
    preview = directory / "input.wav"
    sf.write(preview, crop.original, SAMPLE_RATE, subtype="FLOAT")
    cleanup(old_session)
    start, end = (6.4, 14.08) if duration >= 14.08 else (duration * 0.25, duration * 0.75)
    return session, str(preview), editor_value(session, []), start, end, None, None, None, "Clip ready. Transcribe it, upload MIDI, or add notes."


def load_midi(path, session):
    if not session:
        raise gr.Error("Load an audio clip first.")
    if not path:
        raise gr.Error("Choose a MIDI file aligned with the full uploaded recording.")
    notes = midi_notes(path, session["crop_start"], session["duration"])
    return install_source_notes(session, notes, "MIDI loaded")


def install_source_notes(session, notes, message):
    session = {**session, "source_notes": notes}
    original = write_midi(notes, Path(session["directory"]) / "original.mid")
    unsupported = sorted({n["program"] for n in notes} - PROGRAM_TO_CATEGORY.keys())
    suffix = f" Remap unsupported programs in the editor: {unsupported}." if unsupported else ""
    return session, editor_value(session, notes), original, None, None, f"{message}: {len(notes)} notes. Edit a track, then generate.{suffix}"


def midi_from_html(value):
    if not isinstance(value, str):
        raise ValueError("YourMT3 did not return a transcription.")
    match = re.search(r"data:audio/(?:midi|mid);base64,([A-Za-z0-9+/=]+)", value)
    if not match or len(match[1]) > 8_000_000:
        raise ValueError("YourMT3 returned no readable MIDI file. Please retry or upload MIDI.")
    payload = base64.b64decode(match[1], validate=True)
    MidiFile(file=io.BytesIO(payload))
    return payload


def transcribe(session, request: gr.Request, progress=gr.Progress()):
    if not session:
        raise gr.Error("Load an audio clip first.")
    progress(0, desc="Waiting for YourMT3")
    headers = {}
    if request and request.headers.get("x-ip-token"):
        headers["x-ip-token"] = request.headers["x-ip-token"]
    try:
        client = Client("mimbres/YourMT3", headers=headers, verbose=False)
        result = client.predict(handle_file(str(Path(session["directory"]) / "input.wav")), api_name="/process_audio")
        path = Path(session["directory"]) / "transcription.mid"
        path.write_bytes(midi_from_html(result))
        notes = midi_notes(path, 0, session["duration"])
    except Exception as error:
        raise gr.Error(f"YourMT3 transcription could not finish. {error}") from error
    progress(1, desc="Transcription ready")
    return install_source_notes(session, notes, "YourMT3 transcription")


def export_midi(value, session):
    try:
        notes = validate_score(value, session)
        return write_midi(notes, Path(session["directory"]) / "edited.mid")
    except (ValueError, OSError) as error:
        raise gr.Error(str(error)) from error


@spaces.GPU(duration=40)
def generate_audio(crop, target, source, start, end, method, steps, cfg, context_midi, drop_context_audio):
    if MODEL is None:
        raise gr.Error("Model loading is disabled in this UI preview.")
    mask = torch.zeros(512, dtype=torch.bool)
    first, last = math.floor(start * 25 + 1e-9), math.ceil(end * 25 - 1e-9)
    mask[first:last] = True
    target_rows = prepare_midi(note_array(target), note_array(source), mask, context_midi=context_midi)
    source_rows = prepare_midi(note_array(source), note_array(source), mask, context_midi=context_midi) if method == "flowedit" else None
    encoded = encode_audio(CODEC, crop, DEVICE)
    history, codes = encoded[:, :HISTORY_FRAMES], encoded[:, HISTORY_FRAMES:]
    target_rows = {key: value.to(DEVICE) for key, value in target_rows.items()}
    if source_rows is not None:
        source_rows = {key: value.to(DEVICE) for key, value in source_rows.items()}
    with torch.inference_mode(), torch.autocast(device_type=DEVICE.type, dtype=torch.bfloat16, enabled=DEVICE.type == "cuda"):
        output_codes = sample(MODEL, codes, target_rows, (~mask)[None].to(DEVICE), source_rows=source_rows,
                              method=method, steps=steps, cfg=cfg, context_midi=context_midi,
                              drop_context_audio=drop_context_audio)
    decoded = decode_audio(CODEC, output_codes, history, DEVICE)
    return assemble_output(crop.original, decoded, crop.gain, first * 1920, min(last * 1920, len(crop.original)))


def generate(value, session, start, end, method, steps, cfg, context_midi, drop_context_audio):
    try:
        notes = validate_score(value, session)
        finite_number(start, "Region start", 0, session["duration"])
        finite_number(end, "Region end", 0, session["duration"])
        if end <= start:
            raise ValueError("The region must end after it starts.")
        finite_number(cfg, "Guidance", 0, 8)
        finite_number(steps, "Steps", 1, 64)
        if int(steps) != steps:
            raise ValueError("Steps must be an integer.")
        if method not in ("ordinary", "flowedit"):
            raise ValueError("Unknown generation method.")
        source = session["source_notes"]
        if (method == "flowedit" or context_midi) and source is None:
            raise ValueError("Transcribe the clip or load original MIDI first.")
        began = time.perf_counter()
        result = generate_audio(session["crop"], notes, source or [], start, end, method, int(steps), cfg,
                                context_midi, drop_context_audio)
        directory = Path(session["directory"])
        # Each generated take has its own path, so replaying an earlier result remains reliable.
        take = Path(tempfile.mkdtemp(prefix="take-", dir=directory))
        sf.write(take / "output.wav", result, SAMPLE_RATE, subtype="FLOAT")
        target_path = write_midi(notes, take / "edited.mid")
        first, last = math.floor(start * 25 + 1e-9) / 25, min(math.ceil(end * 25 - 1e-9) / 25, session["duration"])
        return str(take / "output.wav"), target_path, f"Generated {first:.2f}–{last:.2f} s in {time.perf_counter() - began:.1f} s. Audio outside this region is unchanged."
    except (ValueError, RuntimeError, OSError) as error:
        raise gr.Error(str(error)) from error


def load_example(old_session):
    with tempfile.TemporaryDirectory(prefix="spansynth-example-") as folder:
        folder = Path(folder)
        for suffix in ("original.mp3", "before.mid"):
            filename = "early-slakh-track00006-" + suffix
            with urlopen(EXAMPLE_ROOT + filename, timeout=30) as response:
                (folder / filename).write_bytes(response.read())
        loaded = load_clip(str(folder / "early-slakh-track00006-original.mp3"), 0, 20.48, old_session)
        session, preview, _, start, end, *_ = loaded
        notes = midi_notes(folder / "early-slakh-track00006-before.mid", 0, session["duration"])
        session, editor, midi, _, _, status = install_source_notes(session, notes, "Prepared example")
        return session, preview, editor, start, end, None, midi, None, status


EDITOR_HTML = """
<div class="roll-shell">
  <div class="roll-toolbar">
    <label>Track <select data-role="track" aria-label="MIDI track"></select></label>
    <label>Instrument <select data-role="instrument" aria-label="Track instrument"></select></label>
    <button data-action="add-track">+ Track</button>
    <label>Snap <select data-role="snap" aria-label="Time snap"><option value="0">Off</option><option value="0.04" selected>40 ms</option><option value="0.1">100 ms</option><option value="0.25">250 ms</option></select></label>
    <button data-action="undo" title="Undo (Ctrl/Cmd+Z)">Undo</button>
    <button data-action="redo" title="Redo (Ctrl/Cmd+Shift+Z)">Redo</button>
    <button data-action="delete">Delete note</button>
  </div>
  <div class="roll-toolbar roll-secondary">
    <button data-action="play">Play clip</button>
    <button data-action="preview">Preview notes</button>
    <button data-action="stop">Stop</button>
    <label>Zoom <input data-role="zoom" aria-label="Timeline zoom" type="range" min="1" max="4" step="0.25" value="1"></label>
    <label>Velocity <input data-role="velocity" aria-label="Selected note velocity" type="number" min="1" max="127" value="90"></label>
    <span data-role="count"></span>
  </div>
  <div class="roll-scroll" tabindex="0" aria-label="Piano roll. Double click to add a note. Drag to move; drag the right edge to resize.">
    <canvas data-role="roll" aria-label="Editable piano roll"></canvas>
  </div>
  <p class="roll-help">Double-click to add · Drag to move · Drag a note’s right edge to resize · Delete to remove · Ctrl/Cmd+Z to undo. Preview notes uses a simple synth.</p>
  <p data-role="detail" class="roll-detail">Load a clip to begin.</p>
</div>
"""


def build_app():
    with gr.Blocks(title="SpanSynth-Edit", delete_cache=(3600, 3600)) as demo:
        gr.HTML('<header class="hero"><div class="eyebrow">SPANSYNTH-EDIT · MIDI-GUIDED MUSIC EDITING</div><h1>Change the notes.<br><span>Keep the musical context.</span></h1><p>Upload a recording, edit its score, and hear a new version of the selected region.</p><div class="hero-links"><a href="https://mimbres.github.io/spansynth-edit/" target="_blank">Listen to demos ↗</a><a href="https://github.com/mimbres/spansynth-edit" target="_blank">Source code ↗</a><a href="https://huggingface.co/mimbres/spansynth-edit" target="_blank">Model weights ↗</a></div></header>')
        state = gr.State(None, time_to_live=3600, delete_callback=cleanup)
        with gr.Group(elem_classes="step-card"):
            gr.Markdown("### 1 · Choose your audio")
            with gr.Row():
                audio = gr.Audio(label="Upload a recording", sources=["upload"], type="filepath", editable=False,
                                 buttons=["download"], elem_id="upload-audio")
                with gr.Column():
                    gr.Markdown("Work on one clip of up to **20.48 seconds**. Start with the example or upload your own recording.")
                    with gr.Row():
                        crop_start = gr.Number(value=0, minimum=0, label="Crop start · seconds")
                        duration = gr.Number(value=20.48, minimum=0.2, maximum=20.48, label="Clip length · seconds")
                    with gr.Row():
                        load = gr.Button("Load clip", variant="primary")
                        example = gr.Button("Try prepared example")
            original_audio = gr.Audio(label="Original clip", interactive=False, type="filepath", buttons=["download"], elem_id="source-audio")
        with gr.Group(elem_classes="step-card"):
            gr.Markdown("### 2 · Edit the score")
            with gr.Row():
                transcribe_button = gr.Button("Transcribe with YourMT3", variant="primary")
                gr.Markdown("Transcription can make mistakes. Correct the notes before generating. The prepared example already includes MIDI.")
            with gr.Accordion("Already have aligned MIDI?", open=False):
                midi_input = gr.File(label="Original MIDI aligned with the full uploaded recording", file_types=[".mid", ".midi"])
                import_button = gr.Button("Load MIDI into editor")
            editor = gr.HTML(value="{}", html_template=EDITOR_HTML, js_on_load=(HERE / "editor.js").read_text(),
                             css_template="", apply_default_css=False, elem_id="note-editor")
            with gr.Row():
                source_download = gr.File(label="Original MIDI", interactive=False)
                target_download = gr.File(label="Edited MIDI", interactive=False)
                export_button = gr.Button("Export edited MIDI")
        with gr.Group(elem_classes="step-card"):
            gr.Markdown("### 3 · Generate the selected region")
            with gr.Row():
                edit_start = gr.Number(value=6.4, minimum=0, label="Region start · clip seconds", elem_id="edit-start")
                edit_end = gr.Number(value=14.08, minimum=0, label="Region end · clip seconds", elem_id="edit-end")
                method = gr.Dropdown(choices=[("spansynth-edit", "ordinary"), ("spansynth-edit + flowedit", "flowedit")], value="ordinary", label="Method")
            with gr.Accordion("Generation settings", open=False):
                with gr.Row():
                    steps = gr.Slider(1, 64, value=16, step=1, label="Euler steps")
                    cfg = gr.Slider(0, 8, value=2, step=0.1, label="MIDI guidance (CFG)")
                with gr.Row():
                    context_midi = gr.Checkbox(value=False, label="Use original MIDI outside the region")
                    drop_context_audio = gr.Checkbox(value=False, label="Drop audio context")
            generate_button = gr.Button("Generate audio", variant="primary", size="lg")
            status = gr.Markdown("Choose a recording or try the prepared example.", elem_id="run-status")
            output_audio = gr.Audio(label="Edited clip · 48 kHz mono", interactive=False, type="filepath", buttons=["download"], elem_id="result-audio")
        gr.Markdown("Audio outside the selected region is preserved. Region boundaries snap outward to 40 ms. Uploads and results are temporary. Transcription uses [YourMT3](https://huggingface.co/spaces/mimbres/YourMT3); generation runs here. ZeroGPU availability and usage limits depend on your Hugging Face account.", elem_classes="footer-note")
        clip_outputs = [state, original_audio, editor, edit_start, edit_end, output_audio, source_download, target_download, status]
        load.click(load_clip, [audio, crop_start, duration, state], clip_outputs, api_name="load_clip", concurrency_id="editing")
        example.click(load_example, [state], clip_outputs, api_name="example", concurrency_id="editing")
        midi_outputs = [state, editor, source_download, target_download, output_audio, status]
        import_button.click(load_midi, [midi_input, state], midi_outputs, api_name="load_midi", concurrency_id="editing")
        transcribe_button.click(transcribe, [state], midi_outputs, api_name="transcribe", concurrency_id="editing")
        export_button.click(export_midi, [editor, state], target_download, api_name="export_midi", concurrency_id="editing")
        generate_button.click(generate, [editor, state, edit_start, edit_end, method, steps, cfg, context_midi, drop_context_audio],
                              [output_audio, target_download, status], api_name="generate", concurrency_id="editing")
    return demo


def initialize_models():
    global MODEL, CODEC, DEVICE
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    DEVICE = resolve_device(os.environ.get("SPANSYNTH_DEVICE", "auto"))
    assets, codec_assets = resolve_assets()
    MODEL, _ = load_model(*assets, device=DEVICE)
    CODEC = load_scalar_model(codec_assets[1], codec_assets[0], device=DEVICE,
                              dtype=torch.bfloat16 if DEVICE.type == "cuda" else torch.float32)
    print(f"SpanSynth-Edit ready on {DEVICE}", flush=True)


if __name__ == "__main__":
    if os.environ.get("SPANSYNTH_SKIP_MODELS") != "1":
        initialize_models()
    theme = gr.themes.Soft(primary_hue="indigo", neutral_hue="slate")
    # Keep the score and surrounding controls on the same light canvas in both browser modes.
    theme.set(**{key: getattr(theme, key.removesuffix("_dark")) for key in vars(theme)
                 if key.endswith("_dark") and hasattr(theme, key.removesuffix("_dark"))})
    build_app().queue(max_size=16).launch(css=(HERE / "style.css").read_text(), theme=theme,
                                         max_file_size="50mb", show_error=True)
