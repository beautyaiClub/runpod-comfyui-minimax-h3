#!/bin/bash
# Native H3 models and workflows are bundled in the image.
set -euo pipefail
export COMFYUI_DIR=/opt/comfyui-baked
export WORKFLOWS_DIR=/opt/workflows
export H3_MODEL_SOURCE=image
mkdir -p /workspace "$COMFYUI_DIR/input" "$COMFYUI_DIR/output" "$COMFYUI_DIR/temp"
# Do not copy model weights to the writable container layer or use a volume.
rm -f "$COMFYUI_DIR/extra_model_paths.yaml"
python3 /opt/serverless/bake-models.py --verify-only --size-only
export PATH="$COMFYUI_DIR/.venv-${VENV_SUFFIX:-cu130}/bin:$PATH"
exec python3 /opt/serverless/handler.py
