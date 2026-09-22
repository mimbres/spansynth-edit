<h1 align="center">SpanSynth-Edit</h1>

<p align="center">
  <strong>MIDI-guided synthesis and editing of multi-instrument audio mixtures</strong>
</p>

<p align="center">
  <a href="https://mimbres.github.io/spansynth-edit/"><img src="https://img.shields.io/badge/%E2%96%B6%20Listen%20to%20demos-D8410B?style=for-the-badge" alt="Listen to demos" height="32"></a>&nbsp;
  <a href="https://github.com/mimbres/spansynth-edit"><img src="https://img.shields.io/badge/Source%20code-24292F?style=for-the-badge&amp;logo=github&amp;logoColor=white" alt="Source code on GitHub" height="32"></a>&nbsp;
  <a href="https://huggingface.co/mimbres/spansynth-edit"><img src="https://img.shields.io/badge/Model%20weights-FFD21E?style=for-the-badge&amp;logo=huggingface&amp;logoColor=24292F" alt="Model weights on Hugging Face" height="32"></a>
</p>

Add, remove, or modify notes in a recording by revising its MIDI. SpanSynth-Edit resynthesises the selected region, using surrounding audio for timbre guidance.

<p align="center">
  <img src="https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/FigureDraft-fig1-retro-04.svg" alt="SpanSynth-Edit model overview" width="100%">
</p>

<p align="center">
  <a href="#install">Install</a> &nbsp;·&nbsp;
  <a href="#try-an-example">Examples</a> &nbsp;·&nbsp;
  <a href="#options">Options</a> &nbsp;·&nbsp;
  <a href="#timing-and-instruments">Timing &amp; MIDI</a> &nbsp;·&nbsp;
  <a href="#results">Results</a> &nbsp;·&nbsp;
  <a href="#citation">Citation</a>
</p>

## Install

Install Python 3.11–3.13 and [PyTorch for your GPU](https://pytorch.org/get-started/locally/) first. A CUDA GPU with bfloat16 support is recommended. CPU inference is also supported, but Apple GPUs are not.

Install the CLI without downloading the demo audio:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/mimbres/spansynth-edit.git
cd spansynth-edit
git sparse-checkout set spansynth
python -m pip install .
```

Model and codec weights download automatically on first use. No token is required.

## Try an example

**Defaults:** `spansynth-edit` · 16 Euler steps · CFG 2.0<br>
Audio context enabled · Context MIDI omitted

### Example files

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

### spansynth-edit

Resynthesise the selected region from the revised MIDI:

```bash
spansynth-edit edit \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --midi ../spansynth-inputs/early-slakh-track00006-after.mid \
  --cfg 2.0 \
  --output ../spansynth-results/slakh-edit
```

### spansynth-edit + flowedit

Provide both the original and revised MIDI:

```bash
spansynth-edit edit --method flowedit \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --source-midi ../spansynth-inputs/early-slakh-track00006-before.mid \
  --midi ../spansynth-inputs/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-flowedit
```

### Synthesis

Use `spansynth-edit synthesize` to generate a region from a score. The recording supplies the surrounding audio context:

```bash
spansynth-edit synthesize \
  --audio recording.wav --midi score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --output ../spansynth-results/synthesis
```

Add `--check-inputs` to validate audio, MIDI, and timing without loading the model.

## Options

Common options are listed below. Flags marked **off** are enabled by adding them to the command. For all options, run `spansynth-edit edit --help` or `spansynth-edit synthesize --help`.

### Generation

| Option | Default | Use |
| --- | --- | --- |
| `--method` | `ordinary` | `ordinary` selects `spansynth-edit`; `flowedit` selects `spansynth-edit + flowedit`. Available with `edit` only. |
| `--cfg` | `2.0` | MIDI classifier-free guidance scale (0 or higher). |
| `--steps` | `16` | Number of Euler steps. |
| `--context-midi` | off | Use original MIDI outside the generated region. Requires `--source-midi`. |
| `--drop-context-audio` | off | Drop the clean-audio condition. Audio outside the generated region is still preserved. |

### Inputs and timing

All times are in **seconds**.

| Option | Default | Use |
| --- | --- | --- |
| `--audio` | required | Source recording or audio context. |
| `--midi` | required | Target MIDI: the score to synthesise or the revised score. |
| `--source-midi` | none | Original MIDI, required for `spansynth-edit + flowedit` or `--context-midi`. |
| `--crop-start` | `0.0` | Crop start on the audio timeline. |
| `--duration` | `20.48` | Crop length, up to 20.48 seconds. |
| `--edit-start`, `--edit-end` | `6.40`, `14.08` | Generated region relative to the crop. |
| `--midi-offset` | `0.0` | Offset added to target MIDI times to obtain audio times. |
| `--source-midi-offset` | `0.0` | Offset added to original MIDI times to obtain audio times. |

### Execution and output

| Option | Default | Use |
| --- | --- | --- |
| `--output` | required | Folder for generated audio and run settings. |
| `--device` | `auto` | Choose `cpu`, `cuda`, or `cuda:N`. `auto` uses CUDA when available. |
| `--overwrite` | off | Replace existing results in the output folder. |

## Timing and instruments

Provide aligned audio and MIDI, or use the offsets to align their timelines.

With `--crop-start 30`, a MIDI note at 36.4 s appears at 6.4 s in the crop. If MIDI time 0 corresponds to the start of that crop, use `--midi-offset 30`. Set `--source-midi-offset` independently for the original MIDI. Each run processes one crop, with edit boundaries rounded outward to 40 ms.

The [instrument vocabulary](https://github.com/mimbres/spansynth-edit/blob/main/spansynth/vocabulary.py) lists supported MIDI programs and merged instrument groups. Programs in a group share one model category: for example, 0, 1, 3, 6, and 7 map to **Acoustic Piano**. Program numbers are zero-based, and MIDI channel 10 selects drums (internal program 128).

## Results

| File | Contents |
| --- | --- |
| `output.wav` | Full crop with the synthesised or edited region |
| `generated.wav` | Generated region only |
| `input.wav` | Source crop converted to 48 kHz mono |
| `run.json` | Settings and timing |

All audio outputs are 48 kHz mono. Outside the generated region, `output.wav` matches `input.wav` exactly. Use `--overwrite` to replace existing results.

## License and credits

Code and model weights are released under Apache-2.0. See [LICENSE](https://github.com/mimbres/spansynth-edit/blob/main/LICENSE) and [NOTICE](https://github.com/mimbres/spansynth-edit/blob/main/NOTICE) for terms and credits, including YourMT3 and [HeartCodec](https://github.com/HeartMuLa/heartlib). Demo recordings retain their original rights.

## Citation

If you use SpanSynth-Edit in your research, please cite our forthcoming paper. **arXiv link and BibTeX: coming soon.**
