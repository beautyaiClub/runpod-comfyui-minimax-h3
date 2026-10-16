#!/usr/bin/env python3
"""Offline checks that the bundled templates survive handler._patch.

No ComfyUI, no GPU, no network: `runpod` is stubbed and the upload/download
helpers are replaced, so this only exercises the graph surgery — which is where
the native templates can actually break (unfilled loader slots, autogrow
reference inputs, LoRA/unet needle patching).

Run from the repository root:

    python3 scripts/test-handler-templates.py
"""

import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Repo layout: scripts/ inside the repository; image layout: /opt/serverless/.
ROOT = HERE.parent if (HERE.parent / "handler.py").is_file() else HERE
sys.path.insert(0, str(ROOT))

runpod_stub = types.ModuleType("runpod")
runpod_stub.serverless = types.SimpleNamespace(start=lambda *a, **k: None)
sys.modules.setdefault("runpod", runpod_stub)

import handler  # noqa: E402  (needs the stub above)

handler._upload = lambda name, data: name
handler._media_bytes = lambda value: b""

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"ok   {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"FAIL {label} — {detail}")


def class_types(prompt):
    return sorted(node.get("class_type") for node in prompt.values())


def inputs_of(prompt, class_type):
    return [node.get("inputs", {}) for node in prompt.values() if node.get("class_type") == class_type]


def by_id(prompt, node_id):
    return prompt[str(node_id)] if str(node_id) in prompt else prompt.get(node_id)


# ---------------------------------------------------------------- t2va / fl2va
fl2va = handler._load_workflow("MiniMaxH3-Native-FL2VA.json", "native")

t2va = handler._patch(fl2va, {"prompt": "a lighthouse at dusk", "config": "native_turbo"})
check("t2va: both frame slots pruned", "LoadImageFromUrl" not in class_types(t2va), class_types(t2va))
check("t2va: image-to-video node kept", "MiniMaxH3ImageToVideo" in class_types(t2va), class_types(t2va))
m2v = inputs_of(t2va, "MiniMaxH3ImageToVideo")[0]
check("t2va: no dangling frame inputs", "first_frame" not in m2v and "last_frame" not in m2v, sorted(m2v))
check("t2va: prompt patched", m2v.get("prompt") == "a lighthouse at dusk", m2v.get("prompt"))

fl2va_prompt = handler._load_workflow("MiniMaxH3-Native-FL2VA.json", "native")
fl = handler._patch(fl2va_prompt, {
    "prompt": "slow dolly in",
    "config": "native_turbo",
    "images": ["https://cdn.example.com/first.png", "https://cdn.example.com/last.png"],
})
loaders = inputs_of(fl, "LoadImageFromUrl")
check("fl2va: two frame slots kept", len(loaders) == 2, loaders)
check("fl2va: urls injected", [l.get("image") for l in loaders] ==
      ["https://cdn.example.com/first.png", "https://cdn.example.com/last.png"], loaders)
frame_inputs = inputs_of(fl, "MiniMaxH3ImageToVideo")[0]
check("fl2va: first/last frame wired", "first_frame" in frame_inputs and "last_frame" in frame_inputs, sorted(frame_inputs))

i2va = handler._patch(handler._load_workflow("MiniMaxH3-Native-FL2VA.json", "native"),
                      {"prompt": "portrait", "images": ["https://cdn.example.com/only.png"]})
frame_inputs = inputs_of(i2va, "MiniMaxH3ImageToVideo")[0]
check("i2va: one slot kept, last frame pruned", len(inputs_of(i2va, "LoadImageFromUrl")) == 1
      and "first_frame" in frame_inputs and "last_frame" not in frame_inputs, sorted(frame_inputs))

# LoRA / unet needles: config presets patch both, with no model files in reach
lora = inputs_of(fl, "LoraLoaderModelOnly")[0]
check("fl2va: turbo lora name comes from the template", lora.get("strength_model") == 1.0, lora)
unet = inputs_of(fl, "UNETLoader")[0]
check("fl2va: unet is the manifest int8 file", unet.get("unet_name", "").endswith("int8_convrot.safetensors"), unet)
clip = inputs_of(fl, "CLIPLoader")[0]
check("fl2va: clip is the manifest encoder", clip.get("clip_name") == "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors", clip)
vaes = sorted(v.get("vae_name") for v in inputs_of(fl, "VAELoader"))
check("fl2va: vaes from the manifest", vaes == ["minimax_h3_audio_vae_fp32.safetensors", "minimax_h3_video_vae_fp16.safetensors"], vaes)

# --------------------------------------------------------------------- ref2va
ref = handler._load_workflow("MiniMaxH3-Native-Ref2VA.json", "native")
ref_patched = handler._patch(ref, {
    "prompt": "character walk",
    "config": "r2va_singularity_turbo",
    "images": ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"],
    "videos": ["https://cdn.example.com/ref.mp4"],
    "audios": ["https://cdn.example.com/ref.mp3"],
})
check("ref2va: image slots trimmed to the job", len(inputs_of(ref_patched, "LoadImageFromUrl")) == 2,
      len(inputs_of(ref_patched, "LoadImageFromUrl")))
check("ref2va: video slot kept", len(inputs_of(ref_patched, "LoadVideoFromURL")) == 1,
      len(inputs_of(ref_patched, "LoadVideoFromURL")))
check("ref2va: audio slot kept", len(inputs_of(ref_patched, "BeautyAI_LoadAudioFromURL")) == 1,
      len(inputs_of(ref_patched, "BeautyAI_LoadAudioFromURL")))
ref_node = inputs_of(ref_patched, "MiniMaxH3ReferenceToVideo")[0]
ref_keys = sorted(k for k in ref_node if k.startswith("ref_") and "." in k)  # autogrow slots only
check("ref2va: autogrow keys match the job",
      ref_keys == ["ref_audios.ref_audio_0", "ref_images.ref_image_0", "ref_images.ref_image_1",
                   "ref_videos.ref_video_0"], ref_keys)
check("ref2va: prompt patched", ref_node.get("prompt") == "character walk", ref_node.get("prompt"))
check("ref2va: sigma shift removed by preset", "MiniMaxH3SigmaShift" not in class_types(ref_patched),
      class_types(ref_patched))
guider_model = inputs_of(ref_patched, "BasicGuider")[0].get("model")
check("ref2va: guider rewired past the removed sigma shift",
      isinstance(guider_model, list) and by_id(ref_patched, guider_model[0]) is not None
      and by_id(ref_patched, guider_model[0]).get("class_type") == "LoraLoaderModelOnly",
      guider_model)

ref_only_images = handler._patch(handler._load_workflow("MiniMaxH3-Native-Ref2VA.json", "native"),
                                 {"prompt": "just a look", "images": ["https://cdn.example.com/a.png"]})
ref_node = inputs_of(ref_only_images, "MiniMaxH3ReferenceToVideo")[0]
check("ref2va: image-only job keeps one reference",
      sorted(k for k in ref_node if k.startswith("ref_") and "." in k) == ["ref_images.ref_image_0"],
      sorted(k for k in ref_node if k.startswith("ref_") and "." in k))
check("ref2va: optional video/audio slots pruned",
      "LoadVideoFromURL" not in class_types(ref_only_images)
      and "BeautyAI_LoadAudioFromURL" not in class_types(ref_only_images), class_types(ref_only_images))

# ------------------------------------------------------------------ sets & IO
check("template sets", handler._describe_template_sets().get("native") is not None,
      handler._describe_template_sets())
try:
    other_set = handler._load_workflow("MiniMaxH3-Turbo-FL2VA.json", "native")
    check("template fallback: enhancer template served when the requested set lacks it",
          isinstance(other_set, dict) and len(other_set) > 20
          and any(n.get("class_type") == "UNETLoader" for n in other_set.values()), f"{len(other_set)} nodes")
except RuntimeError as exc:
    check("template fallback: enhancer template served when the requested set lacks it", False, str(exc))

# ------------------------------------------------------------- widget repair
# Stand in for the node schemas the running server reports, including the drift
# the enhancer templates were exported before.
handler._OBJECT_INFO = {
    "VHS_VideoCombine": {"input": {"required": {
        "images": ["IMAGE"], "frame_rate": ["FLOAT", {"default": 8}],
        "format": [["video/webm", "video/h264-mp4"], {"default": "video/webm"}],
        "save_output": ["BOOLEAN", {"default": True}],
    }}},
    "UpscalerTensorrt": {"input": {"required": {
        "images": ["IMAGE"], "resize_to": [["none", "2k", "4x"], {"default": "none"}],
        "resize_width": ["INT", {"default": 2048}],
    }}},
    "AutoLoadRifeTensorrtModel": {"input": {"required": {
        "model": [["rife47_x", "rife48_x"], {"default": "rife47_x"}],
        "precision": [["fp16", "fp32"], {"default": "fp16"}],
    }}},
}

drifted = {
    "1": {"class_type": "VHS_VideoCombine", "inputs": {
        "images": ["2", 0], "frame_rate": 48, "format": "video/webm",
        "crf": 20, "pix_fmt": "yuv420p", "save_output": True}},
    "2": {"class_type": "UpscalerTensorrt", "inputs": {
        "images": ["3", 0], "resize_to": "2K", "resize_width": 2048}},
    "3": {"class_type": "AutoLoadRifeTensorrtModel", "inputs": {
        "model": "rife425_ensemble_False_scale_1_sim", "precision": "fp16"}},
}
repairs = handler._repair_widgets(drifted)
check("repair: undeclared widgets dropped",
      "crf" not in drifted["1"]["inputs"] and "pix_fmt" not in drifted["1"]["inputs"],
      drifted["1"]["inputs"])
check("repair: declared widgets kept",
      drifted["1"]["inputs"].get("format") == "video/webm" and drifted["1"]["inputs"].get("frame_rate") == 48,
      drifted["1"]["inputs"])
check("repair: case-folded combo value", drifted["2"]["inputs"]["resize_to"] == "2k", drifted["2"]["inputs"])
check("repair: unsupported model dropped",
      "model" not in drifted["3"]["inputs"] and drifted["3"]["inputs"].get("precision") == "fp16",
      drifted["3"]["inputs"])
check("repair: links untouched", drifted["1"]["inputs"].get("images") == ["2", 0], drifted["1"]["inputs"])
check("repair: every change reported", len(repairs) == 4, repairs)

# ------------------------------------------- MyReels generator response shape
payload = handler._result_payload(
    [{"filename": "out.mp4", "b64": "AAA"}, {"filename": "shot.png", "url": "https://cdn/x.png"}],
    {"Final prompt": "x"}, ["#1 dropped crf"])
check("payload: generator base64 slot", payload.get("images") == [{"data": "AAA", "type": "base64", "filename": "out.mp4"}],
      payload.get("images"))
check("payload: generator url slot", payload.get("image_url") == "https://cdn/x.png", payload.get("image_url"))
check("payload: keeps our own keys", set(payload) >= {"outputs", "texts", "widget_repairs"}, sorted(payload))

# ------------------------------------------------------------ tolerant inputs
check("as_list: single url", handler._as_list("https://a/x.png") == ["https://a/x.png"], handler._as_list("https://a/x.png"))
check("as_list: json array", handler._as_list('["https://a/1.png", "https://a/2.png"]')
      == ["https://a/1.png", "https://a/2.png"], handler._as_list('["https://a/1.png", "https://a/2.png"]'))
check("as_list: comma string", handler._as_list("https://a/1.png, https://a/2.png")
      == ["https://a/1.png", "https://a/2.png"], handler._as_list("https://a/1.png, https://a/2.png"))
check("as_list: empty", handler._as_list(None) == [] and handler._as_list("") == [], handler._as_list(None))

# --------------------------------------------------- automatic template choice
auto_cases = [
    ({"prompt": "x"}, "MiniMaxH3-Native-FL2VA.json"),                                              # t2va
    ({"image1": "https://a/1.png"}, "MiniMaxH3-Native-FL2VA.json"),                                 # i2va / fl2va channel
    ({"image1": "https://a/1.png", "image2": "https://a/2.png"}, "MiniMaxH3-Native-FL2VA.json"),    # 首尾帧
    ({"images": ["https://a/1.png"]}, "MiniMaxH3-Native-Ref2VA.json"),                              # 参考素材
    ({"audios": ["https://a/1.mp3"]}, "MiniMaxH3-Native-Ref2VA.json"),
    ({"enhance": True, "prompt": "x"}, "MiniMaxH3-Turbo-T2VA.json"),
    ({"enhance": True, "image1": "https://a/1.png"}, "MiniMaxH3-Turbo-I2VA.json"),
    ({"enhance": True, "image1": "https://a/1.png", "image2": "https://a/2.png"}, "MiniMaxH3-Turbo-FL2VA.json"),
    ({"enhance": True, "images": ["https://a/1.png"]}, "MiniMaxH3-Turbo-R2VA.json"),
    ({"enhance": True, "images": ["https://a/1.png"], "videos": ["https://a/1.mp4"]}, "MiniMaxH3-Singularity-R2VA.json"),
]
for job, expected in auto_cases:
    got = handler._auto_workflow(job)
    check(f"auto workflow for {sorted(job)}", got == expected, f"expected {expected}, got {got}")

check("auto preset: native channel", handler._auto_config({"prompt": "x"}) == "native_turbo",
      handler._auto_config({"prompt": "x"}))
check("auto preset: enhancer + refs uses Singularity",
      handler._auto_config({"enhance": True, "images": ["https://a/1.png"]}) == "r2va_singularity_turbo",
      handler._auto_config({"enhance": True, "images": ["https://a/1.png"]}))

# ------------------------------------------------------- H3 canvas & frame rules
check("canvas: 480p 16:9 default", handler._h3_size({"resolution": "480p", "aspect_ratio": "16:9"}) == {"width": 832, "height": 480},
      handler._h3_size({"resolution": "480p", "aspect_ratio": "16:9"}))
check("canvas: 720p 9:16", handler._h3_size({"resolution": "720p", "aspect_ratio": "9:16"}) == {"width": 768, "height": 1344},
      handler._h3_size({"resolution": "720p", "aspect_ratio": "9:16"}))
check("canvas: 576p 1:1", handler._h3_size({"resolution": "576p", "aspect_ratio": "1:1"}) == {"width": 576, "height": 576},
      handler._h3_size({"resolution": "576p", "aspect_ratio": "1:1"}))
check("canvas: nothing requested -> template default",
      handler._h3_size({"prompt": "x"}) is None, handler._h3_size({"prompt": "x"}))
check("frames: 5s on the 17-grid", handler._frame_count({"seconds": 5}) == 5 + 17 * 7,
      handler._frame_count({"seconds": 5}))
check("frames: 15s capped by grid", handler._frame_count({"duration_seconds": 15}) <= 3600,
      handler._frame_count({"duration_seconds": 15}))

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s)")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("all template checks passed")
