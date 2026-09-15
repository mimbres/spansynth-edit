# SpanSynth-Edit audio demos

A static paper demo with MIDI visualizations and audio players. There is no build step, package installation, or survey response collection.

## Local review

Serve the repository with a static HTTP server that supports HTTP byte-range requests, then open `/demo/`. Byte-range support is needed for reliable audio seeking. GitHub Pages supports this delivery shape.

The page provides example selection, one-at-a-time playback, target interval markings, linked MIDI playheads, instrument selection, and system/light/dark appearance. On small screens, both MIDI views scroll horizontally together. The default playback option keeps the current time when switching between recordings.

## Early demo

These examples reuse the original published audio files from the earlier V3 SQ model at step 40,250. They do not use the current survey checkpoint or external comparison systems. Base SQ CFG 2 uses 64 steps. The FlowEdit results use 32 steps, start step 0, and CFG 2.

| Task | Example | Source excerpt (s) | Target in excerpt (s) |
| --- | --- | --- | --- |
| Chromatic MIDI editing | Kraisler · track 01 | 20.48–40.96 | 6.40–14.08 |
| Chromatic MIDI editing | Slakh · Track00006 | 209.92–230.40 | 6.40–14.08 |
| Chromatic MIDI editing | MusicNet · 2244 | 117.76–138.24 | 6.40–14.08 |
| Synthesis | MAESTRO test · 2004 Competition, Track 12 | 66.676125–87.156125 | 6.40–14.08 |
| Synthesis | BSED · Symphony No. 1, I · Blomstedt 1976 | 4.911–25.391 | 6.40–14.08 |
| Synthesis | GOAT · item 89 · distortion | 20.48–40.96 | 6.40–14.08 |

The three chromatic edits have no recorded after-edit ground truth. Their reference is the original recording, and the requested pitches are shown by the after-edit MIDI. Kraisler uses six descending violin notes (68–63), Slakh ten ascending trumpet notes (79–88), and MusicNet twelve ascending violin notes (67–78). All other note fields are preserved.

Audio is copied without further encoding or normalization from the established `anysynth_full_flowedit/assets/` files beginning with `spansynth-v3-step40250-`. Direct editing files include `directedit-`, and FlowEdit files include `flowedit-`. The historical files already contain their original peak normalization.

The exact crop definitions and chromatic changes come from the existing research scripts `scripts/demo/generate_spansynth_v3_gallery.py` and `scripts/demo/generate_spansynth_flowedit_gallery.py`. MIDI visualization uses the same corrected note arrays, 16 kHz note-time conversion, and verified original note selections. Downloadable MIDI files contain those crop-relative note events. Notes crossing the excerpt edges are clipped only at export/display boundaries.

## Model comparison

The current review selection contains two synthesis examples and two examples of each of track insertion, track deletion, note insertion, and note deletion. Final listening selection remains subject to author review. POP909 is not included in this selection.

| Examples | Task | Source excerpt (s) | Target in excerpt (s) |
| --- | --- | --- | --- |
| MusicNet · 2382 | Synthesis, violin/viola/cello ensemble | 10.24–30.72 | 5.76–14.76 |
| MusicNet · 2556 | Synthesis, solo piano | 0–20.48 | 3.08–13.72 |
| Slakh · Track02045 | Each of the four editing tasks | 109–129.48 | 6.48–16.76 |
| Slakh · Track02083 | Each of the four editing tasks | 133–153.48 | 3.36–17.72 |

All comparisons use the same crop within an example. The current Base SQ results use the V3 SQ V8 checkpoint at step 127,750, CFG 2, and 64 steps. FlowEdit is a separate generation at the same checkpoint and uses 64 steps. No GAN decoder output is used.

Base SQ, CTD, Spectrogram Diffusion, and U-MUST are available for both synthesis examples. MusicNet 2556 additionally has TokenSynth and MIDI-VALLE results. TokenSynth does not support the ensemble excerpt, and MIDI-VALLE requires solo piano. Editing shows Base SQ, FlowEdit, Spectrogram Diffusion, U-MUST, and CTD where supported. CTD does not support track insertion.

Source records are in the existing research directory `runs/listening-survey-candidates/regenerated/records.jsonl`. The original, edited reference, and MIDI paths are taken from each selected record. Model files are joined by the exact `record_id` in `outputs/<condition>/outputs.jsonl`, using its `full_audio_path`. The selected conditions are `syn-base-sq`, `edit-base-sq`, `edit-base-sq-flowedit`, `ctd`, `specdiff`, `umust`, `tokensynth-musicnet-solo`, and `midi-valle-musicnet-piano` as applicable.

## Comparison listening levels

The comparison audio follows the established procedure in `docs/spansynth-listening-survey.md` and `_survey_listening_gain` in `scripts/demo/generate_spansynth_v3_gallery.py`.

The original crop is peak-normalized to 0.95 once. Each Base SQ and FlowEdit output receives one constant gain based on its source-active context RMS. Other systems retain the normalized original context and receive one target gain matched to the ground-truth target. The after-edit reference uses the before-edit recording’s gain. All players in a crop receive the same final attenuation when needed to keep decoded MP3 peaks within 0.98. The copies are mono, 48 kHz, 192 kbps MP3.

This is reference-assisted level matching for listening. No limiter, crossfade, time stretching, or generated note editing is applied. The original research files remain unchanged. These level adjustments are used only for Model comparison, while Early demo preserves the original published audio.

## Files

- `index.html`: page content and the selected examples, including the exact MIDI note data used by the page.
- `styles.css`: responsive light and dark appearance.
- `app.js`: audio controls, example navigation, and MIDI rendering.
- `assets/`: selected MP3 and MIDI files only. Each filename identifies the example and its audio condition or MIDI role.
- `README.md`: running instructions and the sources and conditions needed to maintain the demo.

## Publication

GitHub Pages has not yet been enabled. The intended public path is `/spansynth-edit/demo/` on the existing account’s Pages site. The local review URL is separate from public deployment.
