# SpanSynth-Edit for ComfyUI

Upload audio → transcribe with YourMT3+ → edit notes → generate audio.

ComfyUI runs in your browser. Its boxes are called **nodes**; a **workflow** connects them. Generation uses your Mac's Apple GPU or a remote server's NVIDIA GPU. YourMT3+ transcription uses an online HF Space.

## 1. Install once

<details>
<summary><strong>Show installation commands</strong> (skip if already installed)</summary>

Requires [Git](https://git-scm.com/downloads) and [Conda / Miniforge](https://github.com/conda-forge/miniforge#install). Open **Terminal** on Mac, or connect to your server with `ssh USER@SERVER` (replace with your SSH login). Install on the computer that will generate audio:

```bash
conda create -n spansynth-edit-comfyui python=3.12 -y
conda activate spansynth-edit-comfyui
cd ~
git clone --depth 1 --branch v0.37.0 https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI
```

**NVIDIA server only:** install CUDA PyTorch first. Skip this command on Mac.

```bash
python -m pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130
```

**Both Mac and server:** install ComfyUI and the SpanSynth nodes:

```bash
python -m pip install -r requirements.txt
git clone --depth 1 --filter=blob:none --sparse https://github.com/mimbres/spansynth-edit.git custom_nodes/spansynth-edit
git -C custom_nodes/spansynth-edit sparse-checkout set spansynth app comfyui
python -m pip install './custom_nodes/spansynth-edit[comfyui]'
```

These examples use `~/ComfyUI`. On managed servers, use your project's storage for ComfyUI, its environment, and the `HF_HOME` cache. [Other systems](https://docs.comfy.org/installation/manual_install).

</details>

## 2. Start ComfyUI

### A. On your Mac

In your Mac's **Terminal**:

```bash
conda activate spansynth-edit-comfyui
cd ~/ComfyUI
python main.py --listen 127.0.0.1 --port 8188
```

Keep Terminal open. Visit **[localhost:8188](http://127.0.0.1:8188)**, then follow step 3.

### B. On a remote GPU server

In a **new laptop Terminal**, connect:

```bash
ssh -L 8189:127.0.0.1:8188 USER@SERVER
```

Then start ComfyUI **on the server**, in the same terminal:

```bash
conda activate spansynth-edit-comfyui
cd ~/ComfyUI
python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch
```

Keep SSH open. Visit **[localhost:8189](http://127.0.0.1:8189)** on your **laptop**. Port 8189 keeps this separate from local ComfyUI. Uploads go to the server; downloads return to your laptop.

## 3. Edit your first clip

1. **Load the workflow.** [Save this file as example.json](https://raw.githubusercontent.com/mimbres/spansynth-edit/main/comfyui/example.json) on your laptop, then drag it onto ComfyUI's canvas. Seven connected nodes appear.
2. **Choose audio.** Upload a recording in **Load Audio**. Leave the defaults to use its first 20.48 seconds.
3. **Edit notes.** Click **Prepare / Open score** in **Edit Score**. After transcription, select a track and move or draw notes.
4. **Generate.** Click **Apply edits**, wait for confirmation, then **Close** → **Run**. Keep defaults: device **auto**, **16** steps, CFG **2.0**. Weights download on first generation.
5. **Listen.** Reopen the score and choose **Generated**. Edit and run again; each generation uses the original recording.

The region follows changed notes. **Download latest WAV** in **Generate** and **Download MIDI** in the editor save to your laptop. Save the workflow from ComfyUI's menu to retain edits; keep the input audio too. Automatic audio saves go to `ComfyUI/output/audio` on the computer running ComfyUI.

**Stop:** press **Ctrl+C** in the ComfyUI terminal. Next time, repeat only step 2.

<details>
<summary><strong>Other inputs and common fixes</strong></summary>

- **Your own MIDI:** replace **Transcribe with YourMT3+** with **Load MIDI · SpanSynth-Edit**, upload MIDI, and connect both `source_midi` inputs.
- **Draw a score:** disconnect both `source_midi` inputs. For synthesis without a recording, also disconnect audio from **Prepare Audio Clip** and set crop start to zero.
- **FlowEdit:** choose **spansynth-edit + flowedit** in **Generate**, with original MIDI connected.
- **New audio or crop:** click **Reset score from inputs** before editing again.
- **Transcription quota:** stop ComfyUI, run `hf auth login` in its environment, then restart. For remote use, log in on the server.
- **Missing nodes:** verify installation in the ComfyUI environment, restart, and refresh the browser.

GM preview is an instrument-sample preview, not the generated timbre. See [requirements](../README.md#system-requirements), [licenses](../README.md#license), and [attribution](../NOTICE).

</details>
