"""Publish the web app and shared inference package to the established HF Space."""
from pathlib import Path
import tomllib

from huggingface_hub import CommitOperationAdd, HfApi, SpaceHardware


def main():
    root = Path(__file__).resolve().parents[1]
    repo = "mimbres/spansynth-edit"
    api = HfApi()
    api.create_repo(repo, repo_type="space", space_sdk="gradio", exist_ok=True,
                    space_hardware=SpaceHardware.ZERO_A10G)
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
