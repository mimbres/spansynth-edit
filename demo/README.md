# SpanSynth-Edit project page and audio demos

A static project page with the paper abstract, model overview, MIDI visualizations, and audio players. There is no build step, package installation, or survey response collection. The Dataset reference and Data banners remain inactive until their public destinations are ready.

## Local review

Serve the repository with a static HTTP server that supports HTTP byte-range requests, then open `/`. Byte-range support is needed for reliable audio seeking. GitHub Pages supports this delivery shape. The old `/demo/` URL redirects to the project page and preserves example links.

The page provides example selection, one-at-a-time playback, target interval markings, linked MIDI playheads, instrument selection, and system/light/dark appearance. For editing, original before-edit audio controls only the before-edit MIDI playhead, while edited ground truth and model outputs control the after-edit playhead. The views keep independent playback positions and horizontal scroll offsets, including when the instrument filter changes. On small screens, each view follows its own playhead and can be scrolled independently. Before-edit labels and original audio use green, while after-edit labels use coral. The default playback option keeps the current time when switching between recordings; Jump to target moves all players and MIDI views to the target start.

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

The selection contains two synthesis examples and ten Slakh songs, each with track insertion, track deletion, note insertion, and note deletion: 40 editing examples in total. The example selector groups the comparisons by task. POP909 Modulator edits appear in the separate AI-assisted edit tab.

| Examples | Task | Source excerpt (s) | Target in excerpt (s) |
| --- | --- | --- | --- |
| MusicNet · 2382 | Synthesis, violin/viola/cello ensemble | 10.24–30.72 | 5.76–14.76 |
| MusicNet · 2556 | Synthesis, solo piano | 0–20.48 | 3.08–13.72 |
| Slakh · Track01888 | Each of the four editing tasks | 43.5–63.98 | 6.92–13.08 |
| Slakh · Track01896 | Each of the four editing tasks | 180.5–200.98 | 0.32–14.68 |
| Slakh · Track01897 | Each of the four editing tasks | 116.5–136.98 | 11.60–17.76 |
| Slakh · Track01916 | Each of the four editing tasks | 81–101.48 | 11.52–17.72 |
| Slakh · Track01920 | Each of the four editing tasks | 192.5–212.98 | 2.20–12.48 |
| Slakh · Track01952 | Each of the four editing tasks | 54–74.48 | 2.64–8.80 |
| Slakh · Track01990 | Each of the four editing tasks | 111.5–131.98 | 9.04–19.32 |
| Slakh · Track02036 | Each of the four editing tasks | 46.5–66.98 | 3.56–17.96 |
| Slakh · Track02045 | Each of the four editing tasks | 109–129.48 | 6.48–16.76 |
| Slakh · Track02083 | Each of the four editing tasks | 133–153.48 | 3.36–17.72 |

All comparisons use the same crop within an example. The current Base SQ results use the V3 SQ V8 checkpoint at step 127,750, CFG 2, and 64 steps. FlowEdit is a separate generation at the same checkpoint and uses 64 steps. No GAN decoder output is used.

Base SQ, CTD, Spectrogram Diffusion, and U-MUST are available for both synthesis examples. MusicNet 2556 additionally has TokenSynth and MIDI-VALLE results. TokenSynth does not support the ensemble excerpt, and MIDI-VALLE requires solo piano. Editing shows Base SQ, FlowEdit, Spectrogram Diffusion, U-MUST, and CTD where supported. CTD does not support track insertion.

Source records are in the existing research directory `runs/listening-survey-candidates/regenerated/records.jsonl`. The original, edited reference, and MIDI paths are taken from each selected record. Model files are joined by the exact `record_id` in `outputs/<condition>/outputs.jsonl`, using its `full_audio_path`. The selected conditions are `syn-base-sq`, `edit-base-sq`, `edit-base-sq-flowedit`, `ctd`, `specdiff`, `umust`, `tokensynth-musicnet-solo`, and `midi-valle-musicnet-piano` as applicable.

## AI-assisted edit

The ten POP909 crops are 017, 019, 024, 025, 175, 178, 328, 330, 443, and 816. The original four author-selected crops remain included. Modulator, an external symbolic music generation model, inpaints a piano passage in each MIDI sequence. The audio models then render the revised MIDI. This task tests the transition from externally generated symbolic edits to audio while retaining the surrounding audio context.

Model reference: K. Bhandari, M. Bizzarri, G. A. Wiggins, and S. Colton, “Change is Key: A generative framework for controllable musical modulations,” Hugging Face model repository, 2026. [Modulator](https://huggingface.co/keshavbhandari/modulator).

| POP909 song | Source excerpt (s) | MIDI inpainting length (s) | Audio generation interval in excerpt (s) |
| --- | --- | ---: | --- |
| 017 | 76.023–96.503 | 5 | 11.72–16.76 |
| 019 | 154.771–175.251 | 5 | 6.12–11.16 |
| 024 | 7.299–27.779 | 10 | 2.00–12.04 |
| 025 | 293.474–313.954 | 10 | 1.96–12.00 |
| 175 | 133.422–153.902 | 5 | 14.00–19.04 |
| 178 | 190.544–211.024 | 5 | 7.68–12.72 |
| 328 | 213.791–234.271 | 5 | 4.04–9.08 |
| 330 | 65.078–85.558 | 10 | 2.96–13.00 |
| 443 | 189.026–209.506 | 10 | 10.16–20.20 |
| 816 | 144.987–165.467 | 7 | 5.36–12.40 |

These are the existing dry-piano crops from the same regenerated record set described above. Before/after reference audio was rendered with FluidSynth and Salamander Grand Piano. The after-edit reference is a rendering of Modulator's edited MIDI, rather than a recorded performance. MIDI inpainting boundaries retain their original timing, while the audio generation intervals shown in the players follow the existing 25 Hz latent-frame boundaries.

Each crop includes Base SQ CFG 2, Base SQ CFG 2 FlowEdit, CTD, Spectrogram Diffusion, U-MUST, TokenSynth, and MIDI-VALLE. The Base SQ and FlowEdit files use the current step-127,750 model with CFG 2 and 64 steps. Existing reference audio, edited MIDI, crop positions, and generated waveforms are reused. Only the listening copies receive the level preparation described below.

The ten records are selected by their original `record_id` in `records.jsonl`. Audio output paths are looked up in `outputs/<condition>/outputs.jsonl`, with `edit-base-sq`, `edit-base-sq-flowedit`, `ctd`, `specdiff`, `umust`, `tokensynth`, and `midi-valle` as the conditions. The downloadable MIDI files copy each record's `before_midi` and `after_midi`. Their parsed crop-relative notes are stored in the existing page data. The public files use `ai-pop909-` followed by the three-digit song number and its audio condition or MIDI role in `demo/assets/`.

## Comparison listening levels

Model comparison and AI-assisted edit audio follow the established procedure in `docs/spansynth-listening-survey.md` and `_survey_listening_gain` in `scripts/demo/generate_spansynth_v3_gallery.py`.

The original crop is peak-normalized to 0.95 once. Each Base SQ and FlowEdit output receives one constant gain based on its source-active context RMS. Other systems retain the normalized original context and receive one target gain matched to the ground-truth target. The after-edit reference uses the before-edit audio’s gain. All players in a crop receive the same final attenuation when needed to keep decoded MP3 peaks within 0.98. The copies are mono, 48 kHz, 192 kbps MP3.

This is reference-assisted level matching for listening. No limiter, crossfade, time stretching, or generated note editing is applied. The original research files remain unchanged. These level adjustments are used for Model comparison and AI-assisted edit, while Early demo preserves the original published audio.

## Files

- Root `index.html`: the abstract, project resources, model overview, and selected examples, including the exact MIDI note data used by the page.
- `demo/index.html`: redirect for previously shared demo URLs.
- `demo/styles.css`: responsive light and dark appearance.
- `demo/app.js`: audio controls, example navigation, and MIDI rendering.
- `demo/assets/`: selected MP3 and MIDI files plus the author-provided model overview SVG. Each audio or MIDI filename identifies the example and its condition or role.
- `demo/README.md`: running instructions and the sources and conditions needed to maintain the demo.

## Publication

The public project page is hosted at [mimbres.github.io/spansynth-edit/](https://mimbres.github.io/spansynth-edit/) with GitHub Pages, using the root of the `main` branch as its publishing source. Visiting this address keeps the short URL and selects the Kraisler Early editing example by default. Selecting another example adds its identifier to the URL for sharing.
