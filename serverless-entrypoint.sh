#!/bin/bash
# Serverless worker entrypoint. The production image contains the H3 models,
# so a model volume is optional and no first-boot model download is required.
set -e

BAKED="/opt/comfyui-baked"
WORKSPACE_COMFYUI="/workspace/runpod-slim/ComfyUI"

# Avoid copying 77GB of baked weights into the writable container layer. When
# the image was built with BAKE_MODELS=true, run ComfyUI directly from the
# read-only image tree; ComfyUI writes transient output/cache files there as
# root. Keep the old materialisation path for code-only/dev images and legacy
# volume deployments.
baked_model="$(find "$BAKED/models" -type f -print -quit 2>/dev/null || true)"
if [ "${BAKED_MODELS:-false}" = "true" ] && [ -n "$baked_model" ]; then
    COMFYUI_DIR="$BAKED"
    echo "entrypoint: using baked models from $COMFYUI_DIR"
else
    COMFYUI_DIR="$WORKSPACE_COMFYUI"
    if [ ! -f "$COMFYUI_DIR/main.py" ]; then
        echo "entrypoint: materializing code-only ComfyUI into $COMFYUI_DIR"
        mkdir -p "$COMFYUI_DIR"
        cp -a "$BAKED/." "$COMFYUI_DIR/"
    fi
fi
export COMFYUI_DIR

# A mounted model volume is an optional compatibility override. It is used
# when present, but the normal image-only deployment does not create or require
# /runpod-volume.
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
  LLM: LLM
  clip: clip
  clip_vision: clip_vision
  vae_approx: vae_approx
  ultralytics_bbox: ultralytics/bbox
  ultralytics_segm: ultralytics/segm
  sams: sams
  upscale_models: upscale_models
YAML
    echo "entrypoint: optional model volume detected at /runpod-volume/models"
else
    rm -f "$COMFYUI_DIR/extra_model_paths.yaml"
fi

# sam_vit_b_01ec64.pth is a legacy-tar checkpoint — PyTorch >= 2.6 defaults
# torch.load to weights_only=True, which rejects it. Impact Pack's SAMLoader
# calls segment_anything.build_sam → torch.load(f) with no override, so patch
# the library in place (baked /opt copy and venv both covered by the roots).
find /usr /opt -name build_sam.py -exec sed -i 's|torch\.load(f)|torch.load(f, weights_only=False)|' {} +

# A truncated legacy volume copy must not shadow the valid baked checkpoint.
if [ -d /runpod-volume/models ]; then
    [ "$(stat -c%s /runpod-volume/models/sams/sam_vit_b_01ec64.pth 2>/dev/null)" = "375042383" ] || rm -f /runpod-volume/models/sams/sam_vit_b_01ec64.pth
fi

# Kept only for legacy volume deployments. With the baked image this is a
# no-op because /runpod-volume is not mounted and defaults to false.
if [ -d /runpod-volume ] && [ "${VOLUME_AUTOPOPULATE:-false}" = "true" ]; then
    /opt/serverless/populate-volume.sh || echo "entrypoint: volume populate failed — baked jobs may still run" >&2
fi

# Templates are baked into /opt/workflows. If a legacy volume is mounted, keep
# the existing editable-volume behaviour; otherwise handler uses the baked set.
VOLUME_WORKFLOWS="/runpod-volume/workflows"
if [ -d /runpod-volume ]; then
    if [ "${WORKFLOW_SEED:-true}" = "true" ]; then
        mkdir -p "$VOLUME_WORKFLOWS"
        for set_dir in /opt/workflows/*/; do
            [ -d "$set_dir" ] || continue
            set_name="$(basename "$set_dir")"
            if [ ! -d "$VOLUME_WORKFLOWS/$set_name" ]; then
                mkdir -p "$VOLUME_WORKFLOWS/$set_name"
                cp "$set_dir"*.json "$VOLUME_WORKFLOWS/$set_name/" 2>/dev/null || true
                echo "entrypoint: seeded workflow set '$set_name' onto the volume"
            fi
        done
    fi
    export WORKFLOWS_DIR="$VOLUME_WORKFLOWS"
    echo "entrypoint: WORKFLOWS_DIR=$WORKFLOWS_DIR (template sets: $(ls -1 "$VOLUME_WORKFLOWS" 2>/dev/null | tr '\n' ' '))"
fi

# TensorRT engines are optional runtime artefacts. A mounted volume can still
# share them between workers; without a volume they are compiled locally under
# the baked ComfyUI models directory.
if [ -d /runpod-volume/models ]; then
    mkdir -p /runpod-volume/models/tensorrt "$COMFYUI_DIR/models"
    dest="$COMFYUI_DIR/models/tensorrt"
    [ -d "$dest" ] && [ ! -L "$dest" ] && rm -rf "$dest"
    ln -sfn /runpod-volume/models/tensorrt "$dest"
fi

export PATH="${COMFYUI_DIR}/.venv-${VENV_SUFFIX:-cu130}/bin:$PATH"
exec python3 /opt/serverless/handler.py
