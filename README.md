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

SpanSynth-Edit synthesises and edits multi-instrument music from MIDI, using surrounding audio to guide instrument timbre. To edit a recording, revise the MIDI to add, remove, or modify notes, and the model resynthesises the selected region.

[Project page and audio demos](https://mimbres.github.io/spansynth-edit/) · [Source code](https://github.com/mimbres/spansynth-edit) · [Model weights](https://huggingface.co/mimbres/spansynth-edit)

![SpanSynth-Edit model overview](https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/FigureDraft-fig1-retro-04.svg)

## Install

Use Python 3.11–3.13 and install [PyTorch for your GPU](https://pytorch.org/get-started/locally/) first. A bfloat16-capable CUDA GPU is recommended; CPU inference is slow, and Apple GPU execution is unsupported.

This sparse clone skips demo audio:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/mimbres/spansynth-edit.git
cd spansynth-edit
git sparse-checkout set spansynth
python -m pip install .
```

Weights and the codec download automatically on first use, without a token. Use `spansynth-edit download` to prefetch them, `--cache-dir` to choose a cache, or `--offline` to use cached files only.

## Try an example

The CLI defaults to **16 Euler steps, CFG 2, ordinary generation, context MIDI dropout on, and context audio dropout off**. FlowEdit is opt-in.

From the clone, download one audio file and its before/after MIDI (about 0.5 MB):

```bash
mkdir -p ../spansynth-inputs
for name in early-slakh-track00006-original.mp3 \
            early-slakh-track00006-before.mid \
            early-slakh-track00006-after.mid; do
  curl --fail --location --output "../spansynth-inputs/$name" \
    "https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/$name"
done
```

Ordinary editing needs audio and edited MIDI:

```bash
spansynth-edit edit \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --midi ../spansynth-inputs/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-edit
```

FlowEdit also needs the before-edit MIDI:

```bash
spansynth-edit edit --method flowedit \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --source-midi ../spansynth-inputs/early-slakh-track00006-before.mid \
  --midi ../spansynth-inputs/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-flowedit
```

For synthesis, use `synthesize` with your recording and score. Both ordinary commands generate from MIDI using surrounding audio as context:

```bash
spansynth-edit synthesize \
  --audio recording.wav --midi score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --output ../spansynth-results/synthesis
```

Add `--check-inputs` to any inference command to validate inputs without loading weights. See `spansynth-edit edit --help` for all options, including `--steps`, `--cfg`, and `--context-midi` (requires `--source-midi`).

## Timing and instruments

All times are **seconds**. Audio and MIDI must be aligned; no automatic transcription or alignment is provided.

| Option | Meaning |
| --- | --- |
| `--crop-start` | Crop origin on the audio timeline; default 0 |
| `--duration` | Crop length; default and maximum 20.48 |
| `--edit-start`, `--edit-end` | Interval relative to the crop; default 6.40–14.08 |
| `--midi-offset`, `--source-midi-offset` | Add to target/source MIDI times to obtain audio times |

For example, with `--crop-start 30`, a MIDI note at 36.4 s appears at 6.4 s in the crop. If the MIDI already starts at the crop boundary, add `--midi-offset 30` (and the source offset if needed). Edit boundaries round outward to 40 ms. Each invocation processes one crop.

See [vocabulary.py: supported MIDI programs and merged instrument groups](https://github.com/mimbres/spansynth-edit/blob/main/spansynth/vocabulary.py). Programs in the same group share one model category: for example, 0, 1, 3, 6, and 7 map to **Acoustic Piano**. Program numbers are zero-based; MIDI channel 10 is drums (internal program 128). Unlisted programs are unsupported.

## Results

Each output folder contains `output.wav` (edited crop), `generated.wav` (generated interval), `input.wav` (input crop), and `run.json` (settings and timing). Audio is converted to 48 kHz mono; samples outside the applied interval remain unchanged from that converted input. Existing results require `--overwrite` to replace.

## License and credits

Code and model assets use Apache-2.0; see [LICENSE](https://github.com/mimbres/spansynth-edit/blob/main/LICENSE) and [NOTICE](https://github.com/mimbres/spansynth-edit/blob/main/NOTICE), including YourMT3 and [HeartCodec](https://github.com/HeartMuLa/heartlib) attribution. Demo recordings retain their original rights. Spaces, automatic transcription, and an interactive MIDI editor are follow-up work.

## Citation

If you use SpanSynth-Edit in your research, please cite our forthcoming paper. **arXiv link and BibTeX: coming soon.**
