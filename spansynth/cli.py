"""Public command-line interface for SpanSynth-Edit."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import time


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("must be finite")
    return result


def positive_int(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def asset_options(parser):
    parser.add_argument("--checkpoint", type=Path, help="Local folder containing config.json and model.safetensors")
    parser.add_argument("--codec-dir", type=Path, help="Local Base SQ folder; otherwise download the bundled release")
    parser.add_argument("--cache-dir", type=Path, help="Hugging Face cache location (or set HF_HOME)")
    parser.add_argument("--revision", default="main", help="Hugging Face branch or release tag")
    parser.add_argument("--offline", action="store_true", help="Use only local files or the existing cache")


def build_parser():
    parser = argparse.ArgumentParser(description="SpanSynth-Edit: MIDI-guided synthesis and music editing.")
    parser.add_argument("--version", action="version", version="spansynth-edit 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="Download V8 step 127750 and its Base SQ codec")
    asset_options(download)
    for command, description in (("synthesize", "Synthesize a MIDI interval using audio context"),
                                  ("edit", "Replace an audio interval using edited MIDI")):
        sub = commands.add_parser(command, help=description, description=description)
        sub.add_argument("--audio", required=True, type=Path, help="Source or reference audio, decoded and mixed to mono at 48 kHz")
        sub.add_argument("--midi", required=True, type=Path, help="Target MIDI on the source timeline unless an offset is supplied")
        sub.add_argument("--source-midi", type=Path, help="Before-edit MIDI; required for FlowEdit or context MIDI")
        sub.add_argument("--crop-start", type=finite, default=0.0, help="Crop origin on the input audio timeline, in seconds")
        sub.add_argument("--duration", type=finite, default=20.48, help="Output crop duration, at most 20.48 seconds")
        sub.add_argument("--edit-start", type=finite, default=6.4, help="Generated interval start, relative to the crop")
        sub.add_argument("--edit-end", type=finite, default=14.08, help="Generated interval end, relative to the crop")
        sub.add_argument("--midi-offset", type=finite, default=0.0,
                         help="Add this many seconds to target MIDI times to align with input audio")
        sub.add_argument("--source-midi-offset", type=finite, default=0.0,
                         help="Add this many seconds to source MIDI times to align with input audio")
        sub.add_argument("--program", type=int, help="Override every target MIDI note's program (0-127, 128=drums)")
        sub.add_argument("--source-program", type=int, help="Override every source MIDI note's program")
        sub.add_argument("--steps", type=positive_int, default=16, help="Euler steps (default: 16)")
        sub.add_argument("--cfg", type=finite, default=2.0, help="MIDI classifier-free guidance scale (default: 2.0)")
        sub.add_argument("--context-midi", action="store_true", help="Use source MIDI outside the interval; dropped by default")
        sub.add_argument("--drop-context-audio", action="store_true", help="Zero the static clean-audio condition; kept by default")
        if command == "edit":
            sub.add_argument("--method", choices=("ordinary", "flowedit"), default="ordinary",
                             help="ordinary: spansynth-edit (default); flowedit: spansynth-edit + flowedit")
            sub.add_argument("--start-step", type=int, default=0, help="FlowEdit starting step (default: 0)")
            sub.add_argument("--average", type=positive_int, default=1, help="FlowEdit noise samples per step")
        else:
            sub.set_defaults(method="ordinary", start_step=0, average=1)
        sub.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:N (CUDA recommended)")
        sub.add_argument("--attention", choices=("auto", "flash", "math"), default="auto")
        sub.add_argument("--threads", type=positive_int, default=8, help="CPU thread count")
        sub.add_argument("--output", type=Path, required=True, help="Directory for output.wav, generated.wav, input.wav, and run.json")
        sub.add_argument("--save-codes", action="store_true", help="Also save SQ codes with causal history")
        sub.add_argument("--overwrite", action="store_true", help="Replace existing result files in the output directory")
        sub.add_argument("--check-inputs", action="store_true", help="Validate audio, MIDI, timing and outputs without loading models")
        asset_options(sub)
    return parser


def validate_args(args):
    if not 0 <= args.crop_start or not 0 < args.duration <= 20.48:
        raise ValueError("crop start must be non-negative and duration must lie in (0,20.48]")
    if not 0 <= args.edit_start < args.edit_end <= args.duration:
        raise ValueError("Require 0 <= edit-start < edit-end <= duration")
    if args.cfg < 0:
        raise ValueError("CFG must be non-negative")
    if args.context_midi and args.source_midi is None:
        raise ValueError("--context-midi requires --source-midi")
    if args.method == "flowedit" and args.source_midi is None:
        raise ValueError("FlowEdit requires --source-midi containing the before-edit notes")
    if not 0 <= args.start_step < args.steps:
        raise ValueError("--start-step must lie in [0, steps)")
    if args.method == "ordinary" and (args.start_step != 0 or args.average != 1):
        raise ValueError("--start-step and --average require --method flowedit")
    for name in ("program", "source_program"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 128:
            raise ValueError(f"--{name.replace('_', '-')} must lie in [0,128]")
    for name in ("audio", "midi", "source_midi"):
        value = getattr(args, name)
        if value is not None and not value.is_file():
            raise FileNotFoundError(f"Input file does not exist: {value}")
    paths = [args.output / name for name in ("output.wav", "generated.wav", "input.wav", "run.json", "codes.safetensors")]
    inputs = {path.resolve() for path in (args.audio, args.midi, args.source_midi) if path is not None}
    if any(path.resolve() in inputs for path in paths):
        raise ValueError("The output directory would replace an input file")
    if args.output.exists() and not args.output.is_dir():
        raise ValueError("--output must name a directory")
    if not args.overwrite and any(path.exists() for path in paths):
        raise FileExistsError("Result files already exist; choose another output directory or use --overwrite")
    return paths


def run(args):
    paths = validate_args(args)
    import numpy as np
    import torch
    import soundfile as sf
    from .audio import read_audio, encode_audio, decode_audio, assemble_output, SAMPLE_RATE, HOP_LENGTH, HISTORY_FRAMES
    from .midi import read_notes, prepare_midi
    from .checkpoint import resolve_assets, load_model, REPO_ID, MODEL_FOLDER
    from .codec import load_scalar_model
    from .sampling import sample

    torch.set_num_threads(args.threads)
    crop = read_audio(args.audio, args.crop_start, args.duration)
    # Round outwards on the 25 Hz latent grid and expose the effective boundaries.
    first = math.floor(args.edit_start * 25 + 1e-9)
    last = math.ceil(args.edit_end * 25 - 1e-9)
    first_sample = first * HOP_LENGTH
    last_sample = min(last * HOP_LENGTH, len(crop.original))
    if first_sample >= last_sample:
        raise ValueError("The selected interval contains no output samples")
    generated = torch.zeros(512, dtype=torch.bool)
    generated[first:last] = True
    target = read_notes(args.midi, crop_start=args.crop_start, offset=args.midi_offset, program=args.program)
    need_source = args.context_midi or args.method == "flowedit"
    source = read_notes(args.source_midi if need_source else None, crop_start=args.crop_start,
                        offset=args.source_midi_offset, program=args.source_program)
    target_rows = prepare_midi(target, source, generated, context_midi=args.context_midi)
    source_rows = prepare_midi(source, source, generated, context_midi=args.context_midi) if args.method == "flowedit" else None
    details = {"command": args.command, "method": args.method, "steps": args.steps, "cfg": args.cfg,
               "context_midi": args.context_midi, "drop_context_audio": args.drop_context_audio,
               "start_step": args.start_step, "average": args.average,
               "audio": str(args.audio.resolve()), "midi": str(args.midi.resolve()),
               "source_midi": str(args.source_midi.resolve()) if args.source_midi else None,
               "midi_offset_seconds": args.midi_offset, "source_midi_offset_seconds": args.source_midi_offset,
               "program": args.program, "source_program": args.source_program,
               "crop_start_seconds": args.crop_start, "duration_seconds": len(crop.original) / SAMPLE_RATE,
               "requested_interval_seconds": [args.edit_start, args.edit_end],
               "applied_interval_seconds": [first_sample / SAMPLE_RATE, last_sample / SAMPLE_RATE],
               "latent_interval_frames": [first, last], "input_sample_rate": crop.input_rate,
               "input_channels": crop.input_channels, "sample_rate": SAMPLE_RATE,
               "padded_seconds": crop.padded_samples / SAMPLE_RATE, "encoder_gain": crop.gain,
               "target_notes_in_crop": len(target), "source_notes_in_crop": len(source)}
    if args.check_inputs:
        print(json.dumps(details, indent=2))
        return 0
    device_name = "cuda" if args.device == "auto" and torch.cuda.is_available() else "cpu" if args.device == "auto" else args.device
    device = torch.device(device_name)
    if device.type not in ("cpu", "cuda"):
        raise ValueError("Supported devices are cpu, cuda, or cuda:N")
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is not available in this PyTorch installation")
    if device.type == "cuda":
        with torch.cuda.device(device):
            if not torch.cuda.is_bf16_supported():
                raise ValueError("CUDA inference requires a GPU supporting bfloat16")
    if args.attention == "flash" and device.type != "cuda":
        raise ValueError("Flash attention requires CUDA")
    print(f"{args.method}: {args.steps} steps, CFG {args.cfg:g}, interval {first_sample / SAMPLE_RATE:.3f}-{last_sample / SAMPLE_RATE:.3f}s", flush=True)
    if crop.padded_samples:
        print(f"Audio ends inside the crop; padding {crop.padded_samples / SAMPLE_RATE:.3f}s with silence.", flush=True)
    assets, codec_assets = resolve_assets(args.checkpoint, args.codec_dir, cache_dir=args.cache_dir,
                                         offline=args.offline, revision=args.revision)
    started = time.perf_counter()
    model, config = load_model(*assets, device=device, backend=args.attention)
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    codec = load_scalar_model(codec_assets[1], codec_assets[0], device=device, dtype=dtype)
    encoded = encode_audio(codec, crop, device)
    history, codes = encoded[:, :HISTORY_FRAMES], encoded[:, HISTORY_FRAMES:]
    target_rows = {key: value.to(device) for key, value in target_rows.items()}
    if source_rows is not None:
        source_rows = {key: value.to(device) for key, value in source_rows.items()}
    def progress(step, total):
        print(f"Step {step}/{total}", flush=True)
    with torch.inference_mode(), torch.autocast(device_type=device.type, dtype=dtype, enabled=device.type == "cuda"):
        result_codes = sample(model, codes, target_rows, (~generated)[None].to(device),
                              source_rows=source_rows, method=args.method, steps=args.steps, cfg=args.cfg,
                              context_midi=args.context_midi, drop_context_audio=args.drop_context_audio,
                              start_step=args.start_step, average=args.average, progress=progress)
    # Free model memory before decoding on GPUs with limited capacity.
    del model
    decoded = decode_audio(codec, result_codes, history, device)
    result = assemble_output(crop.original, decoded, crop.gain, first_sample, last_sample)
    peak = float(np.abs(result).max())
    details.update({"checkpoint": str(args.checkpoint.resolve()) if args.checkpoint else f"{REPO_ID}/{MODEL_FOLDER}",
                    "revision": args.revision, "checkpoint_step": config["step"], "device": str(device),
                    "attention": args.attention, "elapsed_seconds": time.perf_counter() - started,
                    "output_peak": peak, "output_format": "48 kHz mono float32 WAV"})
    args.output.mkdir(parents=True, exist_ok=True)
    sf.write(paths[0], result, SAMPLE_RATE, subtype="FLOAT")
    sf.write(paths[1], result[first_sample:last_sample], SAMPLE_RATE, subtype="FLOAT")
    sf.write(paths[2], crop.original, SAMPLE_RATE, subtype="FLOAT")
    if args.save_codes:
        from safetensors.torch import save_file
        save_file({"codes": torch.cat((history, result_codes), dim=1).cpu().contiguous()}, str(paths[4]))
    elif args.overwrite and paths[4].exists():
        paths[4].unlink()
    paths[3].write_text(json.dumps(details, indent=2) + "\n")
    if peak > 1:
        print(f"Output peak {peak:.3f} exceeds 1; float WAV preserves it. Reduce playback gain if needed.")
    print(f"Saved {paths[0]} ({details['elapsed_seconds']:.1f}s)")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            from .checkpoint import resolve_assets
            model, codec = resolve_assets(args.checkpoint, args.codec_dir, cache_dir=args.cache_dir,
                                          offline=args.offline, revision=args.revision)
            for path in (*model, *codec):
                print(path)
            return 0
        return run(args)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"spansynth-edit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
