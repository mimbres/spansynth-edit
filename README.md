---
license: apache-2.0
tags:
  - audio
  - music
  - midi
  - midi-to-audio
  - audio-to-audio
  - flow-matching
---

# SpanSynth-Edit

SpanSynth-Edit synthesises and edits multi-instrument audio mixtures from MIDI, using surrounding audio for timbre guidance. To edit a recording, add, remove, or modify notes in its MIDI, then resynthesise the selected region.

[Project page and audio demos](https://mimbres.github.io/spansynth-edit/) · [Source code](https://github.com/mimbres/spansynth-edit) · [Model weights](https://huggingface.co/mimbres/spansynth-edit)

![SpanSynth-Edit model overview](https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/FigureDraft-fig1-retro-04.svg)

## Install

Install Python 3.11–3.13 and [PyTorch for your GPU](https://pytorch.org/get-started/locally/) first. A CUDA GPU with bfloat16 support is recommended. CPU inference is also supported, but Apple GPUs are not.

Install the CLI without downloading the demo audio:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/mimbres/spansynth-edit.git
cd spansynth-edit
git sparse-checkout set spansynth
python -m pip install .
```

Model and codec weights download automatically on first use without a token. Run `spansynth-edit download` to download them in advance. Use `--cache-dir` to choose a cache folder or `--offline` to use cached weights.

## Try an example

Choose `spansynth-edit` (default) or `spansynth-edit + flowedit`. Both default to **16 Euler steps and CFG 2**, with audio context enabled and context MIDI omitted.

From the repository directory, download the sample audio and its original and revised MIDI files (about 0.5 MB total):

```bash
mkdir -p ../spansynth-inputs
for name in early-slakh-track00006-original.mp3 \
            early-slakh-track00006-before.mid \
            early-slakh-track00006-after.mid; do
  curl --fail --location --output "../spansynth-inputs/$name" \
    "https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/$name"
done
```

`spansynth-edit` resynthesises the selected region from the revised MIDI:

```bash
spansynth-edit edit \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --midi ../spansynth-inputs/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-edit
```

For `spansynth-edit + flowedit`, provide both the original and revised MIDI:

```bash
spansynth-edit edit --method flowedit \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --source-midi ../spansynth-inputs/early-slakh-track00006-before.mid \
  --midi ../spansynth-inputs/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-flowedit
```

To synthesise a region from a score with `spansynth-edit`, use `synthesize`. The recording supplies the surrounding audio context:

```bash
spansynth-edit synthesize \
  --audio recording.wav --midi score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --output ../spansynth-results/synthesis
```

Add `--check-inputs` to validate audio, MIDI, and timing without loading the model. To include contextual MIDI, add `--context-midi --source-midi original.mid`. See `spansynth-edit edit --help` for all options.

## Timing and instruments

All times are in **seconds**. Provide aligned audio and MIDI, or use the offsets below to align their timelines.

| Option | Meaning |
| --- | --- |
| `--crop-start` | Crop start on the audio timeline (default 0) |
| `--duration` | Crop length (default and maximum 20.48) |
| `--edit-start`, `--edit-end` | Generated region relative to the crop (default 6.40–14.08) |
| `--midi-offset` | Offset added to target MIDI times to obtain audio times |
| `--source-midi-offset` | Offset added to original MIDI times to obtain audio times |

With `--crop-start 30`, a MIDI note at 36.4 s appears at 6.4 s in the crop. If MIDI time 0 corresponds to the start of that crop, use `--midi-offset 30`. Set `--source-midi-offset` independently for the original MIDI. Each run processes one crop, with edit boundaries rounded outward to 40 ms.

The [instrument vocabulary](https://github.com/mimbres/spansynth-edit/blob/main/spansynth/vocabulary.py) lists supported MIDI programs and merged instrument groups. Programs in a group share one model category: for example, 0, 1, 3, 6, and 7 map to **Acoustic Piano**. Program numbers are zero-based, and MIDI channel 10 selects drums (internal program 128).

## Results

Each output folder contains:

- `output.wav`: the full crop with the synthesised or edited region.
- `generated.wav`: the generated region only.
- `input.wav`: the source crop converted to 48 kHz mono.
- `run.json`: settings and timing.

All audio outputs are 48 kHz mono. Outside the generated region, `output.wav` matches `input.wav` exactly. Use `--overwrite` to replace existing results.

## License and credits

Code and model weights are released under Apache-2.0. See [LICENSE](https://github.com/mimbres/spansynth-edit/blob/main/LICENSE) and [NOTICE](https://github.com/mimbres/spansynth-edit/blob/main/NOTICE) for terms and credits, including YourMT3 and [HeartCodec](https://github.com/HeartMuLa/heartlib). Demo recordings retain their original rights.

## Citation

If you use SpanSynth-Edit in your research, please cite our forthcoming paper. **arXiv link and BibTeX: coming soon.**
