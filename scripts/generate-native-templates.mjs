#!/usr/bin/env node
/**
 * Regenerate workflows-api/native/*.json from the graphs the adapter actually
 * posts to ComfyUI.
 *
 * The adapter (myreels-h3-adapter/src/comfy.js) builds both native graphs in
 * JavaScript, so the serverless templates are generated from those builders
 * rather than hand-maintained — that keeps the endpoint and the LAN path on the
 * exact same node set:
 *
 *   buildFl2vaGraph()        -> MiniMaxH3-Native-FL2VA.json  (t2va / i2va / fl2va)
 *   buildRef2vaNativeGraph() -> MiniMaxH3-Native-Ref2VA.json  (ref2va)
 *
 * Loader slots are padded to the maximum the adapter accepts (2 frame slots,
 * 9 reference images, 3 reference videos, 3 reference audios) with example
 * URLs. handler.py fills the slots from the job and drops the leftovers, so an
 * unfilled template degrades to t2va / a smaller reference set instead of
 * failing validation.
 *
 * Usage:
 *   node scripts/generate-native-templates.mjs [--adapter <path-to-myreels-h3-adapter>]
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, '..', 'workflows-api', 'native');

const adapterFlag = process.argv.indexOf('--adapter');
const adapterDir = resolve(
  adapterFlag > -1 ? process.argv[adapterFlag + 1] : join(here, '..', '..', 'myreels-h3-adapter'),
);
const adapterModule = pathToFileURL(join(adapterDir, 'src', 'comfy.js')).href;

const placeholder = (kind, index) => `https://example.invalid/${kind}-${index + 1}.png`;
const placeholderVideo = (index) => `https://example.invalid/ref-video-${index + 1}.mp4`;
const placeholderAudio = (index) => `https://example.invalid/ref-audio-${index + 1}.mp3`;

const comfy = await import(adapterModule);

/**
 * The adapter names the weights the way its own LAN ComfyUI has them. This
 * worker's network volume is described by models-manifest.txt, so the generated
 * templates point at those files instead — otherwise a job without an explicit
 * `config` preset would ask for a file that is not on the volume.
 *
 * `config` presets (native / native_turbo / r2va_singularity / …) still rewrite
 * unet and LoRA by file-name needle at job time, so this is only the default.
 */
const MANIFEST_NAMES = {
  diffusion_models: {
    'minimax_h3_fl2va_pruned_bf16.safetensors': 'minimax_h3_fl2va_pruned_int8_convrot.safetensors',
    'minimax_h3_ref2va_pruned_bf16.safetensors': 'minimax_h3_fl2va_pruned_int8_convrot.safetensors',
  },
  text_encoders: {
    'qwen3vl_32b_minimax_h3_int8_convrot.safetensors': 'qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors',
  },
  vae: {
    'MiniMax-H3-video_vae.safetensors': 'minimax_h3_video_vae_fp16.safetensors',
  },
  loras: {
    'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors':
      'minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors',
  },
};

const WEIGHT_INPUTS = {
  unet_name: 'diffusion_models',
  clip_name: 'text_encoders',
  vae_name: 'vae',
  lora_name: 'loras',
};

function rewriteWeights(graph) {
  const changes = [];
  for (const node of Object.values(graph)) {
    for (const [input, kind] of Object.entries(WEIGHT_INPUTS)) {
      const current = node.inputs?.[input];
      const mapped = MANIFEST_NAMES[kind]?.[current];
      if (typeof current === 'string' && mapped) {
        node.inputs[input] = mapped;
        changes.push(`${current} -> ${mapped}`);
      }
    }
  }
  return changes;
}

// t2va / i2va / fl2va share one builder: the frame slots are optional.
const fl2va = comfy.buildFl2vaGraph({
  first_frame_url: placeholder('first-frame', 0),
  last_frame_url: placeholder('last-frame', 0),
});

// ref2va: pad to the adapter's maxima so every slot the adapter supports has a
// home in the template; handler.py prunes whatever the job does not fill.
const ref2va = comfy.buildRef2vaNativeGraph({
  images: Array.from({ length: 9 }, (_, i) => placeholder('ref-image', i)),
  videos: Array.from({ length: 3 }, (_, i) => placeholderVideo(i)),
  audios: Array.from({ length: 3 }, (_, i) => placeholderAudio(i)),
});

const templates = {
  'MiniMaxH3-Native-FL2VA.json': fl2va,
  'MiniMaxH3-Native-Ref2VA.json': ref2va,
};

mkdirSync(outDir, { recursive: true });
for (const [name, graph] of Object.entries(templates)) {
  const rewrites = rewriteWeights(graph);
  writeFileSync(join(outDir, name), `${JSON.stringify(graph, null, 2)}\n`);
  const counts = Object.values(graph).reduce((acc, node) => {
    acc[node.class_type] = (acc[node.class_type] ?? 0) + 1;
    return acc;
  }, {});
  console.log(`${name}: ${Object.keys(graph).length} nodes — ${Object.entries(counts).map(([k, v]) => `${k}x${v}`).join(', ')}`);
  for (const change of rewrites) console.log(`  weight: ${change}`);
}
console.log(`written to ${outDir}`);
