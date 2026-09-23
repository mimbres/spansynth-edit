"""Export V8 weights and frozen SQ assets without training or dataset records."""
from __future__ import annotations
import argparse
from dataclasses import fields
import json
from pathlib import Path
import shutil
from safetensors import safe_open
from spansynth.model import DiTConfig, EventSetConfig
from spansynth.checkpoint import MODEL_FOLDER, CODEC_FOLDER


def export(checkpoint: Path, codec: Path, output: Path):
    metadata = json.loads((checkpoint / "metadata.json").read_text())
    condition = metadata["extra"]["conditioning"]
    if metadata["step"] != 127750 or condition["profile"]["midi_encoder_mode"] != "framewise_event_set_768_clean_reference":
        raise ValueError("Expected the V8 checkpoint at step 127750")
    raw = condition["model_config"]
    dit = {field.name: raw["dit"]["mel_bins" if field.name == "latent_dim" else field.name] for field in fields(DiTConfig)}
    event_set = {field.name: raw["event_set"][field.name] for field in fields(EventSetConfig)}
    DiTConfig(**dit)
    EventSetConfig(**event_set)
    model_dir, codec_dir = output / MODEL_FOLDER, output / CODEC_FOLDER
    if model_dir.exists() or codec_dir.exists():
        raise FileExistsError("Release directories already exist; inspect them before replacing a release")
    weights = checkpoint / "model.safetensors"
    with safe_open(weights, framework="pt") as handle:
        if any(key.startswith("hidden_alignment_projection.") for key in handle.keys()):
            raise ValueError("Checkpoint contains a training projection; export only inference tensors")
    for name in ("scalar_model_config.json", "scalar_model.safetensors"):
        if not (codec / name).is_file():
            raise FileNotFoundError(f"Missing codec file: {name}")
    config = {"format_version": 1, "architecture": "spansynth_v8", "step": 127750,
              "sample_rate": 48000, "frames": 512, "hop_length": 1920, "dit": dit,
              "event_set": event_set, "defaults": {"steps": 16, "cfg": 2.0, "method": "ordinary",
              "context_midi": False, "drop_context_audio": False}}
    model_dir.mkdir(parents=True)
    codec_dir.mkdir()
    (model_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    shutil.copyfile(weights, model_dir / "model.safetensors")
    for name in ("scalar_model_config.json", "scalar_model.safetensors"):
        shutil.copyfile(codec / name, codec_dir / name)
    root = Path(__file__).resolve().parents[1]
    for name in ("LICENSE", "NOTICE", "README.md"):
        shutil.copyfile(root / name, output / name)
    return output


def export_diffusers(checkpoint: Path | None, codec: Path | None, output: Path):
    """Convert public inference weights to standard Diffusers component folders."""
    import torch
    from spansynth.checkpoint import load_model, resolve_assets
    from spansynth.codec import load_scalar_model
    from spansynth.diffusers_models import HeartCodecModel, SpanSynthTransformerModel
    from spansynth.diffusers_pipeline import SpanSynthEditPipeline

    if output.exists():
        raise FileExistsError("Output already exists; choose a new export directory")
    model_assets, codec_assets = resolve_assets(checkpoint, codec)
    original, config = load_model(*model_assets)
    with torch.device("meta"):
        transformer = SpanSynthTransformerModel(config["dit"], config["event_set"])
    transformer.load_state_dict(original.state_dict(), strict=True, assign=True)
    transformer.eval().requires_grad_(False)
    scalar = load_scalar_model(codec_assets[1], codec_assets[0], device="cpu", dtype=torch.float32)
    with torch.device("meta"):
        heartcodec = HeartCodecModel(scalar.config.to_dict())
    heartcodec.model = scalar
    heartcodec.eval().requires_grad_(False)
    pipeline = SpanSynthEditPipeline(transformer, heartcodec)
    pipeline.save_pretrained(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--codec-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diffusers", action="store_true", help="Convert public weights, downloading them if local paths are omitted")
    args = parser.parse_args()
    if not args.diffusers and (args.checkpoint is None or args.codec_dir is None):
        parser.error("--checkpoint and --codec-dir are required for the original release export")
    exporter = export_diffusers if args.diffusers else export
    print(exporter(args.checkpoint, args.codec_dir, args.output))


if __name__ == "__main__":
    main()
