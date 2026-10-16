#!/usr/bin/env python3
"""Verify that an image can serve the native MiniMax-H3 graphs.

Run inside the built worker image (no GPU and no network volume needed):

    # build the node registry in-process (fast)
    python3 /opt/serverless/verify-native-h3.py

    # start ComfyUI for real (CPU), then validate its live /object_info
    python3 /opt/serverless/verify-native-h3.py --boot

    # validate a dump captured earlier: curl -s host:8188/object_info > oi.json
    python3 /opt/serverless/verify-native-h3.py --object-info oi.json

What it checks
--------------
1. Every node id used by the adapter's native FL2VA / Ref2VA graphs is
   registered. The id list is copied from
   myreels-h3-adapter/src/comfy.js::buildFl2vaGraph and
   ::buildRef2vaNativeGraph — those two builders are what the adapter posts to
   /prompt, so a missing id means a dead graph at runtime.
2. The three URL loaders match the schema of the reference instance
   (192.168.10.20:8189) captured in scripts/fixtures/native-node-parity.json.
   Vendored copies are compared on schema only; `python_module` differs by
   design and is reported, not asserted.

Exit code 0 = the image can run native FL2VA / Ref2VA graphs.
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

COMFY_DIR = Path(os.environ.get("COMFYUI_DIR", "/opt/comfyui-baked"))
COMFY_URL = os.environ.get("COMFY_VERIFY_URL", "http://127.0.0.1:8188")
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "native-node-parity.json"

# myreels-h3-adapter/src/comfy.js — buildFl2vaGraph() + buildRef2vaNativeGraph()
ADAPTER_NATIVE_GRAPH_NODES = [
    # core loaders / samplers
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "LoraLoaderModelOnly",
    "KSamplerSelect",
    "BasicScheduler",
    "BasicGuider",
    "RandomNoise",
    "SamplerCustomAdvanced",
    "VAEDecode",
    "VAEDecodeAudio",
    # H3 core nodes (comfy_extras/nodes_minimax_h3.py)
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SigmaShift",
    # video muxing (comfy_extras/nodes_video.py)
    "CreateVideo",
    "SaveVideo",
    # URL loaders (the only non-core nodes in those graphs)
    "LoadImageFromUrl",
    "LoadVideoFromURL",
    "BeautyAI_LoadAudioFromURL",
]

# Loaders whose schema must match the reference instance exactly.
STRICT_NODES = ["LoadImageFromUrl", "LoadVideoFromURL", "BeautyAI_LoadAudioFromURL"]
SCHEMA_KEYS = ("category", "required", "optional", "output", "output_name", "output_is_list")


def normalize_inputs(input_types):
    """Mirror the /object_info normalisation: {name: {type, default?}}."""
    out = {}
    for name, spec in (input_types or {}).items():
        if isinstance(spec, (list, tuple)) and spec:
            ty = spec[0]
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            entry = {"type": ty}
            if "default" in opts:
                entry["default"] = opts["default"]
        else:
            entry = {"type": spec}
        out[name] = entry
    return out


def normalize_output_is_list(values, output_count):
    """ComfyUI reports per-output list flags even when OUTPUT_IS_LIST is unset."""
    values = list(values or [])
    if len(values) < output_count:
        values += [False] * (output_count - len(values))
    return values


def schema_from_class(cls, display_name=None):
    input_types = cls.INPUT_TYPES() or {}
    output = list(getattr(cls, "RETURN_TYPES", ()) or ())
    return {
        "category": getattr(cls, "CATEGORY", None),
        "display_name": display_name,
        "required": normalize_inputs(input_types.get("required")),
        "optional": normalize_inputs(input_types.get("optional")),
        "output": output,
        "output_name": list(getattr(cls, "RETURN_NAMES", ()) or ()),
        "output_is_list": normalize_output_is_list(getattr(cls, "OUTPUT_IS_LIST", ()), len(output)),
    }


def schema_from_object_info(entry):
    inputs = entry.get("input") or {}
    output = list(entry.get("output") or ())
    return {
        "category": entry.get("category"),
        "display_name": entry.get("display_name"),
        "required": normalize_inputs(inputs.get("required")),
        "optional": normalize_inputs(inputs.get("optional")),
        "output": output,
        "output_name": list(entry.get("output_name") or ()),
        "output_is_list": normalize_output_is_list(entry.get("output_is_list"), len(output)),
    }


def build_registry():
    """Import ComfyUI in-process and register core + custom nodes."""
    sys.path.insert(0, str(COMFY_DIR))
    os.chdir(COMFY_DIR)
    # Registry introspection must work in a plain CPU container: comfy's
    # model_management calls torch.cuda.current_device() at import time unless
    # --cpu is set, and cli_args only parses sys.argv when main.py asks it to, so
    # flip the flag on the parsed namespace before anything imports it.
    import comfy.cli_args  # namespace package: pulls comfy.options only

    comfy.cli_args.args.cpu = True
    import nodes as comfy_nodes

    import_failed = asyncio.run(comfy_nodes.init_extra_nodes(init_custom_nodes=True, init_api_nodes=False))
    display_names = getattr(comfy_nodes, "NODE_DISPLAY_NAME_MAPPINGS", {})
    classes = {
        node_id: schema_from_class(cls, display_names.get(node_id))
        for node_id, cls in comfy_nodes.NODE_CLASS_MAPPINGS.items()
    }
    modules = {node_id: getattr(cls, "__module__", None) for node_id, cls in comfy_nodes.NODE_CLASS_MAPPINGS.items()}
    return classes, modules, import_failed


def fetch_object_info():
    with urllib.request.urlopen(f"{COMFY_URL}/object_info", timeout=120) as resp:
        return json.load(resp)


def boot_and_fetch(timeout_s=600):
    """Start ComfyUI the way production does (main.py) and read /object_info."""
    log = Path("/tmp/comfyui-verify.log")
    proc = subprocess.Popen(
        [sys.executable, str(COMFY_DIR / "main.py"), "--cpu", "--listen", "127.0.0.1", "--port", "8188"],
        cwd=str(COMFY_DIR), stdout=log.open("wb"), stderr=subprocess.STDOUT,
    )
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"ComfyUI exited early (rc={proc.returncode}); see {log}")
            try:
                return fetch_object_info()
            except Exception:
                time.sleep(3)
        raise RuntimeError(f"ComfyUI did not answer /object_info within {timeout_s}s; see {log}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def diff(expected, actual):
    return [
        f"{key}: expected {expected.get(key)!r}, got {actual.get(key)!r}"
        for key in SCHEMA_KEYS
        if expected.get(key) != actual.get(key)
    ]


def main():
    parser = argparse.ArgumentParser(description="Verify native MiniMax-H3 graph coverage")
    parser.add_argument("--object-info", metavar="FILE", help="validate a dumped /object_info instead of the in-process registry")
    parser.add_argument("--boot", action="store_true", help="start ComfyUI on CPU and validate its live /object_info")
    parser.add_argument("--dump", metavar="FILE", help="write the loaders' schema as JSON")
    parser.add_argument("--fixture", default=str(FIXTURE), help=f"parity fixture (default: {FIXTURE})")
    args = parser.parse_args()

    failures = []
    warnings = []
    schemas = {}
    modules = {}
    import_failed = []

    try:
        if args.object_info:
            raw = json.loads(Path(args.object_info).read_text())
            schemas = {k: schema_from_object_info(v) for k, v in raw.items()}
            modules = {k: v.get("python_module") for k, v in raw.items()}
            source = f"object_info {args.object_info}"
        elif args.boot:
            raw = boot_and_fetch()
            schemas = {k: schema_from_object_info(v) for k, v in raw.items()}
            modules = {k: v.get("python_module") for k, v in raw.items()}
            source = f"live server {COMFY_URL}"
        else:
            logging.disable(logging.CRITICAL)
            schemas, modules, import_failed = build_registry()
            source = f"in-process registry from {COMFY_DIR}"
    except Exception:
        print("FAIL: could not obtain the node registry")
        traceback.print_exc()
        return 2

    print(f"registry: {len(schemas)} node ids from {source}")

    if import_failed:
        # A bare in-process import is not a server boot: comfy_extras modules that
        # touch the running-server API (e.g. nodes_replacements) can fail here and
        # still load fine in production. --boot / --object-info are authoritative.
        warnings.append("comfy_extras modules failed to import in this mode: " + ", ".join(import_failed))
    if warnings:
        for warning in warnings:
            print(f"WARN: {warning}")

    missing = [n for n in ADAPTER_NATIVE_GRAPH_NODES if n not in schemas]
    if missing:
        failures.append("native graph nodes missing from the image: " + ", ".join(missing))
        print("FAIL missing nodes: " + ", ".join(missing))
    else:
        print(f"native graph coverage: {len(ADAPTER_NATIVE_GRAPH_NODES)}/{len(ADAPTER_NATIVE_GRAPH_NODES)} node ids present")

    fixture_path = Path(args.fixture)
    fixture = json.loads(fixture_path.read_text()) if fixture_path.is_file() else None
    if fixture is None:
        print(f"WARN: parity fixture not found at {fixture_path}; skipping schema diff")

    dump = {}
    for node_id in STRICT_NODES:
        schema = schemas.get(node_id)
        if schema is None:
            continue
        dump[node_id] = dict(schema, python_module=modules.get(node_id))
        if fixture and node_id in fixture["nodes"]:
            expected = {k: v for k, v in fixture["nodes"][node_id].items() if k != "python_module"}
            if node_id == "LoadImageFromUrl":
                # vendored copy: module path differs from the reference by design
                print(f"parity: {node_id} [vendored as {modules.get(node_id)}]")
            else:
                print(f"parity: {node_id} [{modules.get(node_id)} vs reference {fixture['nodes'][node_id]['python_module']}]")
            problems = diff(expected, schema)
            if problems:
                failures.append(f"{node_id} schema drift from the reference instance: " + "; ".join(problems))
                for problem in problems:
                    print(f"  - {problem}")

    if args.dump:
        Path(args.dump).write_text(json.dumps(dump, indent=2, ensure_ascii=False) + "\n")
        print(f"schema dump: {args.dump}")

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nPASS: native FL2VA / Ref2VA graphs are runnable by this image")
    return 0


if __name__ == "__main__":
    sys.exit(main())
