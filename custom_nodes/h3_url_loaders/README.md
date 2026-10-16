# h3_url_loaders

Vendored URL loaders for the MyReels MiniMax-H3 graphs. Without these three
nodes the native FL2VA / Ref2VA graphs from `myreels-h3-adapter` cannot be
validated (`LoadImageFromUrl` / `LoadVideoFromURL` / `BeautyAI_LoadAudioFromURL`
are the only non-core nodes those graphs use).

| Node id | Vendored from | Pin | Why not install the pack |
| --- | --- | --- | --- |
| `LoadImageFromUrl` | [sipherxyz/comfyui-art-venture](https://github.com/sipherxyz/comfyui-art-venture) | `210dc072b1` (1.1.7) | 87 nodes; needs timm / fairscale / pytorch_lightning / pycocoevalcap / segment_anything / kornia |
| `LoadVideoFromURL` | [MixLabPro/comfyui-mixlab-nodes](https://github.com/MixLabPro/comfyui-mixlab-nodes) | `v0.46.0` | ~100 MB pack; needs rembg[gpu] / bitsandbytes / clip-interrogator / SenseVoice / swarm |
| `BeautyAI_LoadAudioFromURL` | [beautyaiClub/comfyui-beautyai](https://github.com/beautyaiClub/comfyui-beautyai) | `1788e238b0` | installed from its own repo (`Dockerfile`), not vendored — it is tiny and depends on boto3 only |

Both vendored files keep the upstream node id, `INPUT_TYPES`, `RETURN_TYPES`,
`RETURN_NAMES`, `CATEGORY` and display name, so API-format graphs and the
ComfyUI graph browser behave exactly like the reference instance.

`scripts/verify-native-h3.py` diffs the registered schema of these nodes against
`scripts/fixtures/native-node-parity.json`, which was captured from the working
reference instance at `192.168.10.20:8189`.

## Syncing with upstream

```bash
# image loader
curl -sL https://raw.githubusercontent.com/sipherxyz/comfyui-art-venture/<sha>/modules/utility_nodes.py
# video loader
curl -sL https://raw.githubusercontent.com/MixLabPro/comfyui-mixlab-nodes/<sha>/nodes/FalVideo.py
```

Diff the vendored class against those files after every bump; the parity check
then confirms the container matches the graphs the adapter emits.
