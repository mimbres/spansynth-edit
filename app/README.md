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
hf_oauth: true
license: apache-2.0
short_description: Edit notes in a recording with MIDI-guided music generation
thumbnail: https://raw.githubusercontent.com/mimbres/spansynth-edit/main/demo/assets/social-preview.png
---

# SpanSynth-Edit

Upload a recording, transcribe it with YourMT3+, edit the notes, and regenerate a selected region with SpanSynth-Edit.

[Source code](https://github.com/mimbres/spansynth-edit) · [Model weights](https://huggingface.co/mimbres/spansynth-edit) · [Listening examples](https://mimbres.github.io/spansynth-edit/)

Select a track to edit its notes; other tracks remain visible but cannot be changed. Choose **+ Instrument**, select an instrument, and confirm with **Add instrument** before drawing. Use **Pencil** to draw and **Eraser** to remove notes. With **Select**, drag across empty space to select a group, then move, transpose with ↑/↓, or delete it. **Shift-click** extends the selection; undo/redo is available. Each clip is up to 20.48 seconds. Audio outside the selected region is preserved in the 48 kHz mono result. MIDI export supports 15 melodic instruments plus drums; [supported programs and merged groups](https://github.com/mimbres/spansynth-edit/blob/main/spansynth/vocabulary.py) follow the model vocabulary.

Transcription uses the existing [YourMT3+ Space](https://huggingface.co/spaces/mimbres/YourMT3). You can also upload an aligned MIDI file. The original MIDI is kept separately for `spansynth-edit + flowedit`.

Click the waveform to seek, or drag across it to choose a playback range. **Play audio** compares the original or generated clip; **Preview notes** plays selected notes or the current track. In steps 1 and 3, the **|◀** button beside the playback controls returns to the beginning.

Click piano keys or draw notes to hear GM instrument samples, also used by **Preview notes**. Drums use a standard drum kit, and singing voices use choir samples. Each instrument downloads on first use and stays loaded while the editor is open. This preview differs from the generated audio, and long notes may outlast the samples. Preview uses [smplr](https://github.com/danigb/smplr) and FluidR3_GM; see [credits and licenses](https://github.com/mimbres/spansynth-edit/blob/main/NOTICE).

Use **Apply edits** to save an updated MIDI file, or **Apply & Generate** to generate audio from your latest edits. Keep editing after generation and generate again to try another version. The region automatically covers added, removed, and modified notes, including their original positions. Turn off **Auto region from note edits** to set the region yourself. The **Light mode / Dark mode** button at the top switches appearance without clearing your work.

Three sample inputs are available: **Slakh**, **Kraisler**, and **Jazz intro** (*Piano Sway Intro B* by OurMusicBox). Slakh and Kraisler include aligned MIDI. Use YourMT3+ to transcribe the jazz sample. Downloaded audio and MIDI use the chosen clip's timeline.

**Save & Share** preserves the last generated audio, its MIDI, and its editing settings. A title is filled in automatically; change it if you like, then save to get a shareable link. Publishing is currently limited to the `mimbres` HF account; sign in to the Space in the new tab and return to your editing tab. The app shows your sign-in status. Viewing and editing need no login. A shared link opens an editable copy without changing the published work. The **Gallery** lets visitors compare original and edited clips, then open a work in the editor.

Saved audio and MIDI are public in the [gallery dataset](https://huggingface.co/datasets/mimbres/spansynth-edit-gallery), including works not listed in the gallery. Unsaved uploads and results are temporary. GPU availability and usage limits are managed by Hugging Face ZeroGPU.

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

The deployment also prepares the gallery dataset and sets `SPANSYNTH_GALLERY_TOKEN` as a Space secret using the local HF login. This credential must have write access to the gallery dataset. The Space uses HF OAuth to verify the publisher; no repository access is requested from visitors. Locally, Gradio's sign-in uses the HF account already logged in on the machine.
