# MiniMax H3 RunPod Serverless worker — native FL2VA / Ref2VA only.
#
# The production build starts from the tested CUDA 13.0 ComfyUI runtime image.
# The six native H3 weights are downloaded and verified during the RunPod
# GitHub build. Workers read models and templates directly from image layers.
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

# A layer per model keeps completed downloads reusable across code changes.
COPY scripts/bake-models.py /opt/serverless/bake-models.py
COPY models-lock.json /opt/serverless/models-lock.json
RUN python3 /opt/serverless/bake-models.py --file minimax_h3_fl2va_pruned_int8_convrot.safetensors
RUN python3 /opt/serverless/bake-models.py --file qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
RUN python3 /opt/serverless/bake-models.py --file minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors
RUN python3 /opt/serverless/bake-models.py --file minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
RUN python3 /opt/serverless/bake-models.py --file minimax_h3_video_vae_fp16.safetensors
RUN python3 /opt/serverless/bake-models.py --file minimax_h3_audio_vae_fp32.safetensors

# Native H3 loaders and serverless code.
COPY custom_nodes/h3_url_loaders/ /opt/comfyui-baked/custom_nodes/h3_url_loaders/
COPY handler.py /opt/serverless/handler.py
COPY serverless-entrypoint.sh /opt/serverless/entrypoint.sh
COPY populate-volume.sh /opt/serverless/populate-volume.sh
COPY models-manifest.txt /opt/serverless/models-manifest.txt
COPY scripts/verify-native-h3.py /opt/serverless/verify-native-h3.py
COPY scripts/validate-templates.py /opt/serverless/validate-templates.py
COPY scripts/test-handler-templates.py /opt/serverless/test-handler-templates.py
COPY scripts/fixtures/ /opt/serverless/fixtures/
COPY workflows-api/native/ /opt/workflows/native/

ENV BAKED_MODELS=true \
    H3_MODEL_SOURCE=image \
    VOLUME_AUTOPOPULATE=false \
    COMFYUI_DIR=/opt/comfyui-baked \
    WORKFLOWS_DIR=/opt/workflows \
    H3_TEMPLATE_SET=native \
    JOB_TIMEOUT_S=1800

RUN set -eux; \
    rm -rf /opt/workflows/enhancers; \
    sed -i 's/\r$//' /opt/serverless/*.sh /opt/serverless/*.py /opt/serverless/models-manifest.txt /opt/workflows/native/*.json; \
    chmod +x /opt/serverless/entrypoint.sh /opt/serverless/populate-volume.sh; \
    head -n 1 /opt/serverless/entrypoint.sh | grep -qx '#!/bin/bash'; \
    head -n 1 /opt/serverless/populate-volume.sh | grep -qx '#!/bin/bash'; \
    python3 /opt/serverless/bake-models.py --verify-only --size-only
