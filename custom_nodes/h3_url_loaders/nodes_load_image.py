"""LoadImageFromUrl — vendored from ComfyUI ArtVenture (MIT).

Upstream: https://github.com/sipherxyz/comfyui-art-venture
File:     modules/utility_nodes.py (+ helpers from modules/utils.py)
Commit:   210dc072b1f103f91be18a33bdeb13ab315aaae5  (version 1.1.7)

Only the URL image loader and the helpers it calls are kept. Upstream registers
87 nodes whose imports require timm / fairscale / pytorch_lightning /
pycocoevalcap / segment_anything / kornia — installing that stack just to fetch
an image over HTTP is not worth it for a serverless image.

Schema parity with the reference ComfyUI instance (192.168.10.20:8189) is
asserted by scripts/verify-native-h3.py against scripts/fixtures/.
"""

import base64
import io
import os
import random
from typing import List, Optional
from urllib.parse import parse_qs

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths

# --------------------------------------------------------------------------
# helpers vendored from art-venture modules/utils.py (verbatim)
# --------------------------------------------------------------------------


def pil2numpy(image: Image.Image):
    return np.array(image).astype(np.float32) / 255.0


def numpy2pil(image: np.ndarray, mode=None):
    return Image.fromarray(np.clip(255.0 * image, 0, 255).astype(np.uint8), mode)


def pil2tensor(image: Image.Image):
    return torch.from_numpy(pil2numpy(image)).unsqueeze(0)


def tensor2pil(image: torch.Tensor, mode=None):
    return numpy2pil(image.cpu().numpy().squeeze(), mode=mode)


# --------------------------------------------------------------------------
# helpers vendored from art-venture modules/utility_nodes.py (verbatim)
# --------------------------------------------------------------------------


def prepare_image_for_preview(image: Image.Image, output_dir: str, prefix=None):
    if prefix is None:
        prefix = "preview_" + "".join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))

    # save image to temp folder
    (
        outdir,
        filename,
        counter,
        subfolder,
        _,
    ) = folder_paths.get_save_image_path(prefix, output_dir, image.width, image.height)
    file = f"{filename}_{counter:05}_.png"
    image.save(os.path.join(outdir, file), format="PNG", compress_level=4)

    return {
        "filename": file,
        "subfolder": subfolder,
        "type": "temp",
    }


def load_images_from_url(urls: List[str], keep_alpha_channel=False):
    images: List[Image.Image] = []
    masks: List[Optional[Image.Image]] = []

    for url in urls:
        if url.startswith("data:image/"):
            i = Image.open(io.BytesIO(base64.b64decode(url.split(",")[1])))
        elif url.startswith("file://"):
            url = url[7:]
            if not os.path.isfile(url):
                raise Exception(f"File {url} does not exist")

            i = Image.open(url)
        elif url.startswith("http://") or url.startswith("https://"):
            import requests

            response = requests.get(url, timeout=5)
            if response.status_code != 200:
                raise Exception(response.text)

            i = Image.open(io.BytesIO(response.content))
        elif url.startswith(("/view?", "/api/view?")):
            qs_idx = url.find("?")
            qs = parse_qs(url[qs_idx + 1 :])
            filename = qs.get("name", qs.get("filename", None))
            if filename is None:
                raise Exception(f"Invalid url: {url}")

            filename = filename[0]
            subfolder = qs.get("subfolder", None)
            if subfolder is not None:
                filename = os.path.join(subfolder[0], filename)

            dirtype = qs.get("type", ["input"])
            if dirtype[0] == "input":
                url = os.path.join(folder_paths.get_input_directory(), filename)
            elif dirtype[0] == "output":
                url = os.path.join(folder_paths.get_output_directory(), filename)
            elif dirtype[0] == "temp":
                url = os.path.join(folder_paths.get_temp_directory(), filename)
            else:
                raise Exception(f"Invalid url: {url}")

            i = Image.open(url)
        elif url == "":
            continue
        else:
            url = folder_paths.get_annotated_filepath(url)
            if not os.path.isfile(url):
                raise Exception(f"Invalid url: {url}")

            i = Image.open(url)

        i = ImageOps.exif_transpose(i)
        has_alpha = "A" in i.getbands()
        mask = None

        if "RGB" not in i.mode:
            i = i.convert("RGBA") if has_alpha else i.convert("RGB")

        if has_alpha:
            mask = i.getchannel("A")

        if not keep_alpha_channel:
            image = i.convert("RGB")
        else:
            image = i

        images.append(image)
        masks.append(mask)

    return (images, masks)


class UtilLoadImageFromUrl:
    def __init__(self) -> None:
        self.output_dir = folder_paths.get_temp_directory()
        self.filename_prefix = "TempImageFromUrl"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "Input image paths or URLS one per line. Eg:\nhttps://example.com/image.png\nfile:///path/to/local/image.jpg\ndata:image/png;base64,...",
                        "multiline": True,
                        "dynamicPrompts": False,
                    },
                ),
            },
            "optional": {
                "keep_alpha_channel": (
                    "BOOLEAN",
                    {"default": False, "label_on": "enabled", "label_off": "disabled"},
                ),
                "output_mode": (
                    "BOOLEAN",
                    {"default": False, "label_on": "list", "label_off": "batch"},
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "BOOLEAN")
    OUTPUT_IS_LIST = (True, True, False)
    RETURN_NAMES = ("images", "masks", "has_image")
    CATEGORY = "ArtVenture/Image"
    FUNCTION = "load_image"

    def load_image(self, image: str, keep_alpha_channel=False, output_mode=False):
        urls = image.strip().split("\n")
        pil_images, pil_masks = load_images_from_url(urls, keep_alpha_channel)
        has_image = len(pil_images) > 0
        if not has_image:
            i = torch.zeros((1, 64, 64, 3), dtype=torch.float32, device="cpu")
            m = torch.zeros((64, 64), dtype=torch.float32, device="cpu")
            pil_images = [tensor2pil(i)]
            pil_masks = [tensor2pil(m, mode="L")]

        previews = []
        np_images: list[torch.Tensor] = []
        np_masks: list[torch.Tensor] = []

        for pil_image, pil_mask in zip(pil_images, pil_masks):
            if pil_mask is not None:
                preview_image = Image.new("RGB", pil_image.size)
                preview_image.paste(pil_image, (0, 0))
                preview_image.putalpha(pil_mask)
            else:
                preview_image = pil_image

            previews.append(prepare_image_for_preview(preview_image, self.output_dir, self.filename_prefix))

            np_image = pil2tensor(pil_image)
            if pil_mask:
                np_mask = np.array(pil_mask).astype(np.float32) / 255.0
                np_mask = 1.0 - torch.from_numpy(np_mask)
            else:
                np_mask = torch.zeros((64, 64), dtype=torch.float32, device="cpu")

            np_images.append(np_image)
            np_masks.append(np_mask.unsqueeze(0))

        if output_mode:
            result = (np_images, np_masks, has_image)
        else:
            has_size_mismatch = False
            if len(np_images) > 1:
                for np_image in np_images[1:]:
                    if np_image.shape[1] != np_images[0].shape[1] or np_image.shape[2] != np_images[0].shape[2]:
                        has_size_mismatch = True
                        break

            if has_size_mismatch:
                raise Exception("To output as batch, images must have the same size. Use list output mode instead.")

            result = ([torch.cat(np_images)], [torch.cat(np_masks)], has_image)

        return {"ui": {"images": previews}, "result": result}


# Node ids are the upstream ids: graphs in myreels-h3-adapter reference them by name.
NODE_CLASS_MAPPINGS = {
    "LoadImageFromUrl": UtilLoadImageFromUrl,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadImageFromUrl": "Load Image From URL",
}
