# SpanSynth-Edit project page and audio demos

A static project page with the paper abstract, model overview, MIDI visualizations, and audio players. There is no build step, package installation, or survey response collection. The Dataset reference and Data banners remain inactive until their public destinations are ready.

## Local review

Serve the repository with a static HTTP server that supports HTTP byte-range requests, then open `/`. Byte-range support is needed for reliable audio seeking. GitHub Pages supports this delivery shape. The old `/demo/` URL redirects to the project page and preserves example links.

The page provides example selection, one-at-a-time playback, target interval markings, linked MIDI playheads, instrument selection, and system/light/dark appearance. For editing, original before-edit audio controls only the before-edit MIDI playhead, while edited ground truth and model outputs control the after-edit playhead. The views keep independent playback positions and horizontal scroll offsets, including when the instrument filter changes. On small screens, each view follows its own playhead and can be scrolled independently. Before-edit labels and original audio use green, while after-edit labels use coral. The default playback option keeps the current time when switching between recordings; Jump to target moves all players and MIDI views to the target start.

## Early demo

The ten Early selections use the same V3 SQ V8 checkpoint at step 127,750 used in the comparison tabs, with the Base SQ decoder and CFG 2. The default is 64 Euler steps. Both ordinary generation and FlowEdit are available for editing; FlowEdit starts at step 0. These sections contain no external comparison systems. The six original selections and their nine default outputs are retained.

| Task | Example | Source excerpt (s) | Target in excerpt (s) |
| --- | --- | --- | --- |
| Chromatic MIDI editing | Kraisler · track 01 | 20.48–40.96 | 6.40–14.08 |
| Chromatic MIDI editing | Slakh · Track00006 | 209.92–230.40 | 6.40–14.08 |
| Chromatic MIDI editing | MusicNet · 2244 | 117.76–138.24 | 6.40–14.08 |
| Chromatic MIDI editing | FiloBass · 000000 | 168.96–189.44 | 6.40–14.08 |
| Chromatic MIDI editing | Chorale Bricks · 000000 | 15.36–35.84 | 6.40–14.08 |
| Synthesis | MAESTRO test · 2004 Competition, Track 12 | 66.676125–87.156125 | 6.40–14.08 |
| Synthesis | BSED · Symphony No. 1, I · Blomstedt 1976 | 4.911–25.391 | 6.40–14.08 |
| Synthesis | GOAT · item 89 · distortion | 20.48–40.96 | 6.40–14.08 |
| Synthesis | URMP · Chorale 43 | 10.24–30.72 | 6.40–14.08 |
| Synthesis | PianoVAM · 000000 | 353.28–373.76 | 6.40–14.08 |

The five chromatic edits have no recorded after-edit ground truth. Their reference is the original recording, and the requested pitches are shown by the after-edit MIDI. Kraisler uses six descending violin notes (68–63), Slakh ten ascending trumpet notes (79–88), and MusicNet twelve ascending violin notes (67–78). FiloBass retains the historical eleven descending bass notes (44–34). Chorale Bricks changes all four musical parts to ascending chromatic lines, starting at each part’s first complete target note and retaining each instrument’s register. Notes crossing the target boundaries are preserved. Timing, duration, velocity and instrument labels remain unchanged.

Chorale Bricks voice identities come from the per-track annotations in [ChoraleBricks, Balke, Berndt and Müller (2025)](https://zenodo.org/records/15081741), licensed CC BY 4.0. Its combined MIDI merges some instruments and voices. Source annotations recover the soprano, alto, tenor and bass assignments; split fragments of the same performed note share the same edited pitch. Simultaneous same-pitch notes use separate MIDI channels where necessary to preserve their note-off times. The published MIDI is a modified excerpt.

The 65 full generated WAVs are saved locally in `anysynth_full_flowedit/runs/listening-survey-candidates/early-step127750/`. The corresponding directory in the Jupiter research checkout also holds the source audio, exact before/after MIDI, generated codes, target WAVs, and generation settings. The production V3 SQ inference command generates the same 6.40–14.08 s interval, retaining two seconds of codec history before the excerpt. Source crops and every displayed MIDI note were checked against the original selections before generation.

The exact crop definitions and chromatic changes come from the existing research scripts `scripts/demo/generate_spansynth_v3_gallery.py` and `scripts/demo/generate_spansynth_flowedit_gallery.py`. MIDI visualization uses the same corrected note arrays, 16 kHz note-time conversion, and verified original note selections. Downloadable MIDI files contain those crop-relative note events. Notes crossing the excerpt edges are clipped only at export/display boundaries.

### Ablations

One dropdown selects the condition while retaining the original player, MIDI views, instrument filter and playback positions. Switching conditions pauses the model output so the next comparison starts at the retained position.

- **Early editing:** 64 steps (default), 32, 16, 8 and 4, for both ordinary generation and FlowEdit. No synthesis step ablations are included.
- **Early synthesis:** the normal 64-step condition, context clean audio dropout, or context MIDI dropout. Audio dropout zeros only the static clean-reference projection input, preserving observed SQ states and all MIDI. MIDI dropout removes reference MIDI while retaining target MIDI and contextual audio. Each dropout is applied separately.

Every new generation uses fresh random noise. These listening examples do not hold the noise draw fixed between conditions. The extension adds six default outputs, forty editing step outputs and ten synthesis dropout outputs. A single four-GPU Jupiter job uses four independent processes, one per GPU, including SQ decoding.

## Model comparison — Synthesis (Table 1)

Eight author-selected crops appear in the Synthesis comparison tab:

- Slakh: 1884, 1892, 2081.
- MusicNet: 2382, 1819, 2298.
- URMP: 12, 13.

Slakh includes the original recording and SpanSynth-Edit output both with and without drums. External model outputs use the version without drums. Each version has its own input MIDI download and visualization, and each audio player follows the matching view. These are synthesis conditions, so differences between the two MIDI views are not marked as edits.

Every synthesis example includes SpanSynth-Edit Base SQ CFG 2, CTD, Spectrogram Diffusion, and U-MusT. TokenSynth is also included for Slakh, URMP, and MusicNet 2298. It is unavailable for the MusicNet 2382 and 1819 ensemble excerpts. MIDI-VALLE supports solo piano and does not apply to these selected synthesis examples.

## Model comparison — Edit

Eight Slakh examples appear in the Edit comparison tab. The selector groups them by the author-approved task names:

| Task group | Songs | Actual edit |
| --- | --- | --- |
| add new instrument | 2045, 2083, 1888, 1916 | Track insertion |
| note ins/del | 2045, 2083 | Track deletion |
| note ins/del | 1916 | Note insertion |
| note ins/del | 1888 | Note deletion |

The instruction and before/after MIDI retain the actual edit within each group. Each example includes Base SQ, FlowEdit, Spectrogram Diffusion, and U-MusT. CTD is included for the selected deletion and note-insertion examples but does not support adding a new instrument.

## AI-assisted edit

The selected POP909 crops are 025 (10 s), 024 (10 s), 328 (7 s), 019 (5 s), and 443 (10 s), in that order. Modulator, an external symbolic music generation model, inpaints a piano passage in each MIDI sequence. The audio models then render the revised MIDI. This task tests whether audio models can render externally generated symbolic edits while preserving the surrounding audio.

Model reference: K. Bhandari, M. Bizzarri, G. A. Wiggins, and S. Colton, “Change is Key: A generative framework for controllable musical modulations,” Hugging Face model repository, 2026. [Modulator](https://huggingface.co/keshavbhandari/modulator).

| POP909 song | Source excerpt (s) | MIDI inpainting length (s) | Audio generation interval in excerpt (s) |
| --- | --- | ---: | --- |
| 025 | 293.474–313.954 | 10 | 1.96–12.00 |
| 024 | 7.299–27.779 | 10 | 2.00–12.04 |
| 328 | 215.015–235.495 | 7 | 1.84–8.88 |
| 019 | 154.771–175.251 | 5 | 6.12–11.16 |
| 443 | 189.026–209.506 | 10 | 10.16–20.20 |

These are existing dry-piano crops. Before/after reference audio was rendered with FluidSynth and Salamander Grand Piano. The after-edit reference renders Modulator's edited MIDI, rather than a recorded performance. MIDI inpainting boundaries retain their original timing, while the audio generation intervals follow the existing 25 Hz latent-frame boundaries. The 328 example uses the genuine 7 s record and its generated outputs, replacing the previously published 5 s files.

Each crop includes Base SQ CFG 2, Base SQ CFG 2 FlowEdit, CTD, Spectrogram Diffusion, U-MusT, TokenSynth, and MIDI-VALLE.

## Bonus — Electric guitar

The Bonus tab contains four GOAT synthesis crops: clean electric guitar 103, followed by distortion/overdrive 152, 118, and 35. Each includes the original audio, input MIDI, SpanSynth-Edit Base SQ CFG 2, CTD, Spectrogram Diffusion, U-MusT, and TokenSynth. The source record for each distorted example retains its selected amplifier condition.

## Model and source conditions

All comparisons use the same crop within an example. Current Base SQ and FlowEdit outputs use the V3 SQ V8 checkpoint at step 127,750, CFG 2, and 64 steps by default; the Early editing dropdown exposes the stated step ablations. No GAN decoder output is used. Each external model card has an expandable paper reference with a direct link.

Most sources are in the existing research directory `runs/listening-survey-candidates/regenerated/records.jsonl`. Model files are joined by exact `record_id` in `outputs/<condition>/outputs.jsonl`, using `full_audio_path`. The selected conditions are `syn-base-sq`, `edit-base-sq`, `edit-base-sq-flowedit`, `ctd`, `specdiff`, `umust`, `tokensynth`, and `tokensynth-musicnet-solo` as applicable. Synthesis references and MIDI come from `inference_context.source_crop_audio` and `source_crop_midi`. Editing downloads copy the selected before/after MIDI, and the same parsed crop-relative notes are included in the page data.

Two conditions come from the existing full evaluations on Jupiter:

- Slakh with drums: `$PROJECT/amt/data/evaluation/spansynth_paired_eval_v1`, using `full_mix`, `spansynth-v3sqv8-127750-base-cfg2`, reference MIDI on, and target instrument on. The crop and synthesis interval match each corresponding version without drums.
- POP909 328: `$PROJECT/spansynth-edit-eval/results/spansynth_paired_edit_v1`, record `pop909--modulator--dry--target-7s--328`. Base SQ uses `spansynth-v3sqv8-127750-base-ordinary-cfg2`, and FlowEdit uses `spansynth-v3sqv8-127750-codes-cfg2`. External conditions are `ctd`, `specdiff`, `umust-prefix`, `tokensynth`, and `midi-valle`. Reference MIDI is on where supported, and target instrument is on.

Only the selected examples appear in navigation. Previously published assets remain in the existing assets directory.

## Comparison listening levels

Model comparison, AI-assisted edit, and Bonus audio follow the established procedure in `docs/spansynth-listening-survey.md` and `_survey_listening_gain` in `scripts/demo/generate_spansynth_v3_gallery.py`.

The original crop is peak-normalized to 0.95 once. Slakh drum variants are prepared against their respective original mixes, then share the same final attenuation across all players in the example. Each Base SQ and FlowEdit output receives one constant gain based on its source-active context RMS. Other systems retain the normalized original context and receive one target gain matched to the ground-truth target. The after-edit reference uses the before-edit audio’s gain. All players in a crop receive the same final attenuation when needed to keep decoded MP3 peaks within 0.98. The copies are mono, 48 kHz, 192 kbps MP3.

This is reference-assisted level matching for listening. No limiter, crossfade, time stretching, or generated note editing is applied. The original research files remain unchanged. Early demo outputs use the same source-active context RMS method. All conditions for an example are prepared together against one original recording and share any final headroom attenuation. Existing original listening levels are retained before that common attenuation. Originals needing re-encoding are read from the source WAV, avoiding a second lossy encoding of the published MP3. No generated target is independently level-matched to its reference. The raw research WAVs retain their generated levels.

## Files

- Root `index.html`: the abstract, project resources, model overview, and selected examples, including the exact MIDI note data used by the page.
- `demo/index.html`: redirect for previously shared demo URLs.
- `demo/styles.css`: responsive light and dark appearance.
- `demo/app.js`: audio controls, example navigation, and MIDI rendering.
- `demo/assets/`: selected MP3 and MIDI files plus the author-provided model overview SVG. Each audio or MIDI filename identifies the example and its condition or role.
- `demo/README.md`: running instructions and the sources and conditions needed to maintain the demo.

## Publication

The public project page is hosted at [mimbres.github.io/spansynth-edit/](https://mimbres.github.io/spansynth-edit/) with GitHub Pages, using the root of the `main` branch as its publishing source. Visiting this address keeps the short URL and selects the Kraisler Early editing example by default. Selecting another example adds its identifier to the URL for sharing.
