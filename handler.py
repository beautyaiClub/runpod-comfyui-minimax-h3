"""RunPod serverless handler for the MiniMax-H3 ComfyUI stack.

Boots ComfyUI once per worker, then each job picks a workflow template by name,
patches its widgets (prompt / camera_tag / duration / unet config), uploads any
input media, queues the prompt, and returns the produced media as base64.

Job input:
  {
    "workflow": "MiniMaxH3-Native-FL2VA.json",     # file in $WORKFLOWS_DIR/<template_set>
    "template_set": "native|enhancers",            # optional, default $H3_TEMPLATE_SET (native)
    "prompt":   "...",                             # enhancer/directive text
    "config":   "native|native_turbo|r2va_native|r2va_native_turbo|10eros|10eros_turbo",
    "camera_tag": "[ORBIT]",                       # optional
    "seconds":  6,                                 # optional duration override
    "images":   ["https://... or base64"],         # optional, fills LoadImage*(FromUrl) nodes in id order
    "video":    "https://... or base64",           # optional, fills VHS_LoadVideo
    "videos":   ["https://..."], "audios": ["https://..."]   # optional, ref2va reference media
  }

Template sets (subdirectories of WORKFLOWS_DIR, see README):
  native     graphs built by myreels-h3-adapter, core ComfyUI nodes + the three
             URL loaders only — no enhancement nodes involved
  enhancers  the reference project's templates (QwenVL prompt enhancer, RIFE /
             TensorRT upscale, face detailer, wildcards)
"""

import asyncio
import base64
import glob
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

import runpod

COMFY_DIR = os.environ.get("COMFYUI_DIR", "/workspace/runpod-slim/ComfyUI")
COMFY_URL = "http://127.0.0.1:8188"
WORKFLOWS_DIR = Path(os.environ.get("WORKFLOWS_DIR", "/opt/workflows"))
TEMPLATE_SETS = ("native", "enhancers")
DEFAULT_TEMPLATE_SET = os.environ.get("H3_TEMPLATE_SET", "native").strip().lower() or "native"
JOB_TIMEOUT_S = int(os.environ.get("JOB_TIMEOUT_S", "1800"))

# Output delivery: base64 inline under these limits, S3 offload above them.
# RunPod's job-done callback drops results over ~20MB, so keep headroom.
INLINE_MAX = int(os.environ.get("OUTPUT_INLINE_MAX_MB", "10")) * 1024 * 1024
INLINE_BUDGET = int(os.environ.get("OUTPUT_INLINE_BUDGET_MB", "15")) * 1024 * 1024
S3_PREFIX = os.environ.get("S3_OUTPUT_PREFIX", "outputs")

# Config → unet filename needle + sampler values (mirrors chat_service.py _MINIMAX_CONFIGS)
MINIMAX_CONFIGS = {
    "native":            {"unet": "fl2va",  "steps": 20, "sampler": "res_multistep", "scheduler": "simple", "shift_video": 12.0, "shift_audio": 3.0, "lora": None, "tau": None},
    "native_turbo":      {"unet": "fl2va",  "steps": 8,  "sampler": "euler",         "scheduler": "simple", "shift_video": 6.0,  "shift_audio": 3.0, "lora": "fl2v_turbo",  "tau": 1.3},
    "r2va_singularity":       {"unet": "Singularity", "steps": 20, "sampler": "res_multistep", "scheduler": "simple", "lora": None, "tau": None, "sigma_off": True},
    "r2va_singularity_turbo": {"unet": "Singularity", "steps": 6,  "sampler": "euler",         "scheduler": "beta",   "lora": "ref2v_turbo_4step", "tau": 1.3, "sigma_off": True},
    "10eros":            {"unet": "10eros", "steps": 20, "sampler": "res_multistep", "scheduler": "simple", "shift_video": 12.0, "shift_audio": 3.0, "lora": None, "tau": None},
    "10eros_turbo":      {"unet": "10eros", "steps": 8,  "sampler": "euler",         "scheduler": "simple", "shift_video": 6.0,  "shift_audio": 3.0, "lora": "fusion_turbo", "tau": 1.3},
}


def _http(path, payload=None, timeout=30):
    url = COMFY_URL + path
    try:
        if payload is None:
            return urllib.request.urlopen(url, timeout=timeout).read()
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:2000]
        raise RuntimeError(f"ComfyUI {path} → HTTP {e.code}: {body}") from e


_BOOT_DONE = threading.Event()
_BOOT_ERR = None


def _boot_comfy():
    # Extra flags come from COMFYUI_EXTRA_ARGS (space-separated). Keep the
    # default empty: --fast/--use-sage-attention/--async-offload corrupt the
    # int8-convrot Qwen/MMh3 stacks (flat garbage output).
    cmd = [
        "python3", "main.py",
        "--listen", "127.0.0.1", "--port", "8188",
        "--disable-auto-launch",
    ] + os.environ.get("COMFYUI_EXTRA_ARGS", "").split()
    comfy_log = open("/workspace/comfy.log", "ab", buffering=0)
    proc = subprocess.Popen(cmd, cwd=COMFY_DIR, stdout=comfy_log, stderr=subprocess.STDOUT)
    deadline = time.time() + 600
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("ComfyUI exited during boot")
        try:
            _http("/system_stats", timeout=5)
            return proc
        except Exception:
            time.sleep(2)
    raise RuntimeError("ComfyUI did not become ready in 600s")


def _boot_in_bg():
    # ComfyUI boots on a daemon thread so the runpod handler is live (and the
    # `health` action answers) even while ComfyUI is still starting — or on
    # machines where it can't start at all (e.g. Hub test pods without the
    # network volume). Real jobs wait on _BOOT_DONE via _ensure_comfy().
    global _BOOT_ERR
    try:
        _boot_comfy()
    except Exception as exc:
        _BOOT_ERR = exc
        print(f"[handler] ComfyUI boot failed: {exc}", flush=True)
    finally:
        _BOOT_DONE.set()


async def _ensure_comfy():
    await asyncio.to_thread(_BOOT_DONE.wait)
    if _BOOT_ERR is not None:
        raise RuntimeError(f"ComfyUI failed to boot: {_BOOT_ERR} (see /workspace/comfy.log)")


def _media_bytes(value):
    if isinstance(value, str) and value.startswith("http"):
        return urllib.request.urlopen(value, timeout=120).read()
    return base64.b64decode(value)


def _as_list(value):
    """Accept a list, a comma/space separated string, or a single URL.

    Callers differ: a raw API call passes a JSON array, while a prompt resource
    may substitute a single string or an already-joined list.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if str(v).strip()]
        except Exception:
            pass
    parts = [p.strip() for p in re.split(r"[,\n]", text) if p.strip()]
    return parts or [text]


def _upload(filename, data):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        COMFY_URL + "/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    return resp.get("name", filename)


def _template_set_dir(template_set):
    return WORKFLOWS_DIR / template_set


def _set_workflows(template_set):
    """Templates of one set; falls back to a flat WORKFLOWS_DIR (legacy layout)."""
    directory = _template_set_dir(template_set)
    if directory.is_dir():
        return sorted(p.name for p in directory.glob("*.json"))
    if template_set == DEFAULT_TEMPLATE_SET:
        return sorted(p.name for p in WORKFLOWS_DIR.glob("*.json"))
    return []


def _describe_template_sets():
    sets = {name: _set_workflows(name) for name in TEMPLATE_SETS}
    sets = {name: files for name, files in sets.items() if files}
    if not sets:  # flat layout: single anonymous set
        sets = {"": sorted(p.name for p in WORKFLOWS_DIR.glob("*.json"))}
    return sets


def _auto_workflow(job):
    """Pick a template from the job inputs.

    Prompt resources should not ask the user which template file to run: the
    channel follows from the media (frames vs reference material) and the single
    `enhance` switch. Mirrors the LAN adapter's naming so the same job body works
    against both backends.
    """
    enhance = bool(job.get("enhance"))
    refs = bool(_as_list(job.get("images")) or _as_list(job.get("videos")) or _as_list(job.get("audios")))
    has_last = bool(job.get("image2"))
    if enhance:
        if refs:
            return "MiniMaxH3-Singularity-R2VA.json" if _as_list(job.get("videos")) else "MiniMaxH3-Turbo-R2VA.json"
        if has_last:
            return "MiniMaxH3-Turbo-FL2VA.json"
        if job.get("image1"):
            return "MiniMaxH3-Turbo-I2VA.json"
        return "MiniMaxH3-Turbo-T2VA.json"
    return "MiniMaxH3-Native-Ref2VA.json" if refs else "MiniMaxH3-Native-FL2VA.json"


def _auto_config(job):
    """Sampling preset for a job that did not pin one.

    Users should not have to pick a preset: the turbo recipes are what the LAN
    adapter runs by default (8 steps + lightx2v turbo LoRA), and the Singularity
    fusion model is only the right unet for the reference-material channel.
    """
    if job.get("enhance"):
        refs = bool(_as_list(job.get("images")) or _as_list(job.get("videos")) or _as_list(job.get("audios")))
        if refs:
            return "r2va_singularity_turbo"
    return "native_turbo"


# ---- MiniMax H3 canvas + frame rules (mirrors myreels-h3-adapter/src/comfy.js) ----
SIZE_STEP = 32
H3_MAX_PIXELS = 768 * 1344
H3_MIN_RATIO = 1 / 4
H3_MAX_RATIO = 4
H3_RATIOS = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:4": 3 / 4, "9:16": 9 / 16}
# tier -> (short edge, long edge), the官方降采样阶梯
SIZE_TIERS = {"768p": (768, 1344), "720p": (768, 1344), "576p": (576, 1024), "480p": (480, 832), "352p": (352, 640)}
DEFAULT_TIER = "480p"


def _round_step(value):
    return max(SIZE_STEP, int(value / SIZE_STEP + 0.5) * SIZE_STEP)


def _size_for_tier(ratio, tier=DEFAULT_TIER):
    short_edge, long_edge = SIZE_TIERS.get(tier, SIZE_TIERS[DEFAULT_TIER])
    r = min(max(ratio, H3_MIN_RATIO), H3_MAX_RATIO) if ratio and ratio > 0 else 16 / 9
    if r >= 1:
        height, width = short_edge, _round_step(short_edge * r)
        if width > long_edge:
            width = long_edge
            height = _round_step(width / r)
    else:
        width, height = short_edge, _round_step(short_edge / r)
        if height > long_edge:
            height = long_edge
            width = _round_step(height * r)
    return {"width": _round_step(width), "height": _round_step(height)}


def _tier_from_pixel(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return min(SIZE_TIERS, key=lambda name: abs(SIZE_TIERS[name][0] - n))


def _h3_size(job):
    """Resolution/aspect_ratio (or explicit width/height) -> the canvas H3 accepts."""
    ratio = H3_RATIOS.get(str(job.get("aspect_ratio") or "").strip())
    tier = str(job.get("resolution") or "").strip().lower() or None
    if tier and tier not in SIZE_TIERS:
        digits = re.match(r"^(\d{3,4})p?$", tier)
        tier = _tier_from_pixel(digits.group(1)) if digits else None
    width_hint = int(job.get("width") or 0)
    height_hint = int(job.get("height") or 0)
    if width_hint > 0 and height_hint > 0:
        box = _size_for_tier(width_hint / height_hint, "720p") if (
            width_hint * height_hint > H3_MAX_PIXELS
            or max(width_hint, height_hint) > SIZE_TIERS["720p"][1]
            or min(width_hint, height_hint) > SIZE_TIERS["720p"][0]) else {"width": width_hint, "height": height_hint}
        return {"width": _round_step(box["width"]), "height": _round_step(box["height"])}
    if tier or ratio:
        return _size_for_tier(ratio, tier or DEFAULT_TIER)
    return None


def _frame_count(job):
    """Seconds -> H3 frame count on the 17-grid (4n+1), same as the adapter."""
    if job.get("length"):
        return max(5, min(int(job["length"]), 3600))
    raw = job.get("seconds", job.get("duration_seconds"))
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    frames = int(round((seconds * 24) / 17)) * 17 + 5
    return max(5, min(frames, 3600))


def _load_workflow(name, template_set=None):
    """Workflow templates must be saved in ComfyUI *API format* (Export API),
    where every node is keyed by id and inputs carry widget names — the UI
    format's positional widgets_values can't be patched reliably."""
    template_set = (template_set or DEFAULT_TEMPLATE_SET).strip().lower()
    if template_set not in TEMPLATE_SETS:
        raise RuntimeError(f"unknown template_set '{template_set}' (known: {', '.join(TEMPLATE_SETS)})")
    set_dir = _template_set_dir(template_set)
    candidates = [set_dir / name, set_dir / f"{name}.json", WORKFLOWS_DIR / name, WORKFLOWS_DIR / f"{name}.json"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        # A caller (e.g. a prompt resource that only picks a template file) does
        # not have to know the set layout: resolve the name across the other sets
        # before failing.
        for other in TEMPLATE_SETS:
            if other == template_set:
                continue
            other_dir = _template_set_dir(other)
            for candidate in (other_dir / name, other_dir / f"{name}.json"):
                if candidate.is_file():
                    print(f"[handler] template {name} served from set '{other}' "
                          f"(requested '{template_set}')", flush=True)
                    path = candidate
                    break
            if path is not None:
                break
    if path is None:
        elsewhere = {s: files for s, files in _describe_template_sets().items() if name in files or f"{name}.json" in files}
        hint = f" — it is in set(s) {', '.join(elsewhere)}" if elsewhere else ""
        raise RuntimeError(
            f"workflow template not found: {name} in set '{template_set}'{hint} "
            f"(set '{template_set}': {sorted(_set_workflows(template_set))})")
    wf = json.loads(path.read_text())
    if "nodes" in wf:
        raise RuntimeError(f"{name} is a UI-format graph; re-export it with 'Export API' so widgets are named")
    return wf


def _patch(prompt, job):
    preset = job.get("config") or _auto_config(job)
    if not job.get("config"):
        print(f"[handler] sampling preset auto-selected: {preset}", flush=True)
    cfg = MINIMAX_CONFIGS.get(preset)
    base = re.sub(r"[^\w.-]+", "-", str(job.get("job_name") or f"job_{uuid.uuid4().hex[:8]}")).strip("-")
    # Frame slots and reference images are mutually exclusive inputs, so the
    # loader slots are filled from whichever the caller supplied: 首帧/尾帧 go in
    # first (they are the first/last frame slots), otherwise the reference list.
    raw_images = _as_list(job.get("images"))
    if not raw_images:
        raw_images = _as_list(job.get("image1")) + _as_list(job.get("image2"))
    images = [_upload(f"{base}_{i}.png", _media_bytes(v)) for i, v in enumerate(raw_images)]
    raw_videos = _as_list(job.get("videos")) or (_as_list(job.get("video")) if job.get("video") else [])
    raw_audios = _as_list(job.get("audios"))
    video_name = _upload(f"{base}.mp4", _media_bytes(raw_videos[0])) if raw_videos else None

    def _remote(value, fallback):
        """URL loaders fetch the media themselves — only http(s)/data URLs fit."""
        return value if value.startswith(("http://", "https://", "data:")) else fallback

    image_values = [_remote(v, images[i]) for i, v in enumerate(raw_images)]
    video_urls = [v for v in raw_videos if v.startswith(("http://", "https://"))]
    audio_urls = [v for v in raw_audios if v.startswith(("http://", "https://"))]

    def _find_file(kind, needle):
        """Locate a model file under the volume + local model dirs."""
        hits = sorted(
            h for pat in (f"/runpod-volume/models/{kind}/**/*", f"{COMFY_DIR}/models/{kind}/**/*")
            for h in glob.glob(pat, recursive=True)
            if needle.lower() in Path(h).name.lower())
        # "fl2va"/"ref2va" needles must not grab 10Eros files which also contain them
        if needle.lower() in ("fl2va", "ref2va"):
            hits = [h for h in hits if "eros" not in Path(h).name.lower()] or hits
        return Path(hits[0]).name if hits else None

    def _model_exists(kind, name):
        return any(
            Path(h).name == name
            for pat in (f"/runpod-volume/models/{kind}/*", f"{COMFY_DIR}/models/{kind}/*")
            for h in glob.glob(pat))

    canvas = _h3_size(job)
    frames = _frame_count(job)
    if canvas:
        print(f"[handler] canvas {canvas['width']}x{canvas['height']} (resolution={job.get('resolution')!r} aspect_ratio={job.get('aspect_ratio')!r})", flush=True)
    if frames:
        print(f"[handler] frames {frames} ({frames / 24:.1f}s)", flush=True)

    img_idx = 0
    vid_idx = 0
    aud_idx = 0
    filled_loaders = set()
    drop_lora = None
    sigma_off = {}
    for nid, node in prompt.items():
        ct = node.get("class_type", "")
        inp = node.get("inputs", {})

        # URL loaders first: LoadImageFromUrl also matches the "LoadImage" test below.
        if ct == "LoadImageFromUrl":
            if img_idx < len(image_values):
                inp["image"] = image_values[img_idx]
                filled_loaders.add(nid)
                img_idx += 1
        elif ct == "LoadVideoFromURL":
            if vid_idx < len(video_urls):
                inp["url"] = video_urls[vid_idx]
                filled_loaders.add(nid)
                vid_idx += 1
        elif ct == "BeautyAI_LoadAudioFromURL":
            if aud_idx < len(audio_urls):
                inp["url"] = audio_urls[aud_idx]
                filled_loaders.add(nid)
                aud_idx += 1
        elif "LoadImage" in ct:
            if img_idx < len(images):
                inp["image"] = images[img_idx]
                if "LoadImageMiniState" in inp:
                    inp["LoadImageMiniState"] = images[img_idx]
                if "LoadImagePixState" in inp:
                    try:
                        state = json.loads(inp["LoadImagePixState"])
                        state["orig_name"] = images[img_idx]
                        inp["LoadImagePixState"] = json.dumps(state)
                    except Exception:
                        inp.pop("LoadImagePixState", None)
                filled_loaders.add(nid)
                img_idx += 1
        elif "LoadVideo" in ct and video_name:
            inp["video"] = video_name
            filled_loaders.add(nid)
        # Template weights are defaults: if the volume carries a different file
        # name than the template (fresh volume, swapped encoder), fall back to the
        # same needle search the config presets use.
        if cfg is None and "unet_name" in inp and not _model_exists("diffusion_models", inp["unet_name"]) \
                and not _model_exists("unet", inp["unet_name"]):
            found = _find_file("diffusion_models", "fl2va") or _find_file("unet", "fl2va")
            if found:
                inp["unet_name"] = found
        if "clip_name" in inp and not _model_exists("text_encoders", inp["clip_name"]) \
                and not _model_exists("clip", inp["clip_name"]):
            found = _find_file("text_encoders", "qwen3vl") or _find_file("clip", "qwen3vl")
            if found:
                inp["clip_name"] = found
        if "vae_name" in inp and not _model_exists("vae", inp["vae_name"]):
            needle = "audio_vae" if "audio" in str(inp["vae_name"]).lower() else "video_vae"
            found = _find_file("vae", needle)  # keep the needle specific: a bare
            # "vae" match would also catch placeholder files in an empty models dir
            if found:
                inp["vae_name"] = found
        if job.get("prompt") is not None and "prompt" in inp and not isinstance(inp["prompt"], list):
            inp["prompt"] = job["prompt"]
        if job.get("prompt") is not None and ct == "WildcardProcessor":
            inp["text"] = job["prompt"]
            inp["populated_text"] = job["prompt"]
        if job.get("camera_tag") and "camera_tag" in inp:
            inp["camera_tag"] = job["camera_tag"]
        if job.get("vl_preset"):
            for wkey in ("preset_prompt", "enhancement_style"):
                if wkey in inp:
                    inp[wkey] = job["vl_preset"]
        if job.get("enhance") is False and "passthrough" in inp:
            inp["passthrough"] = True
        if "duration" in inp and (job.get("seconds") or job.get("duration_seconds")):
            inp["duration"] = f"{int(float(job.get('seconds') or job['duration_seconds']))}s"
        elif frames and "length" in inp and not isinstance(inp["length"], list):
            inp["length"] = frames
        elif job.get("seconds") and "value" in inp and ct.startswith("Primitive"):
            inp["value"] = int(float(job["seconds"]))
        # H3 canvas: the conditioning nodes carry width/height widgets.
        if canvas and ct in ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"):
            if isinstance(inp.get("width"), int):
                inp["width"] = canvas["width"]
            if isinstance(inp.get("height"), int):
                inp["height"] = canvas["height"]
        if "lora_name" in inp:
            cur_lora = str(inp.get("lora_name", "")).lower()
            if "realism" in cur_lora or "character_swap" in cur_lora:
                # Style LoRA slot (Singularity recipe): job.lora =
                # none | realism | char_swap. Disabled/missing → drop the
                # node entirely below (ComfyUI validates lora_name even at
                # strength 0), so mark it for removal.
                sel = job.get("lora") or "none"
                drop_lora = nid
                if sel != "none":
                    needle = {"realism": "realism", "char_swap": "character_swap"}.get(sel, sel)
                    found = _find_file("loras", needle)
                    if not found:
                        raise RuntimeError(f"Style LoRA '{sel}' not found on the volume")
                    inp["lora_name"] = found
                    for skey in ("strength_model", "strength_clip"):
                        if skey in inp:
                            inp[skey] = 1.0
            elif cfg is not None:
                if cfg["lora"]:
                    found = _find_file("loras", cfg["lora"])
                    if found:
                        inp["lora_name"] = found
                        for skey in ("strength_model", "strength_clip"):
                            if skey in inp:
                                inp[skey] = 1.0
                else:
                    for skey in ("strength_model", "strength_clip"):
                        if skey in inp:
                            inp[skey] = 0.0
        elif cfg is not None and "unet_name" in inp:  # MiniMax H3 generator/enhancer node
            needle = cfg["unet"]
            if needle.lower() not in str(inp.get("unet_name", "")).lower():
                found = _find_file("unet", needle) or _find_file("diffusion_models", needle)
                if found:
                    inp["unet_name"] = found
            for wkey, ckey in (("steps", "steps"), ("sampler_name", "sampler"), ("scheduler", "scheduler"),
                               ("shift_video", "shift_video"), ("shift_audio", "shift_audio")):
                if wkey in inp and not isinstance(inp[wkey], list):
                    inp[wkey] = cfg[ckey]
            if cfg.get("tau") and "tau" in inp:
                inp["tau"] = cfg["tau"]
        if cfg is not None:
            # Sampler/step/shift values live on dedicated nodes, not on the
            # unet loader — patch them by class_type or the preset would only
            # swap weights/LoRA while the template settings silently win.
            if ct == "KSamplerSelect" and "sampler_name" in inp:
                inp["sampler_name"] = cfg["sampler"]
            elif ct == "BasicScheduler":
                for wkey in ("steps", "scheduler"):
                    if wkey in inp:
                        inp[wkey] = cfg[wkey]
            elif ct == "MiniMaxH3SigmaShift":
                if cfg.get("sigma_off"):
                    # API format ignores UI bypass modes — physically remove
                    # the node and rewire its consumers to its model source.
                    sigma_off[nid] = inp.get("model")
                else:
                    for wkey in ("shift_video", "shift_audio"):
                        if wkey in inp:
                            inp[wkey] = cfg[wkey]
    if sigma_off:
        for nid in sigma_off:
            del prompt[nid]
        for n in prompt.values():
            for k, v in n.get("inputs", {}).items():
                if isinstance(v, list) and v and v[0] in sigma_off:
                    n["inputs"][k] = sigma_off[v[0]]
    # Media loaders the job did not fill keep their template placeholder — a URL
    # that does not exist, or a filename that was never uploaded — and would fail
    # validation. Native ref2va templates ship the maximum number of slots
    # (9 images / 3 videos / 3 audios), so the unused ones are dropped here and
    # their consumers unlinked.
    dead = [nid for nid, n in prompt.items()
            if nid not in filled_loaders
            and any(k in n.get("class_type", "") for k in ("LoadImage", "LoadVideo", "LoadAudio"))]
    if dead:
        while dead:
            for nid in dead:
                del prompt[nid]
            emptied = []
            for nid, n in prompt.items():
                for k in [k for k, v in n.get("inputs", {}).items()
                          if isinstance(v, list) and v and v[0] in dead]:
                    del n["inputs"][k]
                if not n["inputs"]:
                    emptied.append(nid)
            # Cascade: nodes left with zero inputs after unlinking (e.g. a
            # PixaromaImageInfo whose only input was a dropped loader) would
            # fail validation on required inputs — drop them too.
            dead = emptied
    if drop_lora is not None:
        # Rewire the dropped style LoRA's consumers to its model source.
        src = prompt[drop_lora].get("inputs", {}).get("model")
        del prompt[drop_lora]
        if isinstance(src, list):
            for n in prompt.values():
                for k, v in n.get("inputs", {}).items():
                    if isinstance(v, list) and v and v[0] == drop_lora:
                        n["inputs"][k] = src
    # Post-processing toggles: the graph chain is  save.images <- rife.frames
    # <- upscale.images <- VAEDecode.  upscale=false rewires rife.frames to the
    # decoder output; rife=false rewires save.images to rife's own source.
    upscaler = next((n for n in prompt.values() if "upscaler_trt_model" in n.get("inputs", {})), None)
    rife = next((n for n in prompt.values() if "rife_trt_model" in n.get("inputs", {})), None)
    saver = next((n for n in prompt.values()
                  if isinstance(n.get("inputs", {}).get("images"), list)
                  and n["inputs"]["images"]
                  and rife is not None
                  and n["inputs"]["images"][0] == next(k for k, v in prompt.items() if v is rife)), None) if rife else None
    if not job.get("upscale", True) and upscaler is not None and rife is not None:
        rife["inputs"]["frames"] = upscaler["inputs"]["images"]
    if not job.get("rife", True) and rife is not None and saver is not None:
        saver["inputs"]["images"] = rife["inputs"]["frames"]
        if "frame_rate" in saver["inputs"]:
            saver["inputs"]["frame_rate"] = saver["inputs"]["frame_rate"] // int(rife["inputs"].get("multiplier", 2) or 2)
    # Upscaler model / target overrides — the loader node feeds "upscaler_trt_model".
    upscaler_loader = next((n for n in prompt.values()
                            if n.get("class_type", "").endswith("LoadUpscalerTensorrtModel")), None)
    if upscaler_loader is not None and job.get("upscale_model"):
        upscaler_loader["inputs"]["model"] = job["upscale_model"]
    if upscaler is not None and job.get("upscale_target"):
        upscaler["inputs"]["resize_to"] = job["upscale_target"]
    # Output size: "1344x768" rewrites the size/latent state and syncs the
    # Qwen encoder's reference resolution to the long edge. The new Pixaroma
    # helper nodes (Sizes/Dropdown) replace the old PixaromaResolution node.
    # Dropdowns feeding a TextEncodeQwenImage21 "resolution" input only —
    # other PixaromaDropdown nodes may control unrelated widgets.
    enc_ids = {nid for nid, n in prompt.items()
               if n.get("class_type", "").startswith("TextEncodeQwenImage21")}
    dropdown_ids = set()
    for nid in enc_ids:
        res = prompt[nid].get("inputs", {}).get("resolution")
        if isinstance(res, list) and res and str(res[0]) in prompt:
            dropdown_ids.add(str(res[0]))
    m = re.match(r"(\d+)\s*x\s*(\d+)", str(job.get("image_size") or ""))
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        for nid, node in prompt.items():
            ct = node.get("class_type", "")
            inp = node.get("inputs", {})
            if ct == "PixaromaResolution":
                try:
                    state = json.loads(inp.get("ResolutionState") or "{}")
                except Exception:
                    state = {}
                state["w"], state["h"] = w, h
                inp["ResolutionState"] = json.dumps(state)
            elif ct == "PixaromaSizes":
                try:
                    state = json.loads(inp.get("SizesState") or "{}")
                except Exception:
                    state = {}
                state["w"], state["h"] = w, h
                inp["SizesState"] = json.dumps(state)
            elif ct == "EmptyLatentImagePresets":
                pw, ph = (w, h) if w >= h else (h, w)
                ratios = {
                    (1024, 1024): "1:1",
                    (1152, 896): "1.286:1",
                    (1216, 832): "1.46:1",
                    (1344, 768): "1.75:1",
                    (1536, 640): "2.4:1",
                }
                ratio = ratios.get((pw, ph))
                if ratio:
                    inp["dimensions"] = f"{pw} x {ph} ({ratio})"
                    inp["invert"] = h > w
            elif ct == "PixaromaDropdown" and str(nid) in dropdown_ids and inp.get("DropdownState"): 
                try:
                    state = json.loads(inp["DropdownState"])
                except Exception:
                    continue
                if state.get("type") == "int":
                    state["value"] = str(max(w, h))
                    inp["DropdownState"] = json.dumps(state)
            elif ct.startswith("TextEncodeQwenImage21") and isinstance(inp.get("resolution"), int):
                inp["resolution"] = max(w, h)
    # Explicit encoder resolution (job["resolution"]) overrides the
    # image_size-derived value on the PixaromaDropdown / direct int input.
    res_override = job.get("resolution")
    if res_override:
        try:
            res_value = str(int(res_override))
        except (TypeError, ValueError):
            res_value = str(res_override)
        for nid in dropdown_ids:
            inp = prompt[nid].setdefault("inputs", {})
            try:
                state = json.loads(inp.get("DropdownState") or "{}")
            except Exception:
                continue
            state["value"] = res_value
            inp["DropdownState"] = json.dumps(state)
        for nid in enc_ids:
            inp = prompt[nid].setdefault("inputs", {})
            if isinstance(inp.get("resolution"), int):
                inp["resolution"] = int(res_value)
    # Per-job seed: baked seeds would repeat identical results across jobs.
    # An explicit job["seed"] pins every seed consumer to the same value.
    job_seed = job.get("seed")
    if job_seed is None:
        job_seed = random.randrange(0, 2**32)
    for node in prompt.values():
        inp = node.get("inputs", {})
        class_type = node.get("class_type", "")
        node_seed = job_seed % (2**32)
        if class_type == "PixaromaSeed":
            inp["seed"] = node_seed
            try:
                state = json.loads(inp.get("SeedState") or "{}")
            except Exception:
                state = {}
            state["runSeed"] = node_seed
            inp["SeedState"] = json.dumps(state)
            continue
        for skey in ("seed", "noise_seed"):
            if isinstance(inp.get(skey), int) and not isinstance(inp.get(skey), bool):
                inp[skey] = node_seed
    return prompt


def _apply_node_params(prompt, params):
    """Apply generic {node_id:widget} patches, after the MMH3 presets."""
    for key, value in (params or {}).items():
        node_id, _, widget = key.rpartition(":")
        node = prompt.get(node_id)
        if isinstance(node, dict) and widget:
            node.setdefault("inputs", {})[widget] = value
    return prompt


_OBJECT_INFO = None


def _node_inputs(class_type):
    """INPUT_TYPES of one node, from the server this worker booted (cached)."""
    global _OBJECT_INFO
    if _OBJECT_INFO is None:
        try:
            _OBJECT_INFO = json.loads(_http("/object_info", timeout=120))
        except Exception as exc:
            print(f"[handler] object_info unavailable, skipping widget repair: {exc}", flush=True)
            _OBJECT_INFO = {}
    entry = _OBJECT_INFO.get(class_type) or {}
    inputs = entry.get("input") or {}
    return {**(inputs.get("required") or {}), **(inputs.get("optional") or {})}


def _repair_widgets(prompt):
    """Reconcile template widgets with the schemas the running server reports.

    Templates age faster than node packs: VideoHelperSuite moved `crf` into the
    format widget, the TensorRT nodes renamed their models and profiles, so a
    template exported against the older releases carries stale values. ComfyUI rejects
    a combo value that is not in the declared list and silently ignores widgets
    the node does not declare at all, so every scalar is checked here:

      * a value that only differs by case is folded to the declared spelling
        (`2K` -> `2k`)
      * an unsupported or undeclared widget is dropped, so the node falls back to
        its own default instead of failing the whole prompt

    Every change is returned so the caller sees exactly what was adjusted.
    """
    repairs = []
    for node_id, node in prompt.items():
        class_type = node.get("class_type")
        specs = _node_inputs(class_type)
        if not specs:
            continue
        for name in list(node.get("inputs") or {}):
            value = node["inputs"][name]
            if isinstance(value, list):  # connection, not a widget
                continue
            spec = specs.get(name.split(".", 1)[0])
            if spec is None:
                node["inputs"].pop(name)
                repairs.append(f"#{node_id} {class_type}: dropped undeclared widget {name}={value!r}")
                continue
            choices = spec[0] if isinstance(spec, list) and spec else None
            if isinstance(choices, list) and choices and value not in choices:
                folded = {str(choice).casefold(): choice for choice in choices}
                match = folded.get(str(value).casefold())
                if match is not None:
                    node["inputs"][name] = match
                    repairs.append(f"#{node_id} {class_type}: {name} {value!r} -> {match!r}")
                else:
                    node["inputs"].pop(name)
                    repairs.append(f"#{node_id} {class_type}: dropped {name}={value!r} (not offered by this node, default applies)")
    return repairs


def _interrupt_prompt(prompt_id):
    """Best-effort ComfyUI abort for a cancelled job: dequeue if pending,
    interrupt if mid-flight."""
    for path, payload in (("/queue", {"delete": [prompt_id]}), ("/interrupt", {})):
        try:
            _http(path, payload, timeout=5)
        except Exception as exc:
            print(f"[handler] {path} failed during cancel: {exc}", flush=True)


async def _queue_and_wait(prompt):
    # Async + awaited sleeps: the runpod SDK stops a job by cancelling its
    # task, so the CancelledError can only be delivered at an await — a sync
    # handler would freeze the event loop and never see the stop signal.
    client_id = uuid.uuid4().hex
    resp = json.loads(await asyncio.to_thread(
        _http, "/prompt", {"prompt": prompt, "client_id": client_id}, 60))
    prompt_id = resp["prompt_id"]
    deadline = time.time() + JOB_TIMEOUT_S
    try:
        while time.time() < deadline:
            hist = json.loads(await asyncio.to_thread(_http, f"/history/{prompt_id}", None, 30))
            entry = hist.get(prompt_id)
            if entry and entry.get("status", {}).get("status_str") == "error":
                msgs = [str(m) for m in entry.get("status", {}).get("messages", [])]
                raise RuntimeError(f"ComfyUI job failed: {' | '.join(msgs)[:800]}")
            if entry and entry.get("status", {}).get("completed"):
                return entry
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        # Even if this await gets cancelled again, the orphan thread still
        # completes and ComfyUI gets interrupted.
        await asyncio.to_thread(_interrupt_prompt, prompt_id)
        raise
    raise TimeoutError(f"prompt {prompt_id} exceeded {JOB_TIMEOUT_S}s")


def _s3_cfg(job):
    cfg = job.get("s3Config") or (job.get("input") or {}).get("s3Config")
    if isinstance(cfg, dict) and cfg.get("endpointUrl") and cfg.get("accessId"):
        return cfg
    return None


def _s3_client(cfg):
    import boto3
    from botocore.config import Config
    host = urllib.parse.urlparse(cfg["endpointUrl"]).netloc
    region = cfg.get("region") or host.split(".", 1)[0].removeprefix("s3api-") or "us-east-1"
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpointUrl"],
        aws_access_key_id=cfg["accessId"],
        aws_secret_access_key=cfg["accessSecret"],
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )


def _emit_file(filename, data, job, inline_used):
    """Small outputs inline as b64; large ones upload to the job's s3Config
    storage (e.g. the RunPod network volume S3 API) and return a presigned URL."""
    oversized = len(data) > INLINE_MAX or inline_used[0] + len(data) > INLINE_BUDGET
    s3c = _s3_cfg(job)
    if oversized and s3c:
        try:
            key = f"{S3_PREFIX}/{job.get('id', 'job')}/{filename}"
            client = _s3_client(s3c)
            client.put_object(Bucket=s3c["bucketName"], Key=key, Body=data)
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": s3c["bucketName"], "Key": key},
                ExpiresIn=int(os.environ.get("S3_URL_TTL_S", "3600")),
            )
            print(f"[handler] {filename} -> s3://{s3c['bucketName']}/{key} ({len(data)/1e6:.1f}MB)", flush=True)
            return {"filename": filename, "url": url}
        except Exception as e:
            print(f"[handler] s3 upload failed for {filename}: {e} — falling back to inline", flush=True)
    elif oversized:
        print(f"[handler] {filename}: {len(data)/1e6:.1f}MB over inline limit, no s3Config — result may be dropped by RunPod", flush=True)
    inline_used[0] += len(data)
    return {"filename": filename, "b64": base64.b64encode(data).decode()}


def _prune_volume_outputs(max_age_s=86400):
    """Drop stale volume output dirs — clients fetch oversized results right
    after the job returns, so anything older than a day is dead weight."""
    root = Path("/runpod-volume") / S3_PREFIX
    cutoff = time.time() - max_age_s
    try:
        for d in root.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def _collect_new_files(since_ts, job, inline_used):
    """Fallback: anything written under ComfyUI's output dir since the job ran."""
    out_dir = Path(COMFY_DIR) / "output"
    try:
        files = sorted(p for p in out_dir.rglob("*") if p.is_file() and p.stat().st_mtime >= since_ts)
    except Exception:
        return []
    out = []
    for p in files:
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".gif", ".wav", ".mp3", ".flac"):
            continue
        out.append(_emit_file(p.name, p.read_bytes(), job, inline_used))
    return out


def _collect_outputs(history_entry, prompt, job, inline_used):
    out, texts = [], {}
    for node_id, node_out in (history_entry.get("outputs") or {}).items():
        print(f"[handler] output node {node_id}: keys={list(node_out.keys())}", flush=True)
        for key in ("images", "gifs", "videos"):
            for item in node_out.get(key, []) or []:
                params = f"filename={item['filename']}&subfolder={item.get('subfolder','')}&type={item.get('type','output')}"
                data = _http(f"/view?{params}", timeout=300)
                out.append(_emit_file(item["filename"], data, job, inline_used))
        src = (prompt or {}).get(node_id, {}).get("inputs", {}).get("source")
        if not isinstance(src, list):  # only ShowText-style nodes carry live text
            continue
        for t in node_out.get("text", []) or []:  # ShowText nodes → prompt trace
            src_ct = (prompt or {}).get(str(src[0]), {}).get("class_type", "")
            label = "Wildcards expanded" if "Wildcard" in src_ct else "Final prompt (QwenVL)"
            texts[label] = t if isinstance(t, str) else str(t)
    return out, texts


def _result_payload(outputs, texts, repairs):
    """Add the response shape MyReels' generator expects on top of our own.

    RunPod wraps whatever the handler returns under `output`, and the generator's
    runpod worker (`extractResultAssets` in beautyai-api-generator) reads only:

        data.video_url / data.image_url / data.audio_url
        data.images[] = {data: <base64>, type: "base64", filename}

    so the same files are mirrored into those keys as well. `outputs` stays the
    canonical, richer form (it also carries texts and widget repairs).
    """
    payload = {"outputs": outputs, "texts": texts, "widget_repairs": repairs}
    media = []
    for item in outputs:
        name = str(item.get("filename") or "")
        lowered = name.lower()
        if item.get("url"):
            if lowered.endswith((".mp4", ".webm", ".gif", ".mov")):
                payload.setdefault("video_url", item["url"])
            elif lowered.endswith((".png", ".jpg", ".jpeg", ".webp")):
                payload.setdefault("image_url", item["url"])
            elif lowered.endswith((".wav", ".mp3", ".flac", ".m4a")):
                payload.setdefault("audio_url", item["url"])
        if item.get("b64"):
            media.append({"data": item["b64"], "type": "base64", "filename": name})
    if media:
        payload["images"] = media
    return payload


async def handler(job):
    print(f"[handler] job {job.get('id')} received", flush=True)
    inputs = job.get("input") or {}
    if inputs.get("action") == "health":
        # Hub smoke test: answers 200 without touching ComfyUI or models, so it
        # also works on an endpoint deployed without the network volume.
        sets = _describe_template_sets()
        return {
            "status": "ok",
            "comfyui": "failed" if _BOOT_ERR else ("ready" if _BOOT_DONE.is_set() else "booting"),
            "comfyui_error": str(_BOOT_ERR) if _BOOT_ERR else None,
            "workflow_set": DEFAULT_TEMPLATE_SET,
            "workflow_sets": sets,
            "workflows": sets.get(DEFAULT_TEMPLATE_SET, []),
            "workflows_dir": str(WORKFLOWS_DIR),
            "volume_mounted": os.path.isdir("/runpod-volume"),
        }
    # Any real workload needs ComfyUI — wait for the background boot first.
    await _ensure_comfy()
    _prune_volume_outputs()
    # _patch uploads input media (blocking, up to minutes for video) — run it
    # off-thread so a cancel signal can still be delivered during uploads.
    if isinstance(inputs.get("prompt_graph"), dict):
        # Raw API-format graph shipped inside the job: the same _patch applies —
        # media upload, config preset, unet/lora needle, prompt.
        prompt = await asyncio.to_thread(_patch, inputs["prompt_graph"], inputs)
    else:
        workflow = str(inputs.get("workflow") or "").strip()
        if not workflow or workflow.lower() == "auto":
            workflow = _auto_workflow(inputs)
            print(f"[handler] workflow auto-selected: {workflow}", flush=True)
        wf = _load_workflow(workflow, inputs.get("template_set"))
        prompt = await asyncio.to_thread(_patch, wf, inputs)
    prompt = _apply_node_params(prompt, inputs.get("params"))
    repairs = await asyncio.to_thread(_repair_widgets, prompt)
    for line in repairs:
        print(f"[handler] widget repair: {line}", flush=True)
    print(f"[handler] queuing prompt ({len(prompt)} nodes)", flush=True)
    started = time.time()
    comfy_log = Path("/workspace/comfy.log")
    log_mark = comfy_log.stat().st_size if comfy_log.is_file() else 0
    history = await _queue_and_wait(prompt)
    print(f"[handler] comfy execution took {time.time() - started:.1f}s", flush=True)
    inline_used = [0]
    outputs, texts = await asyncio.to_thread(_collect_outputs, history, prompt, job, inline_used)
    try:  # surface ComfyUI warnings/errors from this job in the worker log
        with open(comfy_log, "rb") as f:
            f.seek(log_mark)
            interesting = [l for l in f.read().decode(errors="replace").splitlines()
                           if re.search(r"warn|error|missing|unexpected|fail|dtype|clip|traceback", l, re.I)]
            for l in interesting[-40:]:
                print(f"[comfy] {l}", flush=True)
    except Exception:
        pass
    if not outputs:
        outputs = _collect_new_files(started, job, inline_used)
        print(f"[handler] fallback sweep — {len(outputs)} file(s) in output/", flush=True)
    print(f"[handler] done — {len(outputs)} output(s)", flush=True)
    return _result_payload(outputs, texts, repairs)


if __name__ == "__main__":
    threading.Thread(target=_boot_in_bg, daemon=True).start()
    runpod.serverless.start({"handler": handler})
