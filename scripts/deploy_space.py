"""Publish the web app and shared inference package to the established HF Space."""
from pathlib import Path
import tomllib

from huggingface_hub import CommitOperationAdd, HfApi, SpaceHardware, get_token


GALLERY_README = """---
pretty_name: SpanSynth-Edit Gallery
task_categories:
- audio-to-audio
tags:
- music
- midi
- spansynth-edit
---

# SpanSynth-Edit Gallery

Finished MIDI-guided music edits made with [SpanSynth-Edit](https://huggingface.co/spaces/mimbres/spansynth-edit).
Open a work's shared link to compare the original and edited audio, explore its score, and make an editable copy.

Each work has its own folder:

- `input.wav` and `output.wav`: the original clip and saved generated audio, at 48 kHz mono.
- `original.mid` and `edited.mid`: the source and edited score, aligned to the clip.
- `context.wav`: the normalized audio used by the model, including the history before the clip.
- `project.json`: the title, description, notes, editing region, generation settings, and piano-roll display settings.

Shared links have the form `https://mimbres-spansynth-edit.hf.space/?work=FOLDER_NAME`.
An unlisted work is still public here. Source recordings retain their original rights; publication does not grant a new license to them.
Publishing is curated by `mimbres`.
"""


def main():
    root = Path(__file__).resolve().parents[1]
    repo = "mimbres/spansynth-edit"
    api = HfApi()
    api.create_repo(repo, repo_type="space", space_sdk="gradio", exist_ok=True,
                    space_hardware=SpaceHardware.ZERO_A10G)
    gallery = "mimbres/spansynth-edit-gallery"
    api.create_repo(gallery, repo_type="dataset", private=False, exist_ok=True)
    if not api.file_exists(gallery, "README.md", repo_type="dataset"):
        api.upload_file(repo_id=gallery, repo_type="dataset", path_in_repo="README.md",
                        path_or_fileobj=GALLERY_README.encode(), commit_message="Introduce the SpanSynth-Edit gallery")
    token = get_token()
    if not token:
        raise RuntimeError("Log in to Hugging Face with gallery write access before deploying.")
    api.add_space_secret(repo, "SPANSYNTH_GALLERY_TOKEN", token)
    # Spaces installs requirements before copying the application source.
    dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    requirements = (root / "app/requirements.txt").read_text().replace("\n.\n", "\n" + "\n".join(dependencies) + "\n")
    files = [(root / "app/README.md", "README.md")]
    files.extend((root / name, name) for name in ("pyproject.toml", "LICENSE", "NOTICE"))
    files.extend((path, path.relative_to(root).as_posix()) for path in sorted((root / "spansynth").rglob("*.py")))
    files.extend((root / "app" / name, "app/" + name) for name in ("app.py", "editor.js", "style.css"))
    api.create_commit(repo_id=repo, repo_type="space",
                      operations=[CommitOperationAdd(path_in_repo=remote, path_or_fileobj=local)
                                  for local, remote in files] +
                                 [CommitOperationAdd(path_in_repo="requirements.txt", path_or_fileobj=requirements.encode())],
                      commit_message="Update SpanSynth-Edit interactive demo")
    runtime = api.get_space_runtime(repo)
    if runtime.requested_hardware != SpaceHardware.ZERO_A10G:
        api.request_space_hardware(repo, hardware=SpaceHardware.ZERO_A10G)
    print(f"Published https://huggingface.co/spaces/{repo}")


if __name__ == "__main__":
    main()
