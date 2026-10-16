#!/usr/bin/env python3
"""Check that every bundled workflow template is runnable by an image.

Validates each template against a ComfyUI node registry (`/object_info`):

  ERROR (template fails)
    * unknown class_type
    * a *link* whose input name the node does not declare — ComfyUI silently
      drops it, which usually leaves a required input unconnected
    * a required input with no default that the template does not provide
    * a link to a missing node, or to an output slot the node does not have

  WARN (runs anyway)
    * an extra *scalar* input the node does not declare (e.g. `crf` on a
      VideoHelperSuite release that moved it into the format widget). Verified
      against ComfyUI v0.40: unknown widgets are ignored, the node falls back to
      its own default.
    * a combo value the node does not offer (e.g. `resize_to: "2K"` where the
      installed TensorRT node lists `2k`) — handler.py repairs these per job, see
      `_repair_widgets()`.

Option lists that the node builds from the filesystem at runtime (model names,
checkpoints, LoRAs) are not checked here: an image without the network volume
reports them as empty, which says nothing about the endpoint.

Usage:

    # straight from a live server
    python3 scripts/validate-templates.py --url http://host:8188/object_info

    # or from a dump (in the image: /opt/workflows, else ../workflows-api)
    python3 scripts/validate-templates.py --object-info /tmp/oi.json

Exit code 0 = every template in every set can run on that image.
"""

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path


def default_root():
    """Repo checkout (../workflows-api) or the baked image (/opt/workflows)."""
    checkout = Path(__file__).resolve().parent.parent / "workflows-api"
    return checkout if checkout.is_dir() else Path(os.environ.get("WORKFLOWS_DIR", "/opt/workflows"))


def load_registry(args):
    if args.object_info:
        return json.loads(Path(args.object_info).read_text())
    with urllib.request.urlopen(args.url, timeout=120) as resp:
        return json.load(resp)


def spec_of(entry):
    """{name: options-or-type} for required + optional inputs."""
    inputs = entry.get("input") or {}
    specs = {}
    for section in ("required", "optional"):
        specs.update(inputs.get(section) or {})
    return specs, set(inputs.get("required") or {})


def output_count(entry):
    return len(entry.get("output") or () or ("",))


# Inputs whose option list is populated at runtime from the model dirs — not
# comparable against a registry dump taken without the volume mounted.
RUNTIME_POPULATED = {
    "unet_name", "clip_name", "vae_name", "lora_name", "ckpt_name", "style_model_name",
    "control_net_name", "model", "model_name", "image", "video", "audio", "directory", "path",
}


def is_link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and \
        isinstance(value[1], int) and not isinstance(value[1], bool)


def validate_template(path, registry):
    graph = json.loads(path.read_text())
    if "nodes" in graph:
        return [f"{path.name}: UI-format graph, needs 'Export API'"], []

    errors, warnings = [], Counter()
    for node_id, node in graph.items():
        class_type = node.get("class_type")
        entry = registry.get(class_type)
        if entry is None:
            errors.append(f"node {node_id}: unknown class_type {class_type!r}")
            continue
        specs, required = spec_of(entry)
        provided = node.get("inputs") or {}

        for name, value in provided.items():
            base = name.split(".", 1)[0]
            if base not in specs:
                if is_link(value):
                    errors.append(f"node {node_id} ({class_type}): link input {name!r} is not declared "
                                  f"(would be dropped, leaving the node short of an input)")
                else:
                    warnings[f"{class_type}.{name}"] += 1
                continue
            if is_link(value):
                target, slot = value
                if target not in graph:
                    errors.append(f"node {node_id} ({class_type}): input {name!r} links to missing node {target!r}")
                    continue
                target_entry = registry.get(graph[target].get("class_type"))
                if target_entry is not None and slot >= output_count(target_entry):
                    errors.append(f"node {node_id} ({class_type}): input {name!r} reads output {slot} of "
                                  f"{target} ({graph[target].get('class_type')}), which has {output_count(target_entry)}")
            else:
                options = specs[base][1] if isinstance(specs[base], list) and len(specs[base]) > 1 else {}
                choices = specs[base][0] if isinstance(specs[base], list) and specs[base] else None
                if isinstance(choices, list) and choices and value not in choices and base not in RUNTIME_POPULATED:
                    warnings[f"{class_type}.{name}"] += 1
                elif isinstance(options, dict) and options.get("min") is not None and isinstance(value, (int, float)) \
                        and not isinstance(value, bool) and value < options["min"]:
                    errors.append(f"node {node_id} ({class_type}): {name}={value!r} is below min {options['min']}")

        for name in sorted(required):
            if name in provided or any(k.split(".", 1)[0] == name for k in provided):
                continue
            spec = specs.get(name)
            options = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            if "default" in options:
                continue
            errors.append(f"node {node_id} ({class_type}): required input {name!r} is missing and has no default")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description="Validate ComfyUI workflow templates against a node registry")
    parser.add_argument("--url", default="http://127.0.0.1:8188/object_info", help="live /object_info URL")
    parser.add_argument("--object-info", metavar="FILE", help="validate a dumped /object_info instead")
    parser.add_argument("--dir", default=None, help="templates root (default: ../workflows-api, else $WORKFLOWS_DIR)")
    parser.add_argument("--set", action="append", dest="sets", help="only this set (repeatable)")
    args = parser.parse_args()

    root = Path(args.dir) if args.dir else default_root()
    sets = args.sets or sorted(p.name for p in root.iterdir() if p.is_dir())
    try:
        registry = load_registry(args)
        source = args.object_info or args.url
    except Exception as exc:
        print(f"FAIL: could not load the node registry ({exc})")
        return 2

    print(f"registry: {len(registry)} node ids from {source}")
    print(f"templates: {root}")
    failures = 0
    all_warnings = Counter()
    for set_name in sets:
        templates = sorted((root / set_name).glob("*.json"))
        if not templates:
            print(f"\n{set_name}: no templates")
            continue
        print(f"\n{set_name}:")
        for template in templates:
            errors, warnings = validate_template(template, registry)
            all_warnings.update(warnings)
            if errors:
                failures += 1
                print(f"  FAIL {template.name}")
                for error in errors[:12]:
                    print(f"       {error}")
                if len(errors) > 12:
                    print(f"       … {len(errors) - 12} more")
            else:
                nodes = len(json.loads(template.read_text()))
                extra = f", {sum(warnings.values())} ignored widget(s)" if warnings else ""
                print(f"  ok   {template.name} ({nodes} nodes{extra})")

    if all_warnings:
        print("\nrepaired per job by handler._repair_widgets() — undeclared widgets are dropped and"
              " case-only combo differences folded, so the node keeps its own default instead of failing:")
        for name, count in sorted(all_warnings.items()):
            print(f"  {name} x{count}")

    if failures:
        print(f"\nFAIL: {failures} template(s) cannot run on this image")
        return 1
    print("\nPASS: every template in every set is runnable on this image")
    return 0


if __name__ == "__main__":
    sys.exit(main())
