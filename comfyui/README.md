# SpanSynth-Edit for ComfyUI

Transcribe an audio clip with YourMT3+, edit its notes in a piano roll, and generate the edited audio in ComfyUI. You can also import MIDI or draw a score from scratch.

## Install

Use **ComfyUI 0.37.0 or newer** and Python 3.11–3.13. Install with the same Python environment that runs ComfyUI:

```bash
cd ComfyUI/custom_nodes
git clone --depth 1 --filter=blob:none --sparse https://github.com/mimbres/spansynth-edit.git
cd spansynth-edit
git sparse-checkout set spansynth app comfyui
python -m pip install '.[comfyui]'
```

Restart ComfyUI. The **SpanSynth-Edit** category will appear in the node menu. The model and HeartCodec weights download on first generation. CUDA with bfloat16 support, Apple Silicon (MPS), and CPU use the same inference code as the CLI. See the [system requirements](https://github.com/mimbres/spansynth-edit#system-requirements).

## Edit a recording

Drag [example.json](example.json) into ComfyUI, then:

1. Upload your audio in **Load Audio**. In **Prepare Audio Clip**, choose a crop of up to 20.48 seconds.
2. Click **Prepare / Open score** in **Edit Score**. Only the input and transcription nodes run at this point. YourMT3+ receives the selected audio clip, then the piano roll opens.
3. Select a track to edit its notes, or choose **+ Instrument** and confirm the instrument before drawing. Use the keyboard or GM preview to audition notes. Drag across the waveform to listen to a short range.
4. Click **Apply edits**, then **Close** and **Run**. The connected region outputs cover added, removed, and moved notes. With no changes, they cover the whole clip.
5. Reopen the score to compare **Original** and **Generated**, revise notes, and run again. Each generation uses the original recording as context.

**Save Audio** writes the result into ComfyUI's output folder. **Download latest WAV** on the generation node preserves the floating-point waveform. The piano roll also offers **Download MIDI** and **Download audio**. MIDI starts at the selected clip's time zero. Save the ComfyUI workflow to retain applied edits, and keep the source audio/MIDI files when moving it to another machine.

The defaults are **16 Euler steps**, **CFG 2.0**, context MIDI dropped, and context audio retained. Choose **spansynth-edit + flowedit** in the generation node to use FlowEdit with the connected original MIDI. To choose a region manually, disconnect the editor's region outputs and enter start/end in seconds relative to the clip. Boundaries expand to the 40 ms model grid.

## MIDI input and synthesis

- **Bring your own MIDI:** replace **Transcribe with YourMT3+** with **Load MIDI · SpanSynth-Edit** and click **Upload MIDI**. Connect it to both the editor's `source_midi` and the generator's `source_midi`. Offset aligns MIDI time zero with the full input recording, before crop.
- **Draw from scratch:** leave the editor's `source_midi` disconnected.
- **MIDI-to-audio:** disconnect audio from **Prepare Audio Clip**, set crop start to zero, choose a duration, and import or draw MIDI. Generate the whole clip for full synthesis.

Changing the crop or original MIDI requires **Reset score from inputs**. This replaces the applied score only after confirmation.

## Transcription and preview

YourMT3+ runs on the existing [Hugging Face Space](https://huggingface.co/spaces/mimbres/YourMT3), so it requires internet access and is subject to that Space's availability and GPU quota. Run `hf auth login` in the ComfyUI environment, or supply `HF_TOKEN` to the ComfyUI process to use your account. Credentials are never stored in workflows. Importing MIDI avoids remote transcription.

GM preview downloads instrument samples in the browser and approximates the score; it does not predict the generated audio's timbre. See [NOTICE](../NOTICE) for soundfont and HeartCodec attribution. Code and checkpoint terms are linked in the [main README](../README.md#license).
