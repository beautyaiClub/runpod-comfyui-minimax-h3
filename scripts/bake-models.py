"""Download pinned H3 weights while building the image; validate size and SHA256."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def validate(entry, root, hash_file=True):
    path = root / entry["subdir"] / entry["filename"]
    if not path.is_file() or path.stat().st_size != entry["size"]:
        raise RuntimeError(f"Model missing or truncated: {path}")
    if hash_file:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != entry["sha256"]:
            raise RuntimeError(f"SHA256 mismatch: {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", default="/opt/serverless/models-lock.json")
    parser.add_argument("--root", default="/opt/comfyui-baked/models")
    parser.add_argument("--file")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--size-only", action="store_true")
    args = parser.parse_args()
    entries = json.loads(Path(args.lock).read_text())["models"]
    if args.file:
        entries = [entry for entry in entries if entry["filename"] == args.file]
    if not entries:
        raise ValueError("No matching model in lock file")
    root = Path(args.root)
    for entry in entries:
        if not args.verify_only:
            stage = root / ".download" / entry["filename"]
            stage.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "HF_HUB_DISABLE_XET": "1", "HF_HUB_DOWNLOAD_TIMEOUT": "60"}
            env.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
            cmd = ["hf", "download", entry["repo"], entry["path"], "--revision", entry["revision"], "--local-dir", str(stage)]
            print(f"Downloading {entry['filename']} ({entry['size']} bytes)", flush=True)
            process = subprocess.Popen(cmd, env=env)
            while process.poll() is None:
                time.sleep(15)
                partials = list(stage.rglob("*.incomplete"))
                received = sum(item.stat().st_size for item in partials if item.exists())
                print(f"Transfer {entry['filename']}: {received}/{entry['size']} bytes", flush=True)
            if process.returncode:
                raise RuntimeError(f"hf download failed ({process.returncode}): {entry['filename']}")
            target = root / entry["subdir"] / entry["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / entry["path"], target)
            shutil.rmtree(stage)
            stage.parent.rmdir()
        path = validate(entry, root, hash_file=not args.size_only)
        print(f"MODEL_OK {path.name} size={entry['size']} sha256={entry['sha256']}", flush=True)
    print(f"MODELS_OK files={len(entries)} bytes={sum(entry['size'] for entry in entries)}", flush=True)


if __name__ == "__main__":
    main()
