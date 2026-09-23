"""ComfyUI nodes for MIDI-guided synthesis and editing."""

import base64
from collections import Counter
import io as binary_io
import json
import math
from pathlib import Path
import re
import tempfile
import time

import numpy as np
import soundfile as sf
import torch
from mido import Message, MetaMessage, MidiFile, MidiTrack
from comfy_api.latest import ComfyExtension, io
import comfy.model_management as memory
from comfy.model_patcher import ModelPatcher
from comfy.utils import ProgressBar
import folder_paths

from .audio import (
    FRAMES, HISTORY_FRAMES, HOP_LENGTH, SAMPLE_RATE,
    assemble_output, decode_audio, encode_audio, prepare_audio,
)
from .checkpoint import load_model, resolve_assets
from .codec import load_scalar_model
from .midi import NOTE_DTYPE, prepare_midi, read_notes
from .vocabulary import PROGRAM_GROUPS, PROGRAM_TO_CATEGORY


Models = io.Custom("SPANSYNTH_MODELS")
Clip = io.Custom("SPANSYNTH_CLIP")
Midi = io.Custom("SPANSYNTH_MIDI")


def midi_path(name):
    root = Path(folder_paths.get_input_directory()).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in (".mid", ".midi") or not path.is_file():
        raise ValueError("Choose a .mid or .midi file inside the ComfyUI input folder.")
    return path


def score_notes(score, crop_start):
    if score is None:
        return read_notes(None, crop_start=crop_start)
    if "notes" in score:
        offset = score["origin"] - crop_start
        return np.array([(round((note.get("source_onset", note["start"]) + offset) * SAMPLE_RATE),
                          round((note["start"] + note["duration"] + offset) * SAMPLE_RATE),
                          note["pitch"], note["velocity"], note["program"], i)
                         for i, note in enumerate(score["notes"])], dtype=NOTE_DTYPE)
    return read_notes(binary_io.BytesIO(score["data"]), crop_start=crop_start, offset=score["origin"])


def audio_output(waveform):
    return {"waveform": torch.from_numpy(waveform.copy())[None, None], "sample_rate": SAMPLE_RATE}


def preview_audio(waveform):
    # ComfyUI owns these previews and clears its temporary directory on restart.
    with tempfile.NamedTemporaryFile(prefix="spansynth-", suffix=".wav", dir=folder_paths.get_temp_directory(), delete=False) as file:
        sf.write(file, waveform, SAMPLE_RATE, format="WAV", subtype="FLOAT")
    return {"filename": Path(file.name).name, "subfolder": "", "type": "temp"}


def editor_notes(midi, clip):
    duration = len(clip["crop"].original) / SAMPLE_RATE
    result = []
    for n in score_notes(midi, clip["start"]):
        onset, end = int(n["onset"]) / SAMPLE_RATE, int(n["offset"]) / SAMPLE_RATE
        start = max(0., onset)
        end = min(duration, max(end, start + .001))
        if start >= duration or end <= start:
            continue
        note = {"start": start, "duration": end - start, "pitch": int(n["pitch"]),
                "velocity": int(n["velocity"]), "program": int(n["program"])}
        if onset < 0 and n["program"] != 128:
            note["source_onset"] = onset
        result.append(note)
    return result


def validate_notes(notes, duration):
    if not isinstance(notes, list) or len(notes) > 10000:
        raise ValueError("Use a score with at most 10,000 notes.")
    result = []
    for note in notes:
        fields = {}
        for key, low, high in (("start", 0, duration), ("duration", .001, duration),
                               ("pitch", 0, 127), ("velocity", 1, 127), ("program", 0, 128)):
            value = note.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid note {key}.")
            if key in ("pitch", "velocity", "program") and not isinstance(value, int):
                raise ValueError(f"Note {key} must be an integer.")
            fields[key] = value
        if fields["start"] + fields["duration"] > duration + 1e-6:
            raise ValueError("A note extends beyond the clip.")
        if "source_onset" in note and fields["start"] == 0 and fields["program"] != 128:
            onset = float(note["source_onset"])
            if not math.isfinite(onset) or not -36000 <= onset <= 0:
                raise ValueError("Invalid sustained note start.")
            fields["source_onset"] = onset
        result.append(fields)
    if len({n["program"] for n in result} - {128}) > 15:
        raise ValueError("MIDI supports at most 15 melodic instruments plus drums. Merge a few tracks first.")
    return result


def midi_bytes(notes):
    # One tick per 48 kHz sample keeps imported transcription timing intact.
    midi = MidiFile(type=1, ticks_per_beat=24000)
    midi.tracks.append(MidiTrack([MetaMessage("set_tempo", tempo=500_000)]))
    programs = sorted({n["program"] for n in notes})
    channels = iter(c for c in range(16) if c != 9)
    for program in programs:
        channel = 9 if program == 128 else next(channels)
        track = MidiTrack()
        if program != 128:
            track.append(Message("program_change", program=program, channel=channel))
        events = []
        for n in notes:
            if n["program"] == program:
                start = round(n["start"] * SAMPLE_RATE)
                end = max(start + 1, round((n["start"] + n["duration"]) * SAMPLE_RATE))
                events.extend([(start, 1, n), (end, 0, n)])
        previous = 0
        for tick, on, n in sorted(events, key=lambda e: (e[0], e[1])):
            track.append(Message("note_on" if on else "note_off", note=n["pitch"],
                                 velocity=n["velocity"] if on else 0, channel=channel, time=tick - previous))
            previous = tick
        midi.tracks.append(track)
    output = binary_io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def changed_region(source, target, duration):
    def events(notes):
        return Counter((round(n["start"] * SAMPLE_RATE), round((n["start"] + n["duration"]) * SAMPLE_RATE),
                        n["pitch"], n["velocity"], n["program"], n.get("source_onset")) for n in notes)
    before, after = events(source), events(target)
    changes = list(before - after) + list(after - before)
    if not changes:
        return 0., duration
    return (min(n[0] for n in changes) // HOP_LENGTH / 25,
            min(duration, math.ceil(max(n[1] for n in changes) / HOP_LENGTH) / 25))


class SpanSynthLoadModel(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SpanSynthLoadModel", display_name="Load SpanSynth-Edit", category="SpanSynth-Edit",
            description="Load the released model and Base SQ codec. Reuses the local Hugging Face cache.",
            inputs=[
                io.Combo.Input("device", options=["auto", "cpu", "mps", "cuda"], default="auto"),
                io.String.Input("checkpoint", default="", advanced=True, tooltip="Optional local model directory."),
                io.String.Input("codec_directory", default="", advanced=True, tooltip="Optional local Base SQ directory."),
            ], outputs=[Models.Output("model")],
        )

    @classmethod
    def execute(cls, device="auto", checkpoint="", codec_directory=""):
        device = memory.get_torch_device() if device == "auto" else torch.device(device)
        if device.type not in ("cuda", "mps", "cpu"):
            raise ValueError("SpanSynth-Edit supports CUDA, Apple MPS, and CPU.")
        if device.type == "cuda":
            with torch.cuda.device(device):
                if not torch.cuda.is_bf16_supported():
                    raise ValueError("CUDA inference requires bfloat16 support. Choose CPU instead.")
        assets, codec_assets = resolve_assets(checkpoint or None, codec_directory or None)
        transformer, _ = load_model(*assets)
        dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
        codec = load_scalar_model(codec_assets[1], codec_assets[0], device="cpu", dtype=dtype)
        offload = torch.device("cpu")
        return io.NodeOutput((ModelPatcher(transformer, device, offload), ModelPatcher(codec, device, offload)))


class SpanSynthLoadMidi(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        root = Path(folder_paths.get_input_directory())
        files = sorted(str(path.relative_to(root)) for path in root.rglob("*")
                       if path.is_file() and path.suffix.lower() in (".mid", ".midi"))
        return io.Schema(
            node_id="SpanSynthLoadMidi", display_name="Load MIDI · SpanSynth-Edit", category="SpanSynth-Edit",
            description="Read MIDI from ComfyUI/input. Offset aligns MIDI time zero with the audio timeline.",
            inputs=[io.Combo.Input("midi", options=files or [""]),
                    io.Float.Input("offset", default=0, min=-36000, max=36000, step=0.01)],
            outputs=[Midi.Output("midi")],
        )

    @classmethod
    def execute(cls, midi, offset=0):
        if not math.isfinite(offset):
            raise ValueError("MIDI offset must be finite.")
        score = {"data": midi_path(midi).read_bytes(), "origin": float(offset)}
        score_notes(score, 0)
        return io.NodeOutput(score)

    @classmethod
    def fingerprint_inputs(cls, midi, offset=0):
        info = midi_path(midi).stat()
        return (info.st_mtime_ns, info.st_size)


class SpanSynthPrepareClip(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SpanSynthPrepareClip", display_name="Prepare Audio Clip · SpanSynth-Edit", category="SpanSynth-Edit",
            description="Choose the shared timeline. Leave audio disconnected for MIDI-to-audio synthesis.",
            inputs=[io.Audio.Input("audio", optional=True),
                    io.Float.Input("crop_start", default=0, min=0, max=36000, step=0.01),
                    io.Float.Input("duration", default=20.48, min=0.2, max=20.48, step=0.04)],
            outputs=[Clip.Output("clip"), io.Audio.Output("original_clip")],
        )

    @classmethod
    def execute(cls, audio=None, crop_start=0, duration=20.48):
        if not math.isfinite(duration) or not 0 < duration <= 20.48:
            raise ValueError("Clip duration must lie in (0, 20.48] seconds.")
        if audio is None:
            if crop_start != 0:
                raise ValueError("Set crop_start to zero when synthesizing without input audio.")
            crop = prepare_audio(np.zeros(round(duration * SAMPLE_RATE), dtype=np.float32), SAMPLE_RATE, 0, duration)
            return io.NodeOutput({"crop": crop, "start": 0, "preview": preview_audio(crop.original)}, audio_output(crop.original))
        waveform = audio["waveform"]
        if waveform.ndim != 3 or waveform.shape[0] != 1:
            raise ValueError("SpanSynth-Edit processes one audio clip at a time; use a batch size of one.")
        values = waveform[0].detach().float().cpu().transpose(0, 1).numpy()
        rate = audio["sample_rate"]
        remaining = len(values) / rate - crop_start
        if remaining <= 0:
            raise ValueError("Crop start is beyond the end of the audio.")
        duration = min(duration, remaining)
        crop = prepare_audio(values, rate, crop_start, duration)
        if not len(crop.original):
            raise ValueError("The selected clip contains no audio samples.")
        return io.NodeOutput({"crop": crop, "start": crop_start, "preview": preview_audio(crop.original)}, audio_output(crop.original))


class SpanSynthTranscribe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SpanSynthTranscribe", display_name="Transcribe with YourMT3+", category="SpanSynth-Edit",
            description="Uploads the selected clip to mimbres/YourMT3 on Hugging Face. Uses HF_TOKEN or your local HF login. Remote GPU quotas apply.",
            inputs=[Clip.Input("clip")], outputs=[Midi.Output("midi")],
        )

    @classmethod
    def execute(cls, clip):
        from gradio_client import Client, handle_file
        from huggingface_hub import get_token

        memory.throw_exception_if_processing_interrupted()
        client = Client("mimbres/YourMT3", token=get_token(), verbose=False, analytics_enabled=False)
        try:
            path = Path(folder_paths.get_temp_directory()) / clip["preview"]["filename"]
            result = client.predict(handle_file(str(path)), api_name="/process_audio")
        finally:
            client.close()
        memory.throw_exception_if_processing_interrupted()
        html = result[0] if isinstance(result, (tuple, list)) else result
        match = re.search(r"data:audio/(?:midi|mid);base64,([A-Za-z0-9+/=]+)", html) if isinstance(html, str) else None
        if not match or len(match[1]) > 8_000_000:
            raise ValueError("YourMT3+ returned no readable MIDI. Retry or connect Load MIDI instead.")
        payload = base64.b64decode(match[1], validate=True)
        MidiFile(file=binary_io.BytesIO(payload))
        return io.NodeOutput({"data": payload, "origin": clip["start"]})


class SpanSynthEditScore(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SpanSynthEditScore", display_name="Edit Score · SpanSynth-Edit", category="SpanSynth-Edit",
            description="Prepare score opens the piano roll without generating audio. Apply saves your edits in the workflow. Region outputs cover changed notes, or the whole clip if unchanged.",
            inputs=[Clip.Input("clip"), Midi.Input("source_midi", optional=True),
                    io.String.Input("score", default="", multiline=True)],
            outputs=[Midi.Output("midi"), io.Float.Output("region_start"), io.Float.Output("region_end")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, clip, source_midi=None, score=""):
        audio = clip["crop"].original
        duration = len(audio) / SAMPLE_RATE
        source = editor_notes(source_midi, clip)
        identity = f"{clip['start']:.6f}:{len(audio)}"
        saved = json.loads(score) if score else {}
        if saved and (saved.get("clip") != identity or saved.get("source") != source):
            raise ValueError("The crop or original MIDI changed. Use Reset score to load it, or restore the previous inputs to keep editing.")
        notes = validate_notes(saved.get("notes", source), duration)
        instruments = [{"program": programs[0], "name": name, "members": list(programs)}
                       for name, programs in PROGRAM_GROUPS.items() if name not in {"Banjo", "Sitar", "Fiddle"}]
        waveform = [[round(float(p.min()), 4), round(float(p.max()), 4)] for p in np.array_split(audio, min(1024, len(audio)))]
        value = {"clip": identity, "duration": duration, "source": source, "notes": notes,
                 "instruments": instruments, "waveform": waveform, "view": saved.get("view", {})}
        data = midi_bytes(notes)
        first, last = changed_region(source, notes, duration)
        return io.NodeOutput({"data": data, "origin": clip["start"], "notes": notes}, first, last,
                             ui={"score": [value], "original_audio": [clip["preview"]],
                                 "midi": [base64.b64encode(data).decode()], "region": [[first, last]]})


class SpanSynthGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SpanSynthGenerate", display_name="Generate · SpanSynth-Edit", category="SpanSynth-Edit",
            description="Generate fresh audio on each Run. Always uses the original clip as context.",
            inputs=[Models.Input("model"), Clip.Input("clip"), Midi.Input("midi"),
                    io.Combo.Input("method", options=["spansynth-edit", "spansynth-edit + flowedit"], default="spansynth-edit"),
                    io.Float.Input("region_start", default=6.4, min=0, max=20.48, step=0.04),
                    io.Float.Input("region_end", default=14.08, min=0, max=20.48, step=0.04),
                    io.Int.Input("steps", default=16, min=1, max=1024),
                    io.Float.Input("cfg", default=2, min=0, max=100, step=0.1),
                    Midi.Input("source_midi", optional=True),
                    io.Boolean.Input("context_midi", default=False, advanced=True),
                    io.Boolean.Input("drop_context_audio", default=False, advanced=True)],
            outputs=[io.Audio.Output("edited_clip"), io.Audio.Output("generated_region")],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # ComfyUI must execute stochastic generation again for every queued Run.
        return float("nan")

    @classmethod
    @torch.inference_mode()
    def execute(cls, model, clip, midi, method="spansynth-edit", region_start=6.4, region_end=14.08,
                steps=16, cfg=2, source_midi=None, context_midi=False, drop_context_audio=False):
        methods = {"spansynth-edit": "ordinary", "spansynth-edit + flowedit": "flowedit"}
        if method not in methods:
            raise ValueError("Choose spansynth-edit or spansynth-edit + flowedit.")
        method = methods[method]
        crop = clip["crop"]
        duration = len(crop.original) / SAMPLE_RATE
        if not all(math.isfinite(value) for value in (region_start, region_end, cfg)):
            raise ValueError("Region boundaries and CFG must be finite.")
        if not 0 <= region_start < region_end <= duration:
            raise ValueError(f"Choose a region inside the {duration:.2f}-second clip.")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1 or cfg < 0:
            raise ValueError("Steps must be a positive integer and CFG must be nonnegative.")
        if (method == "flowedit" or context_midi) and source_midi is None:
            raise ValueError("Connect the original MIDI for FlowEdit or context MIDI conditioning.")
        first = math.floor(region_start * SAMPLE_RATE / HOP_LENGTH + 1e-9)
        last = math.ceil(region_end * SAMPLE_RATE / HOP_LENGTH - 1e-9)
        first_sample, last_sample = first * HOP_LENGTH, min(last * HOP_LENGTH, len(crop.original))
        if first_sample >= last_sample:
            raise ValueError("The selected region contains no output samples.")
        generated = torch.zeros(FRAMES, dtype=torch.bool)
        generated[first:last] = True
        target = score_notes(midi, clip["start"])
        unsupported = set(int(n["program"]) for n in target) - PROGRAM_TO_CATEGORY.keys()
        if unsupported:
            raise ValueError(f"Choose supported instruments in the editor for MIDI programs {sorted(unsupported)}.")
        source = score_notes(source_midi, clip["start"])
        target_rows = prepare_midi(target, source, generated, context_midi=context_midi)
        source_rows = prepare_midi(source, source, generated, context_midi=context_midi) if method == "flowedit" else None
        transformer, codec = model
        device = transformer.load_device
        memory.throw_exception_if_processing_interrupted()
        memory.load_models_gpu([transformer, codec], memory_required=2 * 1024**3, force_full_load=True)
        started = time.perf_counter()
        encoded = encode_audio(codec.model, crop, device)
        history, codes = encoded[:, :HISTORY_FRAMES], encoded[:, HISTORY_FRAMES:]
        target_rows = {key: value.to(device) for key, value in target_rows.items()}
        if source_rows is not None:
            source_rows = {key: value.to(device) for key, value in source_rows.items()}
        bar = ProgressBar(steps)

        def progress(step, total):
            memory.throw_exception_if_processing_interrupted()
            bar.update_absolute(step, total)

        from .sampling import sample

        with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
            codes = sample(transformer.model, codes, target_rows, (~generated)[None].to(device),
                           source_rows=source_rows, method=method, steps=steps, cfg=cfg,
                           context_midi=context_midi, drop_context_audio=drop_context_audio, progress=progress)
        memory.throw_exception_if_processing_interrupted()
        decoded = decode_audio(codec.model, codes, history, device)
        output = assemble_output(crop.original, decoded, crop.gain, first_sample, last_sample)
        return io.NodeOutput(audio_output(output), audio_output(output[first_sample:last_sample]),
                             ui={"generated_audio": [preview_audio(output)],
                                 "region": [[first_sample / SAMPLE_RATE, last_sample / SAMPLE_RATE]],
                                 "text": [f"Generated in {time.perf_counter() - started:.1f} s · "
                                          f"region {first_sample / SAMPLE_RATE:.2f}–{last_sample / SAMPLE_RATE:.2f} s"]})


def register_routes():
    from aiohttp import ClientSession, ClientTimeout, web
    from server import PromptServer

    root = Path(__file__).resolve().parents[1]
    assets = {"editor.js": root / "app/editor.js", "style.css": root / "app/style.css",
              "host.js": root / "comfyui/editor.js"}

    @PromptServer.instance.routes.get("/spansynth/assets/{name}")
    async def asset(request):
        path = assets.get(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    @PromptServer.instance.routes.get("/spansynth/sounds/{name}")
    async def sound(request):
        name = request.match_info["name"]
        fixed = {
            "smplr.mjs": "https://cdn.jsdelivr.net/npm/smplr@1.0.0/dist/index.mjs",
            "drums.js": "https://cdn.jsdelivr.net/gh/henrikvilhelmberglund/midi-js-compat-soundfonts@gh-pages/GM-soundfonts/FluidR3_GM/drumkits/Standard-mp3.js",
            "names.json": "https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM/names.json",
        }
        url = fixed.get(name)
        if url is None and re.fullmatch(r"[a-z0-9_]+-mp3\.js", name):
            url = "https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM/" + name
        if url is None:
            raise web.HTTPNotFound()
        async with ClientSession(timeout=ClientTimeout(total=30)) as client:
            async with client.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise web.HTTPBadGateway(text="GM preview sound is unavailable. Try again later.")
                data = await response.read()
        return web.Response(body=data, content_type="application/json" if name.endswith(".json") else "application/javascript",
                            headers={"Cache-Control": "public, max-age=86400"})

    @PromptServer.instance.routes.get("/spansynth/editor")
    async def editor(request):
        html = (root / "app/editor.html").read_text()
        return web.Response(content_type="text/html", text='''<!doctype html>
<html data-spansynth-editor><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SpanSynth-Edit · Piano roll</title><link rel="stylesheet" href="/spansynth/assets/style.css">
<style>body{margin:0;padding:16px;background:#f8f4f8;font:14px system-ui}body.dark{background:#211e29}
.gradio-container{max-width:none}header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px;color:var(--ss-text)}
header button,header a{padding:9px 14px;border:1px solid var(--ss-border);border-radius:8px;background:var(--ss-surface);color:var(--ss-text);cursor:pointer;text-decoration:none;font:inherit}
header strong{margin-right:auto}#apply{background:#815c87;color:white}#status,#generation-status{font-size:12px;color:var(--ss-muted)}
.roll-scroll{height:max(260px,calc(100vh - 430px))}[hidden]{display:none!important}</style></head>
<body><div class="gradio-container"><header><strong>SpanSynth-Edit · Piano roll</strong>
<span id="status">Loading score…</span><a id="midi-download" download="edited.mid" hidden>Download MIDI</a>
<a id="audio-download" download="edited.wav" hidden>Download audio</a><button id="theme">◐ Theme</button>
<button id="apply">Apply edits</button><button id="close">Close</button></header>
<p id="generation-status" role="status"></p><main id="editor">''' + html + '''</main></div><script type="module" src="/spansynth/assets/host.js"></script></body></html>''')


class SpanSynthExtension(ComfyExtension):
    async def on_load(self):
        register_routes()

    async def get_node_list(self):
        return [SpanSynthLoadModel, SpanSynthLoadMidi, SpanSynthPrepareClip,
                SpanSynthTranscribe, SpanSynthEditScore, SpanSynthGenerate]
