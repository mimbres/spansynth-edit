---
title: SpanSynth-Edit
emoji: 🎹
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: 6.28.0
python_version: 3.12
app_file: app/app.py
pinned: false
license: apache-2.0
short_description: Edit notes in a recording with MIDI-guided music generation
thumbnail: https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/social-preview.png
---

# SpanSynth-Edit

Upload a recording, transcribe it with YourMT3+, edit the notes, and regenerate a selected region with SpanSynth-Edit.

[Source code](https://github.com/mimbres/spansynth-edit) · [Model weights](https://huggingface.co/mimbres/spansynth-edit) · [Listening examples](https://mimbres.github.io/spansynth-edit/)

The editor supports multiple instruments, drums, note creation, deletion, movement, resizing, and undo/redo. Each clip is up to 20.48 seconds. Audio outside the selected region is preserved in the 48 kHz mono result. MIDI export supports 15 melodic instruments plus drums; [supported programs and merged groups](https://github.com/mimbres/spansynth-edit/blob/main/spansynth/vocabulary.py) follow the model vocabulary. The note preview uses a simple synth, so it does not represent the generated timbre.

Transcription uses the existing [YourMT3+ Space](https://huggingface.co/spaces/mimbres/YourMT3). You can also upload an aligned MIDI file. The original MIDI is kept separately for `spansynth-edit + flowedit`.

Use **Apply edits** to save an updated MIDI file, or **Apply & Generate** to generate audio from your latest edits. The region automatically covers added, removed, and modified notes, including their original positions. Turn off **Auto region from note edits** to set the region yourself. The **Light mode / Dark mode** button at the top switches appearance without clearing your work.

Three sample inputs are available: **Slakh**, **Kraisler**, and **Jazz intro** (*Piano Sway Intro B* by OurMusicBox). Slakh and Kraisler include aligned MIDI. Use YourMT3+ to transcribe the jazz sample. Downloaded audio and MIDI use the chosen clip's timeline.

Uploads and results are stored temporarily for your session. Temporary files are periodically removed. GPU availability and usage limits are managed by Hugging Face ZeroGPU.

![Model overview](https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/FigureDraft-fig1-retro-04.svg)

## Run locally

From the GitHub repository root, install `app/requirements.txt` in a separate Python 3.12 environment, then run. If you used the CLI's sparse clone, first run `git sparse-checkout set spansynth app scripts`.

```bash
python -m pip install -r app/requirements.txt
python app/app.py
```

CUDA, Apple Silicon through MPS, and CPU are supported locally. Model weights download on first launch. For a UI-only check without loading weights, use `SPANSYNTH_SKIP_MODELS=1 python app/app.py`; generation is disabled in that mode.

## Deploy

`python scripts/deploy_space.py` publishes the app and inference package to `mimbres/spansynth-edit`. It excludes the static demo audio and preserves this file as the Space's root README. The existing HF model repository is separate from the Space.
