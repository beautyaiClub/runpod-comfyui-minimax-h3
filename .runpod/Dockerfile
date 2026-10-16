# RunPod GitHub build Dockerfile.
#
# The base is the previous successful RunPod-internal build of this repository,
# not Docker Hub. Reusing it avoids spending the builder timeout reinstalling
# Ubuntu/CUDA/PyTorch/ComfyUI; this build only adds the model layer and refreshes
# the handler/workflows/entrypoint from the current GitHub commit.
FROM registry.runpod.net/beautyaiclub-runpod-comfyui-minimax-h3-main-runpod-dockerfile:b71517eaa

ARG BAKE_MODELS=true

# The base image already contains curl, the Hugging Face CLI, CUDA 13, Python,
# ComfyUI and all pinned custom nodes. Download models into the image instead of
# /runpod-volume. Hugging Face uses `hf` with Xet disabled because a direct curl
# resolve URL can return a tiny error document from a builder network.
COPY models-manifest.txt /tmp/build-models-manifest.txt
RUN set -eux; \
    if [ "$BAKE_MODELS" = "true" ]; then \
      downloaded=0; \
      while IFS="$(printf '\t')" read -r sub name url; do \
        case "$sub" in ''|\#*) continue ;; esac; \
        dest="/opt/comfyui-baked/models/$sub/$name"; \
        mkdir -p "$(dirname "$dest")"; \
        echo "bake-model: $name"; \
        case "$url" in \
          https://huggingface.co/*/resolve/*) \
            hf_ref="${url#https://huggingface.co/}"; \
            repo_id="${hf_ref%%/resolve/*}"; \
            hf_ref="${hf_ref#*/resolve/}"; \
            revision="${hf_ref%%/*}"; \
            repo_path="${hf_ref#*/}"; \
            tmp_dir="/tmp/hf-model"; \
            rm -rf "$tmp_dir"; \
            mkdir -p "$tmp_dir"; \
            HF_HUB_DISABLE_XET=1 HF_HUB_ENABLE_HF_TRANSFER=1 \
              hf download "$repo_id" "$repo_path" --revision "$revision" --local-dir "$tmp_dir"; \
            test -s "$tmp_dir/$repo_path"; \
            mv -f "$tmp_dir/$repo_path" "$dest.part"; \
            rm -rf "$tmp_dir"; \
            ;; \
          *) \
            if [ -f "$dest.part" ]; then \
              curl -fL --retry 5 --retry-all-errors --retry-delay 5 -C - -o "$dest.part" "$url"; \
            else \
              curl -fL --retry 5 --retry-all-errors --retry-delay 5 -o "$dest.part" "$url"; \
            fi; \
            ;; \
        esac; \
        test -s "$dest.part"; \
        mv -f "$dest.part" "$dest"; \
        downloaded=$((downloaded + 1)); \
        stat -c 'bake-model: %n %s bytes' "$dest"; \
      done < /tmp/build-models-manifest.txt; \
      test "$downloaded" -eq 14; \
      du -sh /opt/comfyui-baked/models; \
    else \
      echo 'bake-model: disabled'; \
    fi

# Refresh the serverless code and templates from this commit. This also replaces
# the old volume-first entrypoint with the image-first implementation.
COPY handler.py /opt/serverless/handler.py
COPY serverless-entrypoint.sh /opt/serverless/entrypoint.sh
COPY populate-volume.sh /opt/serverless/populate-volume.sh
COPY models-manifest.txt /opt/serverless/models-manifest.txt
COPY scripts/verify-native-h3.py /opt/serverless/verify-native-h3.py
COPY scripts/validate-templates.py /opt/serverless/validate-templates.py
COPY scripts/test-handler-templates.py /opt/serverless/test-handler-templates.py
COPY scripts/fixtures/ /opt/serverless/fixtures/
COPY workflows-api/native/ /opt/workflows/native/
COPY workflows-api/enhancers/ /opt/workflows/enhancers/

ENV BAKED_MODELS=true \
    VOLUME_AUTOPOPULATE=false \
    COMFYUI_DIR=/opt/comfyui-baked \
    WORKFLOWS_DIR=/opt/workflows

RUN set -eux; \
    sed -i 's/\r$//' /opt/serverless/*.sh /opt/serverless/*.py /opt/serverless/models-manifest.txt /opt/workflows/*/*.json; \
    chmod +x /opt/serverless/entrypoint.sh /opt/serverless/populate-volume.sh; \
    head -n 1 /opt/serverless/entrypoint.sh | grep -qx '#!/bin/bash'; \
    head -n 1 /opt/serverless/populate-volume.sh | grep -qx '#!/bin/bash'
