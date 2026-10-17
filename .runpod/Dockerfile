# MiniMax H3 RunPod Serverless worker — native FL2VA / Ref2VA only.
#
# The production build starts from the tested CUDA 13.0 ComfyUI runtime image.
# Models are supplied by the Network Volume; this image contains code, native
# URL loaders, the handler and the native workflow templates only.
FROM codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0

# Remove enhancement-only packs inherited from the reusable base image.
RUN set -eux; \
    rm -rf \
      /opt/comfyui-baked/custom_nodes/ComfyUI-RIFE-TensorRT-Auto \
      /opt/comfyui-baked/custom_nodes/ComfyUI-Upscaler-TensorRT-Auto \
      /opt/comfyui-baked/custom_nodes/ComfyUI-Pixaroma \
      /opt/comfyui-baked/custom_nodes/ComfyUI-Impact-Pack \
      /opt/comfyui-baked/custom_nodes/ComfyUI-Impact-Subpack \
      /opt/comfyui-baked/custom_nodes/ComfyUI-Easy-Use \
      /opt/comfyui-baked/custom_nodes/ComfyUI-QwenVL-Mod \
      /opt/comfyui-baked/custom_nodes/ComfyUI-PerfectVideoResolution \
      /opt/comfyui-baked/custom_nodes/ComfyUI-TagForge \
      /opt/comfyui-baked/models/tensorrt \
      /opt/comfyui-baked/models/LLM \
      /opt/comfyui-baked/models/ultralytics \
      /opt/comfyui-baked/models/sams \
      /opt/comfyui-baked/models/vae_approx

# Native H3 loaders and serverless code.
COPY custom_nodes/h3_url_loaders/ /opt/comfyui-baked/custom_nodes/h3_url_loaders/
COPY runpod-gpu-pricing.json /opt/serverless/runpod-gpu-pricing.json
COPY handler.py /opt/serverless/handler.py
COPY serverless-entrypoint.sh /opt/serverless/entrypoint.sh
COPY populate-volume.sh /opt/serverless/populate-volume.sh
COPY models-manifest.txt /opt/serverless/models-manifest.txt
COPY scripts/verify-native-h3.py /opt/serverless/verify-native-h3.py
COPY scripts/validate-templates.py /opt/serverless/validate-templates.py
COPY scripts/test-handler-templates.py /opt/serverless/test-handler-templates.py
COPY scripts/fixtures/ /opt/serverless/fixtures/
COPY workflows-api/native/ /opt/workflows/native/

ENV BAKED_MODELS=false \
    VOLUME_AUTOPOPULATE=false \
    COMFYUI_DIR=/workspace/runpod-slim/ComfyUI \
    WORKFLOWS_DIR=/opt/workflows \
    H3_TEMPLATE_SET=native \
    JOB_TIMEOUT_S=1800

RUN set -eux; \
    rm -rf /opt/workflows/enhancers; \
    sed -i 's/\r$//' /opt/serverless/*.sh /opt/serverless/*.py /opt/serverless/models-manifest.txt /opt/workflows/native/*.json; \
    chmod +x /opt/serverless/entrypoint.sh /opt/serverless/populate-volume.sh; \
    head -n 1 /opt/serverless/entrypoint.sh | grep -qx '#!/bin/bash'; \
    head -n 1 /opt/serverless/populate-volume.sh | grep -qx '#!/bin/bash'
