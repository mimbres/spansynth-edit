"""Interactive MIDI-guided audio editing, shared by local Gradio and ZeroGPU."""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
from functools import partial
from html import escape
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import time
from urllib.request import urlopen

# The Space runs this script directly; share the repository's inference package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gradio as gr
from gradio_client import Client, handle_file
from huggingface_hub import CommitOperationAdd, HfApi, RepoFolder, get_token, hf_hub_url
from mido import Message, MetaMessage, MidiFile, MidiTrack
import numpy as np
import soundfile as sf
import spaces
import torch

from spansynth.audio import (AudioCrop, read_audio, encode_audio, decode_audio, assemble_output,
                            SAMPLE_RATE, HISTORY_FRAMES, HISTORY_SAMPLES, PAYLOAD_SAMPLES)
from spansynth.checkpoint import resolve_assets, load_model
from spansynth.cli import resolve_device
from spansynth.codec import load_scalar_model
from spansynth.midi import NOTE_DTYPE, parse_midi_notes, prepare_midi
from spansynth.sampling import sample
from spansynth.vocabulary import PROGRAM_GROUPS, PROGRAM_TO_CATEGORY

HERE = Path(__file__).resolve().parent
MODEL = CODEC = DEVICE = None
MAX_NOTES = 10000
EULER_STEPS = [4, 8, 16, 24, 32, 48, 64]
GALLERY_REPO = "mimbres/spansynth-edit-gallery"
SPACE_URL = "https://mimbres-spansynth-edit.hf.space/"
WORK_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}\Z")
INSTRUMENTS = [{"program": programs[0], "name": name, "members": list(programs)}
               for name, programs in PROGRAM_GROUPS.items() if name not in {"Banjo", "Sitar", "Fiddle"}]
EXAMPLE_ROOT = "https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/"
EXAMPLE_FILES = {
    "Slakh": ("early-slakh-track00006-original.mp3", "early-slakh-track00006-before.mid"),
    "Kraisler": ("early-kraisler-track01-original.mp3", "early-kraisler-track01-before.mid"),
    "Jazz intro": ("jazz-intro-ourmusicbox.mp3", None),
}


def cleanup(session):
    if isinstance(session, dict) and session.get("directory"):
        shutil.rmtree(session["directory"], ignore_errors=True)


def finite_number(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if not lower <= value <= upper:
        raise ValueError(f"{name} must be between {lower:g} and {upper:g}.")
    return value


def validate_score(value, session, *, allow_unsupported=False):
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
        if not allow_unsupported and fields["program"] not in PROGRAM_TO_CATEGORY:
            raise ValueError(f"Program {fields['program']} is not supported. Select that track and choose an instrument.")
        result.append({"start": float(onset), "duration": float(length), **fields})
    if len({note["program"] for note in result if note["program"] != 128}) > 15:
        raise ValueError("Use at most 15 melodic instruments plus drums for MIDI export. Merge a few tracks first.")
    return result


def changed_region(source, target, duration):
    """Cover added, removed, and modified notes on the 40 ms generation grid."""
    def events(notes):
        return Counter((round(n["start"] * SAMPLE_RATE), round((n["start"] + n["duration"]) * SAMPLE_RATE),
                        n["pitch"], n["velocity"], n["program"]) for n in notes)
    before, after = events(source or []), events(target)
    changed = list(before - after) + list(after - before)
    if not changed:
        return None
    hop = SAMPLE_RATE // 25
    first = min(n[0] for n in changed) // hop
    last = (max(n[1] for n in changed) + hop - 1) // hop
    return first / 25, min(duration, last / 25)


def update_region(value, session, enabled):
    if not enabled:
        return gr.update(interactive=True), gr.update(interactive=True), "Manual region. Choose start and end below."
    region = None
    if session:
        try:
            notes = validate_score(value, session, allow_unsupported=True)
            region = changed_region(session.get("source_notes"), notes, session["duration"])
        except ValueError:
            return gr.skip(), gr.skip(), "Finish editing the score to update the region."
    if region is None:
        return (gr.update(interactive=True), gr.update(interactive=True),
                "No note changes yet. Edit notes to set the region automatically, or choose it manually.")
    return (gr.update(value=region[0], interactive=False), gr.update(value=region[1], interactive=False),
            "Covers all changed notes, including removed notes and their original positions. Boundaries align to 40 ms.")


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
        raise gr.Error("Upload audio or choose a sample first.")
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
               "source_notes": None, "suggested_title": f"{Path(audio).stem[:93]} · edit"}
    preview = directory / "input.wav"
    sf.write(preview, crop.original, SAMPLE_RATE, subtype="FLOAT")
    cleanup(old_session)
    start, end = (6.4, 14.08) if duration >= 14.08 else (duration * 0.25, duration * 0.75)
    return session, str(preview), editor_value(session, []), start, end, None, None, None, "Clip ready. Transcribe it, upload MIDI, or add notes.", ""


def load_midi(path, session):
    if not session:
        raise gr.Error("Load an audio clip first.")
    if not path:
        raise gr.Error("Choose a MIDI file aligned with the full uploaded recording.")
    notes = midi_notes(path, session["crop_start"], session["duration"])
    return install_source_notes(session, notes, "MIDI loaded")


def install_source_notes(session, notes, message):
    session = {**session, "source_notes": notes}
    session.pop("take", None)
    original = write_midi(notes, Path(session["directory"]) / "original.mid")
    unsupported = sorted({n["program"] for n in notes} - PROGRAM_TO_CATEGORY.keys())
    suffix = f" Remap unsupported programs in the editor: {unsupported}." if unsupported else ""
    return session, editor_value(session, notes), original, None, None, f"{message}: {len(notes)} notes. Edit a track, then generate.{suffix}", ""


def midi_from_html(value):
    if not isinstance(value, str):
        raise ValueError("YourMT3+ did not return a transcription.")
    match = re.search(r"data:audio/(?:midi|mid);base64,([A-Za-z0-9+/=]+)", value)
    if not match or len(match[1]) > 8_000_000:
        raise ValueError("YourMT3+ returned no readable MIDI file. Please retry or upload MIDI.")
    payload = base64.b64decode(match[1], validate=True)
    MidiFile(file=io.BytesIO(payload))
    return payload


def transcribe(session, request: gr.Request, progress=gr.Progress()):
    if not session:
        raise gr.Error("Load an audio clip first.")
    progress(0, desc="Waiting for YourMT3+")
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
        raise gr.Error(f"YourMT3+ transcription could not finish. {error}") from error
    progress(1, desc="Transcription ready")
    return install_source_notes(session, notes, "YourMT3+ transcription")


def export_midi(value, session):
    try:
        notes = validate_score(value, session)
        path = write_midi(notes, Path(session["directory"]) / "edited.mid")
        gr.Info("Edits applied. Your edited MIDI is ready to download.")
        return path
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


def generate(value, session, start, end, method, steps, cfg, context_midi, drop_context_audio, auto_region=False):
    try:
        notes = validate_score(value, session)
        if auto_region:
            region = changed_region(session.get("source_notes"), notes, session["duration"])
            if region is not None:
                start, end = region
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
        elapsed = time.perf_counter() - began
        session["take"] = {
            "directory": str(take), "notes": notes, "elapsed_seconds": elapsed,
            "settings": {"start": start, "end": end, "method": method, "steps": int(steps), "cfg": cfg,
                         "context_midi": context_midi, "drop_context_audio": drop_context_audio,
                         "auto_region": auto_region},
            "view": editor_view(json.loads(value).get("view", {})),
        }
        first, last = math.floor(start * 25 + 1e-9) / 25, min(math.ceil(end * 25 - 1e-9) / 25, session["duration"])
        return (str(take / "output.wav"), target_path,
                f"Generated {first:.2f}–{last:.2f} s. Keep editing the score to try another version.",
                f"Generation time · {elapsed:.1f} s")
    except (ValueError, RuntimeError, OSError) as error:
        raise gr.Error(str(error)) from error


def editor_view(value):
    """Keep only the small set of display settings understood by the piano roll."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, low, high in (("program", 0, 128), ("zoom", 1, 4), ("scrollTop", 0, 2000), ("scrollLeft", 0, 5000)):
        number = value.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and low <= number <= high:
            result[key] = int(number) if key == "program" else number
    if value.get("snap") in (0, .04, .1, .25):
        result["snap"] = value["snap"]
    colors = value.get("colors")
    result["colors"] = []
    for entry in colors[:129] if isinstance(colors, list) else []:
        if not isinstance(entry, list) or len(entry) != 2:
            continue
        p, color = entry
        if type(p) is int and 0 <= p <= 128 and isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            result["colors"].append([p, color])
    extra = value.get("extraTracks")
    result["extraTracks"] = [p for p in extra[:16] if type(p) is int and 0 <= p <= 128] if isinstance(extra, list) else []
    return result


def work_url(work_id, filename):
    if not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id):
        raise ValueError("Invalid saved work link.")
    return hf_hub_url(GALLERY_REPO, f"{work_id}/{filename}", repo_type="dataset")


def read_project(work_id):
    with urlopen(work_url(work_id, "project.json"), timeout=20) as response:
        content = response.read(4_000_001)
    if len(content) > 4_000_000:
        raise ValueError("The saved score is too large.")
    project = json.loads(content)
    if not isinstance(project, dict) or project.get("version") != 1:
        raise ValueError("This saved work uses an unsupported format.")
    return project


def gallery_html():
    """Read the published works directly; there is no separate gallery index."""
    try:
        folders = [entry.path for entry in HfApi(token=False).list_repo_tree(GALLERY_REPO, repo_type="dataset")
                   if isinstance(entry, RepoFolder) and WORK_ID.fullmatch(entry.path)]
        cards = []
        for work_id in sorted(folders, reverse=True):
            project = read_project(work_id)
            if not project.get("listed"):
                continue
            title = escape(str(project.get("title", "Untitled")))
            description = escape(str(project.get("description", "")))
            settings = project["settings"]
            method = "spansynth-edit + flowedit" if settings["method"] == "flowedit" else "spansynth-edit"
            duration = float(project["duration"])
            colors = dict(editor_view(project.get("view", {})).get("colors", []))
            notes = project["notes"]
            pitches = [n["pitch"] for n in notes]
            low, high = min(pitches, default=48) - 2, max(pitches, default=72) + 2
            bars = []
            for note in notes:
                x, width = note["start"] / duration * 360, max(2, note["duration"] / duration * 360)
                y = 12 + (high - note["pitch"]) / (high - low) * 88
                color = colors.get(note["program"], "#b091dc")
                bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="3" rx="1" fill="{color}"/>')
            preview = '<svg viewBox="0 0 360 116" role="img" aria-label="Edited score preview">' + ''.join(bars) + '</svg>'
            cards.append(f'<article class="work-card"><div class="work-score">{preview}</div><div class="work-body">'
                         f'<div class="work-method">{method} · {duration:.2f} s</div><h3>{title}</h3><p>{description}</p>'
                         f'<label>Original<button class="audio-start" type="button" data-audio-start aria-label="Original audio: go to start">⏮ Start</button><audio controls preload="none" src="{work_url(work_id, "input.wav")}"></audio></label>'
                         f'<label>Edited<button class="audio-start" type="button" data-audio-start aria-label="Edited audio: go to start">⏮ Start</button><audio controls preload="none" src="{work_url(work_id, "output.wav")}"></audio></label>'
                         f'<a class="work-open" href="?work={work_id}" target="_blank" rel="noopener">Open in editor ↗</a>'
                         '</div></article>')
        return '<div class="work-grid">' + ''.join(cards) + '</div>' if cards else (
            '<div class="gallery-empty"><span>♫</span><h3>A place for finished ideas</h3>'
            '<p>Saved works will appear here, ready to listen to and open in the editor.</p></div>')
    except Exception:
        return '<div class="gallery-empty"><p>The gallery is temporarily unavailable. Please refresh it in a moment.</p></div>'


def default_work_title():
    return datetime.now(timezone.utc).strftime("Music edit · %Y-%m-%d %H:%M")


def initial_work_title():
    title = default_work_title()
    return title, title


def resolved_work_title(title, session):
    return str(title or "").strip() or (session or {}).get("suggested_title") or default_work_title()


def suggest_work_title(title, session, automatic_title):
    if str(title or "").strip() and title != automatic_title:
        return title, automatic_title
    title = resolved_work_title("", session)
    return title, title


def publishing_status(profile: gr.OAuthProfile | None):
    if profile is None:
        message = ("Sign in to this Space to publish. Being signed in to the Hugging Face website alone does not "
                   "authorize this Space. Sign-in opens a new tab; return here afterward to keep your work.")
    elif profile.username == "mimbres":
        message = "Signed in as **mimbres**. Ready to save your generated take."
    else:
        message = f"Signed in as **{escape(profile.username)}**. Publishing is currently limited to **mimbres**."
    return gr.update(value=message), gr.update(visible=profile is None)


def save_work(title, description, listed, session, profile: gr.OAuthProfile | None):
    if profile is None:
        raise gr.Error("Sign in to this Space, then return to this tab and save. Your edit will stay here.")
    if profile.username != "mimbres":
        raise gr.Error("Publishing is currently limited to mimbres. Sign in with that account to save.")
    if not session or not session.get("take"):
        raise gr.Error("Generate an edited clip before saving a work.")
    title = resolved_work_title(title, session)
    description = str(description or "").strip()
    if len(title) > 100 or len(description) > 1000:
        raise gr.Error("Keep the title within 100 characters and the description within 1,000 characters.")
    token = os.environ.get("SPANSYNTH_GALLERY_TOKEN")
    if not token and not os.environ.get("SPACE_ID"):
        token = get_token()
    if not token:
        raise gr.Error("Gallery publishing is not configured yet.")
    take = session["take"]
    directory = Path(take["directory"])
    if not (directory / "output.wav").is_file():
        raise gr.Error("This take has expired. Generate it again before saving.")
    crop = session["crop"]
    if "work_id" not in take:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:45] or "music"
        take["work_id"] = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + slug + "-" + secrets.token_hex(4)
        take["created_at"] = datetime.now(timezone.utc).isoformat()
    project = {"version": 1, "title": title, "description": description, "author": "mimbres", "listed": bool(listed),
               "created_at": take["created_at"], "duration": session["duration"], "crop_start": session["crop_start"],
               "gain": crop.gain, "input_rate": crop.input_rate, "input_channels": crop.input_channels,
               "padded_samples": crop.padded_samples, "source_notes": session["source_notes"],
               "notes": take["notes"], "settings": take["settings"], "view": take["view"],
               "elapsed_seconds": take.get("elapsed_seconds")}
    sf.write(directory / "input.wav", crop.original, SAMPLE_RATE, subtype="FLOAT")
    # Preserve the exact normalized codec input, including the history before the crop.
    sf.write(directory / "context.wav", crop.waveform.cpu().numpy(), SAMPLE_RATE, subtype="FLOAT")
    write_midi(session["source_notes"] or [], directory / "original.mid")
    (directory / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n")
    files = ("input.wav", "context.wav", "original.mid", "edited.mid", "output.wav", "project.json")
    try:
        HfApi(token=token).create_commit(
            repo_id=GALLERY_REPO, repo_type="dataset", commit_message=f"Save {title}",
            operations=[CommitOperationAdd(path_in_repo=f'{take["work_id"]}/{name}', path_or_fileobj=directory / name)
                        for name in files])
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        print(f"Gallery upload failed: {type(error).__name__}; HTTP {status}", flush=True)
        if status in (401, 403):
            raise gr.Error("The gallery's upload credential needs attention. Your edit is still here; download your audio and MIDI before leaving.") from None
        raise gr.Error("The gallery could not be reached to save your work. Your take is still here; please try again.") from None
    gr.Info("Saved. Anyone with this link can listen and open an editable copy.")
    return SPACE_URL + "?work=" + take["work_id"], gallery_html()


def publish_work(title, description, listed, session, profile: gr.OAuthProfile | None):
    """Keep save feedback beside the button without clearing a previous share link."""
    title = resolved_work_title(title, session)
    try:
        link, gallery = save_work(title, description, listed, session, profile)
    except gr.Error as error:
        return gr.skip(), gr.skip(), f"**Not saved.** {escape(error.message)}", title
    except Exception as error:
        print(f"Gallery save failed: {type(error).__name__}", flush=True)
        return gr.skip(), gr.skip(), ("**Not saved.** The files could not be prepared. "
                                     "Your edit is still here; download your audio and MIDI before leaving, then try saving again."), title
    return link, gallery, "**Saved.** Copy the link below to share this version.", title


def load_work(work_id, old_session):
    """Restore a saved take without rerunning transcription or generation."""
    directory = None
    try:
        project = read_project(work_id)
        duration = finite_number(project["duration"], "Clip duration", .2, 20.48)
        crop_start = finite_number(project["crop_start"], "Crop start", 0, 3600)
        gain = finite_number(project["gain"], "Input gain", 1e-8, 1)
        session = {"clip": "saved", "duration": duration}
        notes = validate_score(json.dumps({"clip": "saved", "notes": project["notes"]}), session)
        source = project["source_notes"]
        if source is not None:
            source = validate_score(json.dumps({"clip": "saved", "notes": source}), session, allow_unsupported=True)
        settings = project["settings"]
        start = finite_number(settings["start"], "Region start", 0, duration)
        end = finite_number(settings["end"], "Region end", start, duration)
        steps = finite_number(settings["steps"], "Steps", 1, 64)
        cfg = finite_number(settings["cfg"], "Guidance", 0, 8)
        if end <= start or int(steps) != steps or settings["method"] not in ("ordinary", "flowedit"):
            raise ValueError("Invalid saved generation settings.")
        if not all(isinstance(settings[key], bool) for key in ("context_midi", "drop_context_audio")):
            raise ValueError("Invalid saved context settings.")
        directory = Path(tempfile.mkdtemp(prefix="spansynth-session-"))
        take = Path(tempfile.mkdtemp(prefix="take-", dir=directory))
        for name in ("input.wav", "context.wav", "output.wav", "original.mid", "edited.mid"):
            with urlopen(work_url(work_id, name), timeout=30) as response:
                content = response.read(8_000_001)
            if len(content) > 8_000_000:
                raise ValueError("A saved audio file exceeds the clip size limit.")
            (take / name).write_bytes(content)
        audio = {}
        for name, length in (("input", round(duration * SAMPLE_RATE)), ("output", round(duration * SAMPLE_RATE)),
                             ("context", HISTORY_SAMPLES + PAYLOAD_SAMPLES)):
            samples, rate = sf.read(take / f"{name}.wav", dtype="float32")
            if rate != SAMPLE_RATE or samples.shape != (length,) or not np.isfinite(samples).all():
                raise ValueError("Invalid saved audio.")
            audio[name] = samples
        crop = AudioCrop(torch.from_numpy(audio["context"]), audio["input"], gain,
                         project["input_rate"], project["input_channels"], project["padded_samples"])
        session.update(directory=str(directory), clip=directory.name, crop=crop, crop_start=crop_start, source_notes=source)
        shutil.copyfile(take / "input.wav", directory / "input.wav")
        view = editor_view(project.get("view", {}))
        elapsed = project.get("elapsed_seconds")
        if elapsed is not None:
            finite_number(elapsed, "Generation time", 0, 86400)
        session["take"] = {"directory": str(take), "notes": notes, "settings": settings, "view": view,
                           "elapsed_seconds": elapsed}
        editor = json.loads(editor_value(session, notes))
        editor["view"] = view
        cleanup(old_session)
        title = str(project["title"])
        session["suggested_title"] = title
        return (session, str(directory / "input.wav"), json.dumps(editor), start, end, str(take / "output.wav"),
                str(take / "original.mid") if source is not None else None, str(take / "edited.mid"),
                f'Opened “{escape(title)}”. You are editing a copy; the shared work stays unchanged.',
                settings["method"], gr.update(choices=sorted(set(EULER_STEPS + [int(steps)])), value=int(steps)),
                cfg, settings["context_midi"], settings["drop_context_audio"],
                title, str(project.get("description", "")), SPACE_URL + "?work=" + work_id,
                f"Generation time · {elapsed:.1f} s" if elapsed is not None else "", settings.get("auto_region", False),
                str(directory / "input.wav"), 0, duration)
    except Exception:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise gr.Error("This saved work could not be opened. Check the link or try again in a moment.") from None


def open_shared_work(work_id, session):
    return load_work(work_id, session) if work_id else tuple(gr.skip() for _ in range(22))


def load_example(old_session, sample_name="Slakh"):
    if sample_name not in EXAMPLE_FILES:
        raise gr.Error("Choose one of the three sample recordings.")
    audio_name, midi_name = EXAMPLE_FILES[sample_name]
    with tempfile.TemporaryDirectory(prefix="spansynth-example-") as folder:
        folder = Path(folder)
        for filename in (audio_name, midi_name):
            if filename is None:
                continue
            local = HERE.parent / "demo" / "assets" / filename
            if local.is_file():
                shutil.copyfile(local, folder / filename)
            else:
                with urlopen(EXAMPLE_ROOT + filename, timeout=30) as response:
                    (folder / filename).write_bytes(response.read())
        loaded = load_clip(str(folder / audio_name), 0, 20.48, old_session)
        session, preview, editor, start, end, *_ = loaded
        session["suggested_title"] = f"{sample_name} · edit"
        midi = None
        status = f"{sample_name} loaded. Use YourMT3+ to transcribe it, then edit the notes."
        if midi_name:
            notes = midi_notes(folder / midi_name, 0, session["duration"])
            session, editor, midi, _, _, status, _ = install_source_notes(session, notes, f"{sample_name} sample")
        sample_audio = Path(session["directory"]) / audio_name
        shutil.copyfile(folder / audio_name, sample_audio)
        return session, preview, editor, start, end, None, midi, None, status, "", str(sample_audio), 0, session["duration"]


EDITOR_HTML = """
<div class="roll-shell">
  <audio data-role="player" preload="metadata" hidden></audio>
  <div class="roll-toolbar">
    <div class="track-control"><span>Track</span><div class="track-picker">
      <button data-role="track" type="button" aria-label="MIDI track" aria-describedby="midi-track-name" aria-haspopup="listbox" aria-expanded="false" aria-controls="midi-track-options">
        <span class="track-swatch" aria-hidden="true"></span><span id="midi-track-name" data-role="track-name">Choose a track</span><span class="track-arrow" aria-hidden="true">▾</span>
      </button>
      <div id="midi-track-options" data-role="track-options" role="listbox" aria-label="MIDI tracks" hidden></div>
    </div></div>
    <label>Instrument <select data-role="instrument" aria-label="Track instrument"></select></label>
    <button data-action="add-track">+ Instrument</button>
  </div>
  <div class="roll-toolbar roll-add" data-role="add-panel" hidden>
    <label>New instrument <select data-role="new-instrument" aria-label="New instrument"></select></label>
    <button data-action="confirm-track">Add instrument</button><button data-action="cancel-track">Cancel</button>
    <span>Confirm the instrument before drawing notes.</span>
  </div>
  <div class="roll-toolbar roll-tools">
    <button data-tool="select" title="Select and move notes (V)">↖ Select</button>
    <button data-tool="pencil" title="Draw notes (D)">✎ Pencil</button>
    <button data-tool="erase" title="Erase notes (E)">▱ Eraser</button>
    <label>Snap <select data-role="snap" aria-label="Time snap"><option value="0">Off</option><option value="0.04" selected>40 ms</option><option value="0.1">100 ms</option><option value="0.25">250 ms</option></select></label>
    <button data-action="undo" title="Undo (Ctrl/Cmd+Z)">Undo</button>
    <button data-action="redo" title="Redo (Ctrl/Cmd+Shift+Z)">Redo</button>
    <button data-action="delete">Delete selected</button>
  </div>
  <div class="roll-toolbar roll-secondary">
    <button data-action="restart" title="Go to start"><span class="transport-icon" aria-hidden="true">⏮</span> Start</button>
    <label>Audio <select data-role="audio-source" aria-label="Audio to play"><option value="original">Original</option><option value="generated">Generated</option></select></label>
    <button data-action="play"><span class="transport-icon" aria-hidden="true">▶</span> Play audio</button>
    <button data-action="preview"><span class="transport-icon" aria-hidden="true">▶</span> Preview notes</button>
    <button data-action="stop"><span class="transport-icon" aria-hidden="true">■</span> Stop</button>
    <label>Listen <select data-role="listen" aria-label="Playback range"><option value="cursor">From cursor</option><option value="selection">Selection</option><option value="whole">Whole clip</option></select></label>
    <span data-role="listen-range" aria-live="polite"></span>
  </div>
  <div class="roll-toolbar roll-secondary">
    <label>Zoom <input data-role="zoom" aria-label="Timeline zoom" type="range" min="1" max="4" step="0.25" value="1"></label>
    <label>Velocity <input data-role="velocity" aria-label="Selected note velocity" type="number" min="1" max="127" value="90"></label>
    <span data-role="count"></span>
  </div>
  <p data-role="edit-lock" class="roll-lock" role="status" hidden>Editing paused — choose <b>Add instrument</b> or <b>Cancel</b> above.</p>
  <div class="roll-scroll" tabindex="0" aria-label="Piano roll. Only the selected track can be edited. Select notes, draw with the pencil, or erase. Drag the waveform to select a playback range.">
    <canvas data-role="roll" aria-label="Editable piano roll"></canvas>
  </div>
  <div class="roll-help">
    <p>Only the selected track is editable. Drag empty space to select its notes · Drag selected notes to move · Drag a note’s right edge to resize</p>
    <p>Click the waveform to seek · Drag across it to listen to a range · Select notes to preview only those notes</p>
    <p><kbd>Shift</kbd> + click to add to selection · <kbd>↑</kbd> <kbd>↓</kbd> transpose · <kbd>Shift</kbd> + arrows for an octave · <kbd>Del</kbd> remove</p>
    <p><kbd>Ctrl</kbd> / <kbd>⌘ Cmd</kbd> + <kbd>Z</kbd> undo · <kbd>V</kbd> select · <kbd>D</kbd> pencil · <kbd>E</kbd> eraser · <kbd>Esc</kbd> clear selection</p>
    <p>Click piano keys or draw notes to hear their pitch immediately. Preview notes uses a simple synth. Choose Generated audio to hear the model’s instruments.</p>
  </div>
  <p data-role="detail" class="roll-detail">Load a clip to begin.</p>
</div>
"""

THEME_JS = """
const button = element.querySelector('.theme-toggle');
function updateThemeButton() {
  const dark = !!element.closest('.dark');
  button.textContent = dark ? '☀ Light mode' : '☾ Dark mode';
  button.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
}
button.addEventListener('click', () => {
  const current = element.closest('.dark');
  (current || document.body).classList.toggle('dark', !current);
  const url = new URL(location.href);
  url.searchParams.set('__theme', current ? 'light' : 'dark');
  history.replaceState(history.state, '', url);
  updateThemeButton();
});
document.addEventListener('click', event => {
  const button = event.target.closest('[data-audio-start]');
  const audio = button?.parentElement.querySelector('audio');
  if (audio) { audio.pause(); audio.currentTime = 0; }
});
function addAudioStartButtons() {
  for (const id of ['upload-audio', 'source-audio', 'result-audio']) {
    const player = document.getElementById(id);
    const controls = player?.querySelector('[data-testid="waveform-controls"] .play-pause-wrapper');
    if (!controls || controls.querySelector('.audio-restart')) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'audio-restart';
    button.title = 'Go to start';
    button.setAttribute('aria-label', 'Go to start');
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h3v16H5zM19 4v16L9 12z" fill="currentColor"/></svg>';
    button.addEventListener('click', () => document.getElementById(id + '-start')?.click());
    controls.prepend(button);
  }
}
const audioControlsObserver = new MutationObserver(records => {
  if (records.some(record => [...record.addedNodes].some(node => node.nodeType === 1))) addAudioStartButtons();
});
audioControlsObserver.observe(element.closest('.gradio-container'), {childList: true, subtree: true});
addAudioStartButtons();
function refreshPublishingStatus() {
  if (document.visibilityState !== 'visible') return;
  const refresh = document.querySelector('#publishing-auth-refresh');
  (refresh?.matches('button') ? refresh : refresh?.querySelector('button'))?.click();
}
window.addEventListener('focus', refreshPublishingStatus);
document.addEventListener('visibilitychange', refreshPublishingStatus);
const observer = new MutationObserver(updateThemeButton);
for (let node = element; node; node = node.parentElement)
  observer.observe(node, {attributes:true, attributeFilter:['class']});
updateThemeButton();
"""


def build_app():
    with gr.Blocks(title="SpanSynth-Edit", delete_cache=(3600, 3600)) as demo:
        gr.HTML('<header class="hero"><button class="theme-toggle" type="button" aria-label="Switch to dark mode">☾ Dark mode</button><div class="eyebrow">SPANSYNTH-EDIT · MIDI-GUIDED MUSIC EDITING</div><h1>Change the notes.<br><span>Keep the musical context.</span></h1><p>Upload a recording, edit its score, and hear a new version of the selected region.</p><div class="hero-links"><a href="https://mimbres.github.io/spansynth-edit/" target="_blank">Listen to demos ↗</a><a href="https://github.com/mimbres/spansynth-edit" target="_blank">Source code ↗</a><a href="https://huggingface.co/mimbres/spansynth-edit" target="_blank">Model weights ↗</a></div></header>', apply_default_css=False, js_on_load=THEME_JS)
        state = gr.State(None, delete_callback=cleanup)
        shared_id = gr.Textbox(visible=False)
        with gr.Tabs():
            with gr.Tab("Editor", id="editor"):
                with gr.Group(elem_classes="step-card"):
                    gr.Markdown("### 1 · Choose your audio")
                    with gr.Row():
                        upload_start = gr.Button("Go to start", visible="hidden", elem_id="upload-audio-start")
                        audio = gr.Audio(label="Upload a recording", sources=["upload"], type="filepath", editable=False,
                                         buttons=["download"], elem_id="upload-audio")
                        with gr.Column():
                            gr.Markdown("Work on one clip of up to **20.48 seconds**. Choose a sample below or upload your own recording.")
                            with gr.Row():
                                crop_start = gr.Number(value=0, minimum=0, precision=2, label="Crop start · seconds")
                                duration = gr.Number(value=20.48, minimum=0.2, maximum=20.48, precision=2, label="Clip length · seconds")
                            with gr.Row():
                                load = gr.Button("Load clip", variant="primary")
                    gr.Markdown("**Try a sample** — Slakh and Kraisler include MIDI. Transcribe the jazz clip with YourMT3+.", elem_classes="sample-note")
                    with gr.Row():
                        slakh_example = gr.Button("Slakh · 20 s", elem_classes="sample-button")
                        kraisler_example = gr.Button("Kraisler · 20 s", elem_classes="sample-button")
                        jazz_example = gr.Button("Jazz intro · 11 s", elem_classes="sample-button")
                    source_start = gr.Button("Go to start", visible="hidden", elem_id="source-audio-start")
                    original_audio = gr.Audio(label="Original clip", interactive=False, type="filepath", buttons=["download"], elem_id="source-audio")
                with gr.Group(elem_classes="step-card"):
                    gr.Markdown("### 2 · Edit the score")
                    with gr.Row():
                        transcribe_button = gr.Button("Transcribe with YourMT3+", variant="primary")
                        gr.Markdown("Transcription can make mistakes. Correct the notes before generating. Slakh and Kraisler already include MIDI.")
                    with gr.Accordion("Already have aligned MIDI?", open=False):
                        midi_input = gr.File(label="Original MIDI aligned with the full uploaded recording", file_types=[".mid", ".midi"])
                        import_button = gr.Button("Load MIDI into editor")
                    editor = gr.HTML(value="{}", html_template=EDITOR_HTML, js_on_load=(HERE / "editor.js").read_text(),
                                     css_template="", apply_default_css=False, elem_id="note-editor")
                    export_button = gr.Button("Apply edits", variant="primary", size="lg", elem_id="apply-edits")
                    gr.Markdown("Apply edits to update your MIDI download. **Apply & Generate** below also uses your latest edits.", elem_classes="apply-note")
                    with gr.Row():
                        source_download = gr.File(label="Original MIDI", interactive=False)
                        target_download = gr.File(label="Edited MIDI", interactive=False)
                with gr.Group(elem_classes="step-card"):
                    gr.Markdown("### 3 · Generate the selected region")
                    auto_region = gr.Checkbox(value=True, label="Auto region from note edits")
                    region_hint = gr.Markdown("Edit notes to set the region automatically, or choose it manually.", elem_classes="apply-note")
                    with gr.Row():
                        edit_start = gr.Number(value=6.4, minimum=0, precision=2, label="Region start · clip seconds", elem_id="edit-start")
                        edit_end = gr.Number(value=14.08, minimum=0, precision=2, label="Region end · clip seconds", elem_id="edit-end")
                        method = gr.Dropdown(choices=[("spansynth-edit", "ordinary"), ("spansynth-edit + flowedit", "flowedit")], value="ordinary", label="Method")
                        steps = gr.Dropdown(choices=EULER_STEPS, value=16, label="Euler steps")
                    with gr.Accordion("Generation settings", open=False):
                        with gr.Row():
                            cfg = gr.Slider(0, 8, value=2, step=0.1, label="MIDI guidance (CFG)")
                        with gr.Row():
                            context_midi = gr.Checkbox(value=False, label="Use original MIDI outside the region")
                            drop_context_audio = gr.Checkbox(value=False, label="Drop audio context")
                    generate_button = gr.Button("Apply & Generate", variant="primary", size="lg")
                    status = gr.Markdown("Choose a recording or try a sample.", elem_id="run-status")
                    output_start = gr.Button("Go to start", visible="hidden", elem_id="result-audio-start")
                    output_audio = gr.Audio(label="Edited clip · 48 kHz mono", interactive=False, type="filepath", buttons=["download"], elem_id="result-audio")
                    generation_time = gr.Markdown("", elem_id="generation-time")
                with gr.Group(elem_classes=["step-card", "share-card"]):
                    gr.Markdown("### Save & Share")
                    gr.Markdown("Save the last generated take, with the score and settings that produced it. Shared audio and MIDI are public.")
                    with gr.Row():
                        work_title = gr.Textbox(value=default_work_title(), label="Title", placeholder="Give this version a name", max_length=100)
                        automatic_title = gr.State("")
                        work_description = gr.Textbox(label="Description · optional", placeholder="What did you change?", max_length=1000)
                    listed = gr.Checkbox(value=True, label="Show in gallery")
                    gr.Markdown("Publishing is currently curated by **mimbres**. Unlisted works are also public.", elem_classes="apply-note")
                    # Enable Gradio's OAuth routes; its built-in click handler reloads the editing tab.
                    gr.LoginButton(visible=False)
                    sign_in_button = gr.Button("Sign in to publish ↗", link="/login/huggingface", link_target="_blank",
                                               variant="huggingface", size="sm")
                    auth_status = gr.Markdown("Checking sign-in status…", elem_classes="apply-note")
                    refresh_auth = gr.Button("Refresh sign-in status", visible="hidden", elem_id="publishing-auth-refresh")
                    save_button = gr.Button("Save & Share", variant="primary")
                    save_status = gr.Markdown("", elem_classes="apply-note", elem_id="save-status")
                    share_link = gr.Textbox(label="Saved work link", interactive=False, buttons=["copy"])
            with gr.Tab("Gallery", id="gallery") as gallery_tab:
                gr.Markdown("## Made with SpanSynth-Edit")
                gr.Markdown("Listen to finished edits. Open any work in a new tab to explore the score and make your own version.")
                refresh_gallery = gr.Button("Refresh gallery", size="sm")
                gallery = gr.HTML('<div class="gallery-empty"><p>Loading the gallery…</p></div>', apply_default_css=False, elem_id="work-gallery")
        gr.Markdown("Audio outside the selected region is preserved. Region boundaries snap outward to 40 ms. Unsaved uploads and results are temporary. Saved works stay available through their shared links. Transcription uses [YourMT3+](https://huggingface.co/spaces/mimbres/YourMT3); generation runs here. ZeroGPU availability and usage limits depend on your Hugging Face account.", elem_classes="footer-note")
        for button, player in ((upload_start, audio), (source_start, original_audio), (output_start, output_audio)):
            button.click(lambda: gr.update(playback_position=0), None, player, queue=False, show_progress="hidden")
        clip_outputs = [state, original_audio, editor, edit_start, edit_end, output_audio, source_download, target_download, status, generation_time]
        load_event = load.click(load_clip, [audio, crop_start, duration, state], clip_outputs, api_name="load_clip", concurrency_id="editing")
        sample_outputs = [*clip_outputs, audio, crop_start, duration]
        slakh_event = slakh_example.click(load_example, [state], sample_outputs, api_name="example", concurrency_id="editing")
        kraisler_event = kraisler_example.click(partial(load_example, sample_name="Kraisler"), [state], sample_outputs,
                               api_name="example_kraisler", concurrency_id="editing")
        jazz_event = jazz_example.click(partial(load_example, sample_name="Jazz intro"), [state], sample_outputs,
                            api_name="example_jazz", concurrency_id="editing")
        midi_outputs = [state, editor, source_download, target_download, output_audio, status, generation_time]
        import_event = import_button.click(load_midi, [midi_input, state], midi_outputs, api_name="load_midi", concurrency_id="editing")
        transcribe_event = transcribe_button.click(transcribe, [state], midi_outputs, api_name="transcribe", concurrency_id="editing")
        export_button.click(export_midi, [editor, state], target_download, api_name="export_midi", concurrency_id="editing")
        gr.on([editor.change, auto_region.change], update_region, [editor, state, auto_region],
              [edit_start, edit_end, region_hint], queue=False, trigger_mode="always_last", show_progress="hidden",
              api_name="update_region")
        gr.on([edit_start.change, edit_end.change], None, None, None, queue=False,
              js="() => { window.dispatchEvent(new Event('spansynth-region')); }")
        generate_event = generate_button.click(generate, [editor, state, edit_start, edit_end, method, steps, cfg, context_midi, drop_context_audio, auto_region],
                              [output_audio, target_download, status, generation_time], api_name="generate", concurrency_id="editing")
        for event in (load_event, slakh_event, kraisler_event, jazz_event, import_event, transcribe_event, generate_event):
            event.success(lambda: ("", ""), None, [share_link, save_status], queue=False,
                          show_progress="hidden", api_name=False)
        for event in (load_event, slakh_event, kraisler_event, jazz_event):
            event.success(suggest_work_title, [work_title, state, automatic_title], [work_title, automatic_title], queue=False,
                          show_progress="hidden", api_name=False)
        output_audio.change(None, None, None, queue=False,
                            js="() => { window.dispatchEvent(new Event('spansynth-generated')); }")
        save_button.click(publish_work, [work_title, work_description, listed, state], [share_link, gallery, save_status, work_title],
                          api_name="save_work", concurrency_id="editing").then(
            publishing_status, None, [auth_status, sign_in_button], queue=False, show_progress="hidden", api_name=False)
        demo.load(publishing_status, None, [auth_status, sign_in_button], queue=False,
                  show_progress="hidden", api_name=False)
        refresh_auth.click(publishing_status, None, [auth_status, sign_in_button], queue=False,
                           show_progress="hidden", api_name=False)
        shared_outputs = [*clip_outputs[:-1], method, steps, cfg, context_midi, drop_context_audio,
                          work_title, work_description, share_link, generation_time, auto_region,
                          audio, crop_start, duration]
        demo.load(initial_work_title, None, [work_title, automatic_title], queue=False,
                  show_progress="hidden", api_name=False).then(
            open_shared_work, [shared_id, state], shared_outputs, api_name="open_shared_work", concurrency_id="editing",
            js="(id, state) => [new URLSearchParams(location.search).get('work') || '', state]").success(
            lambda title: title, work_title, automatic_title, queue=False, show_progress="hidden", api_name=False)
        gallery_tab.select(gallery_html, None, gallery, api_name="gallery")
        refresh_gallery.click(gallery_html, None, gallery, api_name="refresh_gallery")
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
    theme = gr.themes.Soft(primary_hue="violet", neutral_hue="slate").set(
        body_background_fill="#fbf8f7", body_background_fill_dark="#15151f",
        block_background_fill="#ffffff", block_background_fill_dark="#20212e",
        block_border_color="#e7dfe8", block_border_color_dark="#3a3749",
        block_label_background_fill="#f3eef5", block_label_background_fill_dark="#323041",
        block_title_background_fill="transparent", block_title_background_fill_dark="transparent",
        button_primary_background_fill="linear-gradient(115deg, #73599c, #8c5775)",
        button_primary_background_fill_dark="linear-gradient(115deg, #73599c, #8c5775)",
        button_primary_background_fill_hover="linear-gradient(115deg, #8267ab, #9b6584)",
        button_primary_background_fill_hover_dark="linear-gradient(115deg, #8267ab, #9b6584)")
    build_app().queue(max_size=16).launch(css=(HERE / "style.css").read_text(), theme=theme,
                                         max_file_size="50mb", show_error=True)
