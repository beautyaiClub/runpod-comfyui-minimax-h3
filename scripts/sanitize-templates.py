#!/usr/bin/env python3
"""Drop template inputs the target image does not declare.

Template exports age badly: ComfyUI nodes rename or move widgets between
releases (VideoHelperSuite turned `crf` / `pix_fmt` / `save_metadata` /
`trim_to_audio` from plain inputs into per-format widgets), and ComfyUI rejects
a prompt that carries an input the node does not declare. This script removes
exactly those inputs, prints what it removed, and rewrites the template.

Only non-link inputs are touched: anything shaped like ["node_id", slot] is a
connection and is left alone.

    # report only
    python3 scripts/sanitize-templates.py --object-info /tmp/oi.json --set enhancers
    # apply
    python3 scripts/sanitize-templates.py --object-info /tmp/oi.json --set enhancers --write

Exit code 1 without --write means "this template would need changes".
"""

import argparse
import json
import os
import sys
from pathlib import Path


def default_root():
    """Repo checkout (../workflows-api) or the baked image (/opt/workflows)."""
    checkout = Path(__file__).resolve().parent.parent / "workflows-api"
    return checkout if checkout.is_dir() else Path(os.environ.get("WORKFLOWS_DIR", "/opt/workflows"))


def input_names(entry):
    inputs = entry.get("input") or {}
    return set(inputs.get("required") or {}) | set(inputs.get("optional") or {})


def is_link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def sanitize(path, registry):
    graph = json.loads(path.read_text())
    removed = []
    for node_id, node in graph.items():
        entry = registry.get(node.get("class_type"))
        if entry is None:
            continue  # unknown node: the validator reports that, not this script
        known = input_names(entry)
        for name in list(node.get("inputs") or {}):
            if "." in name:  # autogrow slot: schema declares only the base name
                base = name.split(".", 1)[0]
            else:
                base = name
            value = node["inputs"][name]
            if base in known or is_link(value):
                continue
            node["inputs"].pop(name)
            removed.append(f"{node_id} ({node.get('class_type')}): {name}={value!r}")
    return graph, removed


def main():
    parser = argparse.ArgumentParser(description="Drop template inputs the image does not declare")
    parser.add_argument("--object-info", required=True, metavar="FILE", help="dumped /object_info of the target image")
    parser.add_argument("--dir", default=None, help="templates root (default: ../workflows-api, else $WORKFLOWS_DIR)")
    parser.add_argument("--set", action="append", dest="sets", help="only this set (repeatable)")
    parser.add_argument("--write", action="store_true", help="rewrite the templates instead of only reporting")
    args = parser.parse_args()

    root = Path(args.dir) if args.dir else default_root()
    sets = args.sets or sorted(p.name for p in root.iterdir() if p.is_dir())
    registry = json.loads(Path(args.object_info).read_text())

    changed = 0
    for set_name in sets:
        for template in sorted((root / set_name).glob("*.json")):
            graph, removed = sanitize(template, registry)
            if not removed:
                print(f"ok   {set_name}/{template.name} — nothing to drop")
                continue
            changed += 1
            print(f"{'fix ' if args.write else 'FAIL'} {set_name}/{template.name} — {len(removed)} input(s) not declared by this image")
            for line in removed:
                print(f"       {line}")
            if args.write:
                template.write_text(json.dumps(graph, indent=2) + "\n")

    if changed and not args.write:
        print(f"\n{changed} template(s) would be rewritten — rerun with --write to apply")
        return 1
    print(f"\n{'rewrote' if args.write else 'checked'} {changed} template(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
