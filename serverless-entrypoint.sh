#!/bin/bash
# Native-only MiniMax H3 serverless worker entrypoint.
set -e

BAKED="/opt/comfyui-baked"
COMFYUI_DIR="/workspace/runpod-slim/ComfyUI"
VENV_DIR="$COMFYUI_DIR/.venv-${VENV_SUFFIX:-cu130}"

# The production image contains code only. Materialize it into the writable
# workspace, then point ComfyUI at the Network Volume model directory.
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
    echo "entrypoint: materializing ComfyUI code into $COMFYUI_DIR"
    mkdir -p "$COMFYUI_DIR"
    cp -a "$BAKED/." "$COMFYUI_DIR/"
fi
export COMFYUI_DIR

if [ -d /runpod-volume/models ]; then
    cat > "$COMFYUI_DIR/extra_model_paths.yaml" << 'YAML'
runpod_volume:
  base_path: /runpod-volume/models
  checkpoints: checkpoints
  diffusion_models: diffusion_models
  unet: unet
  loras: loras
  vae: vae
  text_encoders: text_encoders
  clip_projections: clip_projections
  clip: clip
  clip_vision: clip_vision
  vae_approx: vae_approx
  sams: sams
YAML
    echo "entrypoint: Network Volume models mounted at /runpod-volume/models"
else
    echo "entrypoint: /runpod-volume/models is not mounted" >&2
fi

# The native image does not use the enhancement-only SAM/TensorRT setup or
# enhancement model downloads. Auto-populate remains available for controlled
# volume provisioning, but is disabled in the production endpoint.
if [ -d /runpod-volume ] && [ "${VOLUME_AUTOPOPULATE:-false}" = "true" ]; then
    /opt/serverless/populate-volume.sh
fi

# Seed only the native templates. Existing volume files are not overwritten.
VOLUME_WORKFLOWS="/runpod-volume/workflows"
if [ -d /runpod-volume ] && [ "${WORKFLOW_SEED:-true}" = "true" ]; then
    mkdir -p "$VOLUME_WORKFLOWS/native"
    for wf in /opt/workflows/native/*.json; do
        [ -f "$wf" ] || continue
        target="$VOLUME_WORKFLOWS/native/$(basename "$wf")"
        [ -f "$target" ] || cp "$wf" "$target"
    done
fi
if [ -d "$VOLUME_WORKFLOWS/native" ]; then
    export WORKFLOWS_DIR="$VOLUME_WORKFLOWS"
else
    export WORKFLOWS_DIR="/opt/workflows"
fi
echo "entrypoint: WORKFLOWS_DIR=$WORKFLOWS_DIR (native templates: $(ls -1 "$WORKFLOWS_DIR/native" 2>/dev/null | tr '\n' ' '))"

export PATH="$VENV_DIR/bin:$PATH"
exec python3 /opt/serverless/handler.py
