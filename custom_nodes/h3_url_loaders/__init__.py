"""URL loaders required by the MyReels MiniMax-H3 graphs.

These three nodes are the only non-core nodes that the graphs in
myreels-h3-adapter/src/comfy.js reference for the native FL2VA / Ref2VA paths:

  LoadImageFromUrl           <- ComfyUI ArtVenture   (sipherxyz/comfyui-art-venture)
  LoadVideoFromURL           <- Mixlab nodes         (MixLabPro/comfyui-mixlab-nodes)
  BeautyAI_LoadAudioFromURL  <- BeautyAI nodes       (beautyaiClub/comfyui-beautyai)

The first two upstream packs pull in very large dependency trees (timm,
pytorch_lightning, rembg, bitsandbytes, clip-interrogator, SenseVoice, ...) for
nodes we never call, so only the two loader implementations are vendored here.
Node ids, input schema, return types, categories and display names are kept
byte-for-byte compatible with upstream; see nodes_*.py headers for provenance.
"""

from .nodes_load_image import (
    NODE_CLASS_MAPPINGS as IMAGE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as IMAGE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .nodes_load_video import (
    NODE_CLASS_MAPPINGS as VIDEO_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as VIDEO_NODE_DISPLAY_NAME_MAPPINGS,
)

NODE_CLASS_MAPPINGS = {}
NODE_CLASS_MAPPINGS.update(IMAGE_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(VIDEO_NODE_CLASS_MAPPINGS)

NODE_DISPLAY_NAME_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS.update(IMAGE_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(VIDEO_NODE_DISPLAY_NAME_MAPPINGS)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
