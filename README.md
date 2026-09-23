<h1 align="center">SpanSynth-Edit</h1>

<p align="center">
  <strong>MIDI-guided synthesis and editing of multi-instrument audio mixtures</strong>
</p>

<p align="center">
  <a style="display:inline-block" href="https://arxiv.org/abs/2609.25546"><img src="https://img.shields.io/badge/arXiv-2609.25546-b31b1b.svg" alt="Paper on arXiv" height="20" style="display:inline-block;margin:0;height:20px;vertical-align:middle;"></a>&nbsp;
  <a style="display:inline-block" href="https://github.com/mimbres/spansynth-edit"><img src="https://img.shields.io/badge/GitHub-Code-181717?logo=github&amp;logoColor=white" alt="Source code on GitHub" height="20" style="display:inline-block;margin:0;height:20px;vertical-align:middle;"></a>&nbsp;
  <a style="display:inline-block" href="https://huggingface.co/mimbres/spansynth-edit"><img src="https://img.shields.io/badge/Checkpoint-555555" alt="Checkpoint" height="20" style="display:inline-block;margin:0;height:20px;vertical-align:middle;"></a>&nbsp;
  <a style="display:inline-block" href="https://huggingface.co/spaces/mimbres/spansynth-edit"><img src="https://img.shields.io/badge/Live%20Demo-555555" alt="Live Demo" height="20" style="display:inline-block;margin:0;height:20px;vertical-align:middle;"></a>&nbsp;
  <a style="display:inline-block" href="https://mimbres.github.io/spansynth-edit/"><img src="https://img.shields.io/badge/Demo-Listen-007ec6" alt="Listen to audio demos" height="20" style="display:inline-block;margin:0;height:20px;vertical-align:middle;"></a>
</p>

Synthesise music from MIDI, or add, remove, and modify notes in a recording by revising its MIDI. SpanSynth-Edit generates the selected region, using surrounding audio for timbre guidance.

[Try it in your browser](https://huggingface.co/spaces/mimbres/spansynth-edit): upload audio, transcribe with YourMT3+, edit the piano roll, and generate. See the [local web setup](https://github.com/mimbres/spansynth-edit/blob/main/app/README.md) to run the app yourself.

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

### System requirements

- **Python:** 3.11–3.13 with [PyTorch](https://pytorch.org/get-started/locally/).
- **NVIDIA GPU:** CUDA with bfloat16 support. **6 GB VRAM recommended.**
- **Apple Silicon GPU:** supported through **Metal (PyTorch MPS)**. Tested on an **M1 Pro with 16 GB unified memory** and PyTorch 2.13.
- CPU inference is also supported.

Measured peak VRAM was **about 3.9 GiB** on a GH200 for synthesis and both editing methods with default settings (20.48 s crop, 16 steps, CFG 2.0).

Install [PyTorch for your GPU](https://pytorch.org/get-started/locally/) first, then install the CLI without downloading the demo audio:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/mimbres/spansynth-edit.git
cd spansynth-edit
git sparse-checkout set spansynth
python -m pip install .
```

Model and codec weights download automatically on first use. No token is required.

### Diffusers

Install the optional integration and convert the public weights once:

```bash
python -m pip install '.[diffusers]'
git sparse-checkout add scripts
python -m scripts.export_checkpoint --diffusers --output ../spansynth-diffusers
```

Use the same example files and timing as the CLI below:

```python
import soundfile as sf
from spansynth.diffusers_pipeline import SpanSynthEditPipeline

pipe = SpanSynthEditPipeline.from_pretrained(
    '../spansynth-diffusers', trust_remote_code=True,
).to('cuda')
result = pipe(
    audio='../spansynth-inputs/early-slakh-track00006-original.mp3',
    midi='../spansynth-inputs/early-slakh-track00006-after.mid',
    num_inference_steps=16,
    guidance_scale=2.0,
)
sf.write('edited.wav', result.audios[0, 0], result.sample_rate, subtype='FLOAT')
```

Use `.to('mps')` for Apple Silicon or `.to('cpu')` for CPU. For FlowEdit, add
`method='flowedit'` and `source_midi` pointing to the original score. The pipeline
also accepts the crop, region, MIDI offset, and context options described below.
`pipe.save_pretrained(path)` saves both components for later loading.

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

The examples below regenerate **6.40–14.08 s** within the first **20.48 s** of the recording.

### spansynth-edit

Provide the full revised MIDI, including notes that should remain unchanged within the selected region:

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

Synthesise the original score in the selected region, using the surrounding recording as audio context:

```bash
spansynth-edit synthesize \
  --audio ../spansynth-inputs/early-slakh-track00006-original.mp3 \
  --midi ../spansynth-inputs/early-slakh-track00006-before.mid \
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
| `--drop-context-audio` | off | Drop the audio-context condition. Audio outside the generated region is still preserved. |

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
| `--device` | `auto` | Choose `cpu`, `mps` (Apple GPU), `cuda`, or `cuda:N`. `auto` tries CUDA, then MPS, then CPU. |
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

Code and model weights are released under Apache-2.0. See [LICENSE](https://github.com/mimbres/spansynth-edit/blob/main/LICENSE) and [NOTICE](https://github.com/mimbres/spansynth-edit/blob/main/NOTICE) for terms and credits, including YourMT3+ and [HeartCodec](https://github.com/HeartMuLa/heartlib). Demo recordings retain their original rights.

## Citation

If you use SpanSynth-Edit in your research, please cite [our paper](https://arxiv.org/abs/2609.25546):

```bibtex
@misc{chang2026spansynthedit,
  title={Synthesis and editing of multi-instrument audio mixtures using scalar-quantised latents with {MIDI Span} conditioning},
  author={Sungkyun Chang and Keshav Bhandari and Simon Dixon and Emmanouil Benetos},
  year={2026},
  eprint={2609.25546},
  archivePrefix={arXiv},
  primaryClass={cs.SD},
  url={https://arxiv.org/abs/2609.25546}
}
```
