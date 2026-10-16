# RunPod GitHub build entrypoint.
#
# This file intentionally contains the complete build instead of a Docker Hub
# FROM wrapper. RunPod checks out the repository and builds this Dockerfile on
# its own builder; BAKE_MODELS=true puts the H3 weights in the resulting
# internal image, so the endpoint does not need a model volume.
#
# MiniMax H3 RunPod Serverless worker.
#
# Ubuntu 24.04 + CUDA 13.0 + ComfyUI v0.40.0, built with the multi-stage layout
# of the official runpod-workers/comfyui-base image.
#
# Node set:
#   * official base packs  - ComfyUI-Manager, KJNodes, Civicomfy, RunpodDirect
#   * video                - ComfyUI-VideoHelperSuite
#   * MyReels H3 loaders   - beautyaiClub/comfyui-beautyai (official repo) plus
#                            custom_nodes/h3_url_loaders (LoadImageFromUrl /
#                            LoadVideoFromURL vendored from their upstream
#                            projects, see custom_nodes/h3_url_loaders/README.md)
#   * enhancement chain    - prompt enhancer, RIFE / TensorRT upscaler, face
#                            detailer, wildcards (WITH_H3_ENHANCERS, see below)
#
# Two template sets ship side by side and are picked per job with
# `"template_set": "native" | "enhancers"` (endpoint default: H3_TEMPLATE_SET):
#
#   /opt/workflows/native     graphs built by myreels-h3-adapter — core ComfyUI
#                             plus the three URL loaders, no enhancement nodes
#   /opt/workflows/enhancers  the enhancement templates — need the
#                             enhancement chain
#
# Both template sets and the H3 model manifest are baked into the image by
# default. The RunPod GitHub builder produces the final image directly, so the
# worker does not need a model volume or a first-boot model download.
#
# WITH_H3_ENHANCERS=true (default) installs the enhancement chain so the
# `enhancers` set runs. Build with --build-arg WITH_H3_ENHANCERS=false for a
# smaller image that only serves the `native` set.
#
# BAKE_MODELS=true (default) downloads every model in models-manifest.txt during
# the image build. Set it to false only for a code-only development image.

# ============================================================================
# Stage 1: Builder - ComfyUI, node packs and the Python dependency lock
# ============================================================================
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

ARG COMFYUI_VERSION=v0.40.0
# Official base packs (same four sha's as runpod-workers/comfyui-base).
ARG MANAGER_SHA=c352b16bb186
ARG KJNODES_SHA=bc8e4ce4254b
ARG CIVICOMFY_SHA=555e984bbcb0
ARG RUNPODDIRECT_SHA=809065c9d2f3
# Video + the loaders used by the MyReels H3 graphs.
ARG VHS_SHA=4d907bee61e92c2e65af3bd6383a4e4d356126d1
ARG BEAUTYAI_SHA=1788e238b069e1fb56524a17f49f45916f9e0fdb
# Enhancement chain for the `enhancers` template set (see the header). Every
# pack is pinned to a commit so a rebuild resolves to the same code.
ARG WITH_H3_ENHANCERS=true
ARG BAKE_MODELS=true
ARG RIFE_TRT_SHA=46d7a504457e5583f7be9b6535bd2375c8f5c7af
ARG UPSCALER_TRT_SHA=f428364befb14848c7b6efe357f4b4ff0904009c
ARG PIXAROMA_SHA=63538e7214cc1a72241def48b4a5939d7ed7fd87
ARG IMPACT_PACK_SHA=429d0159ad429e64d2b3916e6e7be9c22d025c3c
ARG IMPACT_SUBPACK_SHA=50c7b71a6a224734cc9b21963c6d1926816a97f1
ARG EASY_USE_SHA=4ba22aa1ec2dd606995574d93d70c6c8a3744859
ARG QWENVL_MOD_SHA=190a62e6f6d188493305f9606499ea51122ed9fa
ARG PERFECT_VIDEO_RES_SHA=f9a6cda341e1aec2996b4648f2457aff958e0a5f
ARG TAGFORGE_SHA=3fde4bd32512fc9a0db3f3ef2ec188f5338796bd
# TensorRT version the prebuilt engines in models-manifest.txt were compiled
# with (see the engine file names). Engines are not forward/backward compatible
# across TensorRT feature releases, so the runtime library has to match.
ARG TRT_CU13_VERSION=10.15.1.29
ARG POLYGRAPHY_VERSION=0.49.26
ARG TORCH_VERSION=2.10.0+cu130
ARG TORCHVISION_VERSION=0.25.0+cu130
ARG TORCHAUDIO_VERSION=2.10.0+cu130
ARG CUDA_VERSION_DASH=13-0
ARG TORCH_INDEX_SUFFIX=cu130

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget curl git ca-certificates \
    python3.12 python3.12-venv python3.12-dev build-essential \
    libgl1 libglib2.0-0 \
    && wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb \
    && dpkg -i cuda-keyring_1.1-1_all.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       cuda-minimal-build-${CUDA_VERSION_DASH} libcusparse-dev-${CUDA_VERSION_DASH} \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f cuda-keyring_1.1-1_all.deb /usr/lib/python3.12/EXTERNALLY-MANAGED

RUN curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && \
    python3.12 /tmp/get-pip.py pip==26.1.2 && \
    python3.12 -m pip install --no-cache-dir pip-tools==7.6.0 && \
    rm -f /tmp/get-pip.py

ENV PATH=/usr/local/cuda/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64
ENV TORCH_VERSION=${TORCH_VERSION} \
    TORCHVISION_VERSION=${TORCHVISION_VERSION} \
    TORCHAUDIO_VERSION=${TORCHAUDIO_VERSION}

WORKDIR /tmp/build
COPY models-manifest.txt /tmp/build/models-manifest.txt
RUN curl -fSL "https://github.com/Comfy-Org/ComfyUI/archive/refs/tags/${COMFYUI_VERSION}.tar.gz" -o comfyui.tar.gz && \
    mkdir -p ComfyUI && \
    tar xzf comfyui.tar.gz --strip-components=1 -C ComfyUI && \
    rm -f comfyui.tar.gz

# Node packs are pinned to a commit/tag so a rebuild is reproducible; only the
# two enhancement packs that have no official upstream follow a branch.
WORKDIR /tmp/build/ComfyUI/custom_nodes
RUN set -eux; \
    fetch_sha() { \
      local user="$1" repo="$2" sha="$3" dir="$4"; \
      curl -fSL "https://github.com/$user/$repo/archive/$sha.tar.gz" -o "$dir.tar.gz"; \
      mkdir -p "$dir"; tar xzf "$dir.tar.gz" --strip-components=1 -C "$dir"; rm -f "$dir.tar.gz"; \
    }; \
    fetch_sha ltdrdata ComfyUI-Manager "${MANAGER_SHA}" ComfyUI-Manager; \
    fetch_sha kijai ComfyUI-KJNodes "${KJNODES_SHA}" ComfyUI-KJNodes; \
    fetch_sha MoonGoblinDev Civicomfy "${CIVICOMFY_SHA}" Civicomfy; \
    fetch_sha MadiatorLabs ComfyUI-RunpodDirect "${RUNPODDIRECT_SHA}" ComfyUI-RunpodDirect; \
    fetch_sha Kosinkadink ComfyUI-VideoHelperSuite "${VHS_SHA}" ComfyUI-VideoHelperSuite; \
    fetch_sha beautyaiClub comfyui-beautyai "${BEAUTYAI_SHA}" comfyui-beautyai

# The three H3 URL loaders. LoadImageFromUrl / LoadVideoFromURL are vendored
# (their upstream packs would drag in ~100 MB of unrelated dependencies), the
# audio loader ships with the beautyai pack cloned above.
COPY custom_nodes/h3_url_loaders /tmp/build/ComfyUI/custom_nodes/h3_url_loaders

# Enhancement chain for the `enhancers` template set. RIFE and the upscaler come
# from their upstream projects; QwenVL-Mod, TagForge and PerfectVideoResolution
# exist only in their author's repositories (the node ids QwenVL_Unified /
# WildcardProcessor / ComfyUI-PerfectVideoResolution have no other upstream),
# which is exactly why this tier is optional.
RUN set -eux; \
    if [ "$WITH_H3_ENHANCERS" = "true" ]; then \
      cd /tmp/build/ComfyUI/custom_nodes; \
      fetch_sha() { \
        local user="$1" repo="$2" sha="$3" dir="$4"; \
        curl -fSL "https://github.com/$user/$repo/archive/$sha.tar.gz" -o "$dir.tar.gz"; \
        mkdir -p "$dir"; tar xzf "$dir.tar.gz" --strip-components=1 -C "$dir"; rm -f "$dir.tar.gz"; \
      }; \
      fetch_sha silveroxides ComfyUI_RIFE_TensorRT_Auto "${RIFE_TRT_SHA}" ComfyUI-RIFE-TensorRT-Auto; \
      fetch_sha yuvraj108c ComfyUI-Upscaler-Tensorrt "${UPSCALER_TRT_SHA}" ComfyUI-Upscaler-TensorRT-Auto; \
      fetch_sha pixaroma ComfyUI-Pixaroma "${PIXAROMA_SHA}" ComfyUI-Pixaroma; \
      fetch_sha ltdrdata ComfyUI-Impact-Pack "${IMPACT_PACK_SHA}" ComfyUI-Impact-Pack; \
      fetch_sha ltdrdata ComfyUI-Impact-Subpack "${IMPACT_SUBPACK_SHA}" ComfyUI-Impact-Subpack; \
      fetch_sha yolain ComfyUI-Easy-Use "${EASY_USE_SHA}" ComfyUI-Easy-Use; \
      fetch_sha huchukato ComfyUI-QwenVL-Mod "${QWENVL_MOD_SHA}" ComfyUI-QwenVL-Mod; \
      fetch_sha huchukato ComfyUI-PerfectVideoResolution "${PERFECT_VIDEO_RES_SHA}" ComfyUI-PerfectVideoResolution; \
      fetch_sha huchukato ComfyUI-TagForge "${TAGFORGE_SHA}" ComfyUI-TagForge; \
    fi

WORKDIR /tmp/build
RUN set -eux; \
    for repo_dir in ComfyUI ComfyUI/custom_nodes/ComfyUI-Manager ComfyUI/custom_nodes/ComfyUI-KJNodes \
                    ComfyUI/custom_nodes/Civicomfy ComfyUI/custom_nodes/ComfyUI-RunpodDirect \
                    ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite ComfyUI/custom_nodes/comfyui-beautyai; do \
      cd "/tmp/build/$repo_dir"; \
      git init -q; git add -A; git -c user.name=- -c user.email=- commit -q -m "${repo_dir##*/} ${COMFYUI_VERSION}"; \
    done

# Resolve the whole dependency set (ComfyUI + node packs + the CUDA 13.0 PyTorch
# build) into a fully pinned lock file. This is the slowest, most network-bound
# step, so it gets its own layer: a cached requirements.lock survives reruns of
# the install layer below, and `-u -v` keeps progress visible on slow links.
#
# No --generate-hashes: pip-tools then downloads every artifact of every pinned
# version to hash it - including Windows/macOS wheels - which is tens of GB on
# this dependency set. Exact pins + a version check after install cover us
# instead (see the torch verification below).
RUN set -eux; \
    cat ComfyUI/requirements.txt > requirements.in; \
    for node_dir in \
      ComfyUI/custom_nodes/ComfyUI-Manager \
      ComfyUI/custom_nodes/ComfyUI-KJNodes \
      ComfyUI/custom_nodes/Civicomfy \
      ComfyUI/custom_nodes/ComfyUI-RunpodDirect \
      ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite \
      ComfyUI/custom_nodes/comfyui-beautyai \
      ComfyUI/custom_nodes/h3_url_loaders; do \
      if [ -f "$node_dir/requirements.txt" ]; then cat "$node_dir/requirements.txt" >> requirements.in; fi; \
    done; \
    printf '%s\n' GitPython opencv-python jupyter jupyter-resource-usage jupyterlab-nvdashboard >> requirements.in; \
    sed -i -E '/^[[:space:]]*(torch|torchvision|torchaudio)([[:space:]]|[\[<>=!~;#]|$)/d' requirements.in; \
    printf 'torch==%s\ntorchvision==%s\ntorchaudio==%s\n' "$TORCH_VERSION" "$TORCHVISION_VERSION" "$TORCHAUDIO_VERSION" >> requirements.in; \
    echo 'pillow>=12.1.1' >> requirements.in; \
    PIP_INDEX_URL=https://pypi.org/simple \
    PIP_EXTRA_INDEX_URL="https://download.pytorch.org/whl/${TORCH_INDEX_SUFFIX}" \
      python3.12 -u -m piptools compile -v --output-file=requirements.lock --strip-extras --allow-unsafe requirements.in

# Installed without --no-cache-dir on purpose: pip-tools already pulled every
# wheel into ~/.cache/pip while resolving (the PyTorch index has no PEP 658
# metadata), so re-downloading ~2.5GB of CUDA wheels here would be pure waste.
# The cache is dropped again by `pip cache purge` two layers down, and the
# runtime stage never copies it.
RUN set -eux; \
    python3.12 -m pip install --ignore-installed \
      --index-url https://pypi.org/simple \
      --extra-index-url "https://download.pytorch.org/whl/${TORCH_INDEX_SUFFIX}" \
      -r requirements.lock; \
    printf 'torch==%s\ntorchvision==%s\ntorchaudio==%s\n' "$TORCH_VERSION" "$TORCHVISION_VERSION" "$TORCHAUDIO_VERSION" > /tmp/torch-constraints.txt; \
    python3.12 -c 'import importlib.metadata as m, os, sys; expected={"torch":os.environ["TORCH_VERSION"],"torchvision":os.environ["TORCHVISION_VERSION"],"torchaudio":os.environ["TORCHAUDIO_VERSION"]}; bad=[f"{k}: expected {v}, got {m.version(k)}" for k,v in expected.items() if m.version(k)!=v]; sys.exit("\n".join(bad) if bad else 0)' \
      || (echo 'PyTorch version verification failed'; exit 1)

# Enhancement packs are installed after the lock (they bring TensorRT, SAM and
# their own vision stack) while torch stays constrained to the CUDA 13 build.
RUN set -eux; \
    if [ "$WITH_H3_ENHANCERS" = "true" ]; then \
      cd /tmp/build; \
      for node_dir in \
        ComfyUI/custom_nodes/ComfyUI-Easy-Use \
        ComfyUI/custom_nodes/ComfyUI-QwenVL-Mod \
        ComfyUI/custom_nodes/ComfyUI-TagForge \
        ComfyUI/custom_nodes/ComfyUI-PerfectVideoResolution \
        ComfyUI/custom_nodes/ComfyUI-Pixaroma \
        ComfyUI/custom_nodes/ComfyUI-Impact-Pack \
        ComfyUI/custom_nodes/ComfyUI-Impact-Subpack; do \
          if [ -f "$node_dir/requirements.txt" ]; then \
            python3.12 -m pip install --no-cache-dir --no-build-isolation --constraint /tmp/torch-constraints.txt -r "$node_dir/requirements.txt"; \
          fi; \
      done; \
      # Upstream silveroxides/ComfyUI_RIFE_TensorRT_Auto pins its own TensorRT
      # (10.14.1.48). The engines already on the volume are 10.15.1.29, so the
      # matching runtime + polygraphy pair is installed instead of that pin.
      # Build isolation stays on here: the TensorRT sdists build through the
      # wheel_stub backend, which only exists in pip's isolated build env.
      python3.12 -m pip install --no-cache-dir --constraint /tmp/torch-constraints.txt \
        "tensorrt_cu13==${TRT_CU13_VERSION}" \
        "tensorrt_cu13_bindings==${TRT_CU13_VERSION}" \
        "tensorrt_cu13_libs==${TRT_CU13_VERSION}" \
        "polygraphy==${POLYGRAPHY_VERSION}" \
        einops colored cuda-python requests tqdm; \
      python3.12 -m pip install --no-cache-dir --no-deps 'transformers>=5.2.0'; \
    fi; \
    python3.12 -m pip install --no-cache-dir --constraint /tmp/torch-constraints.txt 'runpod>=1.12' requests boto3 'huggingface_hub[cli]' hf_transfer; \
    python3.12 -m pip cache purge

RUN set -eux; \
    mkdir -p /opt/comfyui-baked; \
    cp -a /tmp/build/ComfyUI/. /opt/comfyui-baked/; \
    mkdir -p /opt/comfyui-baked/models/{vae,vae_approx,diffusion_models,unet,text_encoders,clip_projections,loras,checkpoints,LLM,clip,clip_vision,ultralytics/bbox,sams}; \
    printf '%s\n' \
      "COMFYUI_VERSION=${COMFYUI_VERSION}" \
      "MANAGER_SHA=${MANAGER_SHA}" \
      "KJNODES_SHA=${KJNODES_SHA}" \
      "VHS_SHA=${VHS_SHA}" \
      "BEAUTYAI_SHA=${BEAUTYAI_SHA}" \
      "H3_URL_LOADERS=vendored (art-venture 210dc072b1 / mixlab v0.46.0)" \
      "WITH_H3_ENHANCERS=${WITH_H3_ENHANCERS}" \
      "TENSORRT_CU13=${TRT_CU13_VERSION}" \
      "TEMPLATE_SETS=native,enhancers" \
      "H3_WORKER=runpod-comfyui-minimax-h3" \
      "BAKE_MODELS=${BAKE_MODELS}" \
      > /opt/comfyui-baked/.runpod-bundle-version

# Download the model files into the builder output. Hugging Face files use
# the installed `hf` client with Xet disabled; direct curl against a resolve URL
# can return a tiny access/error document from a builder network instead of the
# LFS object. Every file is staged and renamed only after it exists.
RUN set -eux; \
    if [ "$BAKE_MODELS" = "true" ]; then \
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
        stat -c 'bake-model: %n %s bytes' "$dest"; \
      done < /tmp/build/models-manifest.txt; \
      test "$(find /opt/comfyui-baked/models -type f | wc -l)" -eq 14; \
      du -sh /opt/comfyui-baked/models; \
    else \
      echo 'bake-model: disabled'; \
    fi

COPY scripts/prebake-manager-cache.py /tmp/prebake-manager-cache.py
RUN python3.12 /tmp/prebake-manager-cache.py /opt/comfyui-baked/user/__manager/cache

# Completion-word wildcards, only useful for the TagForge prompt node that the
# enhancement chain provides. Fetched individually and tolerantly: upstream
# renames files (pmp/prmpt.yaml is gone at the time of writing) and a missing
# wildcard must not fail the image build.
RUN set -eux; \
    if [ "$WITH_H3_ENHANCERS" = "true" ]; then \
      WILDCARD_DIR=/opt/comfyui-baked/custom_nodes/ComfyUI-TagForge/wildcards; \
      WILDCARD_BASE=https://raw.githubusercontent.com/huchukato/ComfyUI-Garage/master/wildcards; \
      missing=0; \
      for wf in pmp/act.yaml pmp/actff.yaml pmp/actffm.yaml pmp/actmmf.yaml pmp/actsolo.yaml pmp/blwjob.yaml pmp/prmpt.yaml pmp/qwen21.yaml pmp/prmpt/acc.yaml pmp/prmpt/char.yaml pmp/prmpt/clths.yaml pmp/prmpt/exprss.yaml pmp/prmpt/hair.yaml pmp/prmpt/imgcmp.yaml pmp/prmpt/lctns.yaml pmp/prmpt/light.yaml pmp/prmpt/lens.yaml pmp/prmpt/pose.yaml pmp/prmpt/styles.yaml pmp/prmpt/dynmc.yaml mmh3/camera.yaml mmh3/music.yaml mmh3/nsfw.yaml mmh3/style.yaml mmh3/vfx.yaml; do \
        mkdir -p "$WILDCARD_DIR/$(dirname "$wf")"; \
        if ! wget -q --tries=3 --timeout=30 "$WILDCARD_BASE/$wf" -O "$WILDCARD_DIR/$wf"; then \
          rm -f "$WILDCARD_DIR/$wf"; missing=$((missing + 1)); echo "wildcard missing upstream: $wf" >&2; \
        fi; \
      done; \
      echo "wildcards: $(ls -1 "$WILDCARD_DIR"/pmp/*.yaml "$WILDCARD_DIR"/mmh3/*.yaml 2>/dev/null | wc -l) files, $missing missing upstream"; \
    fi

# ============================================================================
# Stage 2: Runtime - Ubuntu 24.04 with the baked CUDA 13.0 Python stack
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg \
    FILEBROWSER_CONFIG=/workspace/runpod-slim/.filebrowser.json

ARG CUDA_VERSION_DASH=13-0
ARG TORCH_VERSION=2.10.0+cu130
ARG TORCHVISION_VERSION=0.25.0+cu130
ARG TORCHAUDIO_VERSION=2.10.0+cu130
ARG BAKE_MODELS=true
ARG FILEBROWSER_VERSION=v2.63.23
ARG FILEBROWSER_SHA256=b14db2bb8033caa3f80205eb6578b2ed0744ebd9e716b790bc4a9703ce909e88

RUN printf 'torch==%s\ntorchvision==%s\ntorchaudio==%s\n' "$TORCH_VERSION" "$TORCHVISION_VERSION" "$TORCHAUDIO_VERSION" > /opt/comfyui-runtime-constraints.txt

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
      git python3 python3.12 python3.12-venv python3.12-dev build-essential libssl-dev \
      wget gnupg xz-utils openssh-client openssh-server curl ca-certificates \
      less net-tools iputils-ping procps openssl ffmpeg rsync libgl1 libglib2.0-0 \
    && wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb \
    && dpkg -i cuda-keyring_1.1-1_all.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends cuda-minimal-build-${CUDA_VERSION_DASH} \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* cuda-keyring_1.1-1_all.deb \
    && rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED

COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/local/share/jupyter /usr/local/share/jupyter
COPY --from=builder /opt/comfyui-baked /opt/comfyui-baked

RUN mkdir -p /usr/local/etc/jupyter/jupyter_server_config.d /workspace/runpod-slim /opt/serverless /opt/workflows && \
    echo '{"ServerApp":{"jpserver_extensions":{"jupyter_server_terminals":true,"jupyterlab":true,"jupyter_resource_usage":true,"jupyterlab_nvdashboard":true}}}' > /usr/local/etc/jupyter/jupyter_server_config.d/extensions.json

RUN curl -fSL "https://github.com/filebrowser/filebrowser/releases/download/${FILEBROWSER_VERSION}/linux-amd64-filebrowser.tar.gz" -o /tmp/fb.tar.gz && \
    echo "${FILEBROWSER_SHA256}  /tmp/fb.tar.gz" | sha256sum -c - && \
    tar xzf /tmp/fb.tar.gz -C /usr/local/bin filebrowser && rm -f /tmp/fb.tar.gz

ENV PATH=/usr/local/cuda/bin:${PATH} \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64 \
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu130 \
    NVIDIA_REQUIRE_CUDA="" \
    NVIDIA_DISABLE_REQUIRE=true \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all \
    HF_TOKEN="" \
    BAKED_MODELS=${BAKE_MODELS} \
    VOLUME_AUTOPOPULATE=false \
    COMFYUI_DIR=/opt/comfyui-baked \
    WORKFLOWS_DIR=/opt/workflows \
    H3_TEMPLATE_SET=native \
    JOB_TIMEOUT_S=1800 \
    COMFYUI_VENV_NAME=.venv-cu130

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

# A Windows checkout used to deliver CRLF shell scripts, and `#!/bin/bash\r`
# makes the kernel fail the exec ("No such file or directory"), which shows up as
# a worker that restarts forever. Normalise here so the image is safe whatever
# the checkout did, and fail the build if a shebang still looks wrong.
RUN set -eux; \
    sed -i 's/\r$//' /opt/serverless/*.sh /opt/serverless/*.py /opt/serverless/models-manifest.txt /opt/workflows/*/*.json; \
    chmod +x /opt/serverless/entrypoint.sh /opt/serverless/populate-volume.sh; \
    head -n 1 /opt/serverless/entrypoint.sh | grep -qx '#!/bin/bash'; \
    head -n 1 /opt/serverless/populate-volume.sh | grep -qx '#!/bin/bash'

WORKDIR /workspace/runpod-slim
EXPOSE 8188 22 8888 8080
# Invoke through bash as well: the script's executable bit is enough on Linux,
# but this removes the shebang from the failure path entirely.
ENTRYPOINT ["/bin/bash", "/opt/serverless/entrypoint.sh"]
