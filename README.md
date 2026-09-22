---
license: apache-2.0
tags:
  - audio
  - music
  - midi
  - audio-to-audio
  - flow-matching
---

# SpanSynth-Edit

MIDI-guided music synthesis and editing with the V8 checkpoint at step **127,750** and the frozen **Base SQ (HeartCodec)** encoder/decoder. Ordinary generation and FlowEdit use the same weights.

[Project page and audio demos](https://mimbres.github.io/spansynth-edit/) · [Source code](https://github.com/mimbres/spansynth-edit) · [Model weights](https://huggingface.co/mimbres/spansynth-edit)

![SpanSynth-Edit model overview](https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/FigureDraft-fig1-retro-04.svg)

The CLI defaults to **16 Euler steps, CFG 2, ordinary generation, context MIDI dropout on, and context audio dropout off**. FlowEdit is opt-in. Existing website examples retain their published settings, which can differ from these CLI defaults.

## Install

Use Python 3.11–3.13. A CUDA GPU supporting bfloat16 is recommended. Install a PyTorch build appropriate for your GPU and driver using the [official PyTorch instructions](https://pytorch.org/get-started/locally/) before installing this package. CPU execution is supported but a full checkpoint run is expensive. Apple GPU execution is not supported.

```bash
git clone https://github.com/mimbres/spansynth-edit.git
cd spansynth-edit
python -m pip install .
spansynth-edit --help
```

The package uses PyTorch, NumPy, Mido, SoundFile, SciPy, safetensors, and huggingface-hub. No training framework, transcription model, external MIDI renderer, or separately installed HeartCodec package is needed. A setuptools build installs only `spansynth`, without the demo audio files. If SoundFile cannot load its audio library on your platform, install `libsndfile` with your operating system's package manager.

Download the approximately 1.92 GB generator weights and the bundled codec once:

```bash
spansynth-edit download
```

The first inference also downloads missing weights automatically. Public downloads do not require a token. Files are cached through Hugging Face. To choose a cache location, set `HF_HOME` or pass `--cache-dir`. Set these outside the Git checkout. Add `--offline` to use only already-cached or explicitly supplied local files.

The model repository contains:

```text
v3-sq-v8-127750step/
  config.json
  model.safetensors
heartcodec-sq/
  scalar_model_config.json
  scalar_model.safetensors
README.md
LICENSE
NOTICE
```

The SQ files are the exact frozen scalar codec used with this checkpoint. They are not the complete HeartCodec music-generation pipeline or a fine-tuned decoder. To use manually downloaded files, pass both `--checkpoint /path/to/v3-sq-v8-127750step` and `--codec-dir /path/to/heartcodec-sq`.

## Try the included example

Run these from the Git clone. The example's audio and MIDI already use the same crop-relative timeline. Outputs can be stored outside the checkout:

```bash
spansynth-edit edit \
  --audio demo/assets/early-slakh-track00006-original.mp3 \
  --midi demo/assets/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-edit
```

This uses ordinary generation, 16 steps, CFG 2, and no context MIDI. To use FlowEdit, also supply the before-edit MIDI:

```bash
spansynth-edit edit --method flowedit \
  --audio demo/assets/early-slakh-track00006-original.mp3 \
  --source-midi demo/assets/early-slakh-track00006-before.mid \
  --midi demo/assets/early-slakh-track00006-after.mid \
  --output ../spansynth-results/slakh-flowedit
```

The public MP3 is a listening copy with the website's level adjustments. Inference from this MP3 does not reproduce the original research waveform exactly.

## Synthesis and ordinary editing

Both commands generate the requested interval from MIDI, conditioned on the surrounding audio. Use `synthesize` when the MIDI describes music to synthesize and `edit` when it describes a change to an existing recording. In ordinary mode, the original audio inside the generated interval is replaced by noise for generation. Its before-edit MIDI is not required with the default context MIDI dropout.

```bash
spansynth-edit synthesize \
  --audio recording.wav --midi score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --output ../spansynth-results/synthesis

spansynth-edit edit \
  --audio recording.wav --midi edited-score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --output ../spansynth-results/edit
```

To condition on before-edit MIDI outside the interval, add `--context-midi --source-midi score.mid`. Context audio remains enabled by default. `--drop-context-audio` disables the static clean-audio projection, while observed audio still follows its fixed interpolation path and remains unchanged in the saved output.

The model was trained on generated spans covering 30–70% of a 20.48-second window. Interior spans had at least 5% context on each side. The CLI permits other intervals, but their audio quality has not been established. It processes one window per invocation, without automatic long-form stitching.

## FlowEdit

FlowEdit starts from the encoded recording and uses the velocity difference between the before-edit and edited MIDI conditions. It requires the original audio, its before-edit MIDI, and edited MIDI on aligned timelines:

```bash
spansynth-edit edit --method flowedit \
  --audio recording.wav --source-midi score.mid --midi edited-score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --steps 16 --cfg 2 --start-step 0 --average 1 \
  --output ../spansynth-results/flowedit
```

`--start-step` selects the first active step on the full Euler grid and must be smaller than `--steps`. `--average` sets the number of independently drawn noise samples per step. Increasing it increases computation and memory. Context MIDI is dropped by default for both methods, while the before-edit MIDI inside the edited interval still supplies FlowEdit's source condition.

## Timing and audio handling

All CLI times are **seconds**. MIDI tempo changes are honored. No automatic transcription, alignment estimation, time stretching, or MIDI editing is performed.

| Option | Meaning |
| --- | --- |
| `--crop-start` | Crop origin on the input audio timeline, default 0 |
| `--duration` | Output crop length, default and maximum 20.48 |
| `--edit-start`, `--edit-end` | Generated interval relative to the crop, default 6.40–14.08 |
| `--midi-offset` | Added to target MIDI times to obtain input-audio times |
| `--source-midi-offset` | Added to before-edit MIDI times to obtain input-audio times |

The alignment equation is `audio time = MIDI time + offset`. By default, audio and both MIDI files share an absolute timeline. With `--crop-start 30`, a MIDI note at 36.4 seconds appears at 6.4 seconds in the crop. If the MIDI was already trimmed to the crop, use `--midi-offset 30`. A target-only MIDI starting at the edit boundary needs `--midi-offset 36.4` in this example. Supply the corresponding source offset independently if necessary.

The codec has 25 latent frames per second. Interval starts round down and ends round up to the nearest 40 ms boundary. The CLI prints the effective interval and records both requested and applied times in `run.json`. The last output sample is limited by `--duration`. Crop origins round to the nearest 48 kHz sample.

SoundFile decodes supported audio formats, including WAV, FLAC, OGG, and MP3 on current libsndfile builds. Stereo is averaged to mono, then SciPy resamples to 48 kHz. The encoder retains two seconds of causal history before the crop when available. Missing leading history and short audio tails are zero-padded. Audio beyond `--duration` is excluded. Input peaks above 0.95 receive one encoder gain, which is reversed on generated audio when assembling the result.

Only the effective generated interval is replaced in `output.wav`. Samples outside it match the resampled mono input crop exactly. This preserves the source waveform outside the edit instead of decoding the entire crop for final output. There is no boundary crossfade or loudness matching. Outputs use float32 WAV so finite peaks above 1 are retained rather than clipped. Adjust playback gain if required.

General MIDI program-change events select instruments, channel 10 denotes drums, and melodic channels without a program-change start at program 0 (acoustic piano). `--program` and `--source-program` override all notes in the respective file. The model supports the Fine40 instrument groups in `spansynth/vocabulary.py`; unsupported programs and frames exceeding 128 active note rows are rejected explicitly. Sustain pedal, repeated notes, and notes crossing the crop or edit boundaries are retained.

## Check inputs and inspect results

Validate timing, MIDI capacity and audio decoding without downloading weights or starting GPU inference:

```bash
spansynth-edit edit \
  --audio recording.wav --midi edited-score.mid \
  --crop-start 30 --edit-start 6.4 --edit-end 14.08 \
  --output ../spansynth-results/edit --check-inputs
```

Each run saves:

- `output.wav`: the full selected crop with the generated interval inserted.
- `generated.wav`: only the generated interval, on a timeline starting at zero.
- `input.wav`: the resampled mono source crop, including any zero-padded tail.
- `run.json`: input paths, timing, generation settings, checkpoint step and measured runtime.
- `codes.safetensors`: optional with `--save-codes`, containing 50 history frames and 512 output frames.

Existing result files are protected unless `--overwrite` is supplied. Run settings contain local input paths, so review them before sharing a result folder. Each inference draws fresh noise; repeated invocations are not expected to produce identical music. A successful numerical check does not establish listening quality.

Use `--device cuda:0` to select a GPU, `--threads` to limit CPU threads, and `--attention math` for PyTorch's math attention backend. `--attention auto` uses PyTorch's available native kernels. No external FlashAttention package is required.

## Release and development

The inference code preserves V8 parameter names and uses strict safetensors loading. Training state, optimizer files, evaluation models and dataset preparation are excluded. To export the approved research checkpoint and codec into a release directory outside the checkout:

```bash
PYTHONPATH=. python scripts/export_checkpoint.py \
  --checkpoint /path/to/checkpoint-step-00127750 \
  --codec-dir /path/to/sq-codec-v1 \
  --output /path/to/spansynth-release
```

The export includes model configuration, model weights, frozen codec assets and these public documentation/license files. It refuses to overwrite an existing release. Upload only the resulting release folder to `mimbres/spansynth-edit`.

```bash
python -m pip install '.[test]'
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

Validation on a Jupiter GH200 with Python 3.12 and PyTorch 2.13 / CUDA 13 completed synthesis, ordinary editing and FlowEdit at the default 16 steps. All three produced finite 48 kHz outputs with unchanged samples outside the applied interval. Small-model numerical comparisons matched the original MIDI conditions and quantized Euler/FlowEdit outputs. These checks establish execution and numerical behavior, not perceptual quality.

The existing static website uses `index.html` and `demo/`, with no Python build required. See [demo maintenance notes](https://github.com/mimbres/spansynth-edit/blob/main/demo/README.md). Spaces, automatic transcription and an interactive MIDI editor are follow-up work.

## License and credits

Inference code and released model assets use Apache-2.0. See `LICENSE` and `NOTICE`, including attribution to YourMT3 and [HeartMuLa/HeartCodec](https://github.com/HeartMuLa/heartlib). Dataset recordings and third-party examples on the demo site retain their original rights and attribution; the software license does not grant new rights to those recordings.
