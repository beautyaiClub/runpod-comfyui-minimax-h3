# native template set

Generated — do not hand-edit. These are the graphs `myreels-h3-adapter` posts to
ComfyUI, dumped into API format so the endpoint runs the same node set as the LAN
instances:

```bash
node scripts/generate-native-templates.mjs [--adapter <path-to-myreels-h3-adapter>]
```

| Template | Built by | Slots |
| --- | --- | --- |
| `MiniMaxH3-Native-FL2VA.json` | `buildFl2vaGraph()` | 2 frame loaders (first / last) |
| `MiniMaxH3-Native-Ref2VA.json` | `buildRef2vaNativeGraph()` | 9 reference images, 3 reference videos, 3 reference audios |

Notes

- The graphs only use core ComfyUI nodes plus `LoadImageFromUrl`,
  `LoadVideoFromURL` and `BeautyAI_LoadAudioFromURL`, so they never touch the
  enhancement chain (`WITH_H3_ENHANCERS` can stay `false`).
- Loader slots are padded to the adapter's maximum and filled from the job's
  `images` / `videos` / `audios`; whatever the job does not fill is removed from
  the graph together with its links, so one template covers t2va (no frames),
  i2va (first frame only) and fl2va (first + last).
- Weight names come straight from the adapter, then `generate-native-templates.mjs`
  rewrites them to the files listed in `models-manifest.txt` (the adapter's LAN
  ComfyUI has bf16 weights the volume does not). `config` presets still swap the
  unet / LoRA by file-name needle at job time, and `handler.py` falls back to the
  same needle search if a template name is missing on the volume.
- Checks: `scripts/test-handler-templates.py` (offline graph surgery) and
  `scripts/validate-templates.py` (node schemas against a live `/object_info`).
