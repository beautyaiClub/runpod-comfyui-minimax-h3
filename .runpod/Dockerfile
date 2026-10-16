# Dockerfile used by the Runpod Hub listing.
#
# The full build (ComfyUI + CUDA 13 torch + TensorRT ...) takes 30-60 minutes on
# a clean machine, and the result is already published as a versioned image, so
# the Hub only needs to pull it. The `.runpod/` directory takes precedence over
# the repository root for Hub builds, which is why this thin wrapper lives here
# and the real build stays in ./Dockerfile for local/CI use.
#
# Keep the tag in sync with the pushed image:
#   wslc build --tag codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0 .
#   wslc push  codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0
FROM codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0

LABEL org.opencontainers.image.source="https://github.com/beautyaiClub/runpod-comfyui-minimax-h3" \
      org.opencontainers.image.description="MiniMax H3 (FL2VA / Ref2VA, video+audio) on ComfyUI v0.40.0 / CUDA 13.0 for Runpod Serverless"

# Nothing to add: the entrypoint, handler, node packs and templates are already
# in the base image. Re-normalise line endings defensively in case a future
# release ships scripts checked out with CRLF (a CRLF shebang makes the worker
# restart forever).
RUN set -eux; \
    sed -i 's/\r$//' /opt/serverless/*.sh /opt/serverless/*.py; \
    head -n 1 /opt/serverless/entrypoint.sh | grep -qx '#!/bin/bash'
