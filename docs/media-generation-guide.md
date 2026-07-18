# Media generation guide

## Image generation

Use `image.generate` with the native `/image/generate` endpoint. The bridge defaults to `safe_mode=false`, `hide_watermark=true`, WebP, one image, and binary response.

`parameters.variants` is the canonical manifest field for image count:

| Requested count | Provider response mode | Serialized fields |
|---:|---|---|
| Omitted or `1` | Binary | `return_binary=true`; `variants` omitted |
| `2`–`4` | JSON | `return_binary=false`; `variants=<count>` |

`return_binary` is an internal transport decision and is rejected in user parameters. Existing manifests containing `variants: 1` remain valid; the logical count is preserved while the optional field is omitted from the wire request. JSON-mode responses use the native `images` array of raw-base64 values. The writer validates and decodes every image before atomically publishing any artifact, preserves response order, and records one-based `variant_index`/`variant_count` metadata.

Sizing is model-specific:

- Pixel models use `width` and `height` and may require a divisor.
- Aspect-ratio models use `aspect_ratio`.
- Resolution-tier models use `resolution`, often with `aspect_ratio`.
- Quality-aware models may accept `quality`.

Prompt structure:

```text
Primary subject + action/pose + environment + composition + camera/lens + lighting + material/style + color language + quality constraints
```

Keep negative prompts concrete. Avoid repeating the positive prompt as a negative. Use seeds for reproducibility, not quality guarantees.

Prompts are passed through verbatim. The bridge does not rewrite an adult age, add sexual details, silently substitute a model ID, or infer a different subject. Model availability, type, offline state, pricing, and constraints come from the exact live catalog entry.

## Image editing

Use:

- `image.edit` for one base image
- `image.multi_edit` for 1–3 images, with the first as the base and remaining images as references/layers

Editing prompts should identify what changes and what remains invariant:

```text
Replace the overcast sky with a warm sunset; preserve the subject, camera angle, facial features, clothing, and foreground geometry.
```

## Upscale and background removal

`image.upscale` accepts scale 2 or 4 and creativity from 0 to 0.02. Lower creativity stays closer to the source. The bridge accepts a local image path, validated image data URL, or validated raw base64, then sends raw base64 only to `/image/upscale`. It preserves the decoded bytes and never includes a `data:image/...;base64,` prefix on that endpoint.

`image.background_remove` returns PNG transparency. The operation does not need a model ID in the request manifest.

## Media input encoding contracts

The bridge normalizes at the endpoint boundary; do not manually rewrite media unless troubleshooting proves the file structure itself is incompatible.

| Workflow | Bridge input | Provider representation |
|---|---|---|
| Image generation | No source media | JSON prompt/parameters |
| Edit / inpaint through `image.edit` | Local path, data URL, or URL | Data URL for local files; existing data URL/URL preserved |
| Multi-image edit | Array of local paths, data URLs, or URLs | Array of data URLs/URLs |
| Background removal | Local path, data URL, or URL | `image` for inline data URL or `image_url` for URL |
| Image upscale | Local path, data URL, or raw base64 | Validated raw base64 without a data-URL prefix |
| Image/reference-to-video | Local path, data URL, or URL | Typed URL field; local files become data URLs |
| TTS / generated audio | Text prompt and typed parameters | JSON; no local media input in the current bridge contract |
| Transcription | Local audio path | Multipart file upload |

Voice cloning is not a distinct supported bridge operation. Do not invent a manifest shape for it; verify a future provider schema and add a typed operation before use.

## Video generation

Video uses an asynchronous queue. Required fields are model, prompt, and duration. Aspect ratio, resolution, audio, and input media depend on live model constraints.

Prompt structure:

```text
Subject + motion + environment + camera movement/cut + aesthetic + lighting + temporal behavior + audio direction
```

Specify temporal changes explicitly: opening frame, action progression, camera path, and ending frame.

## Seedance 2.0

The bundled guide defines three principal variants:

- Text-to-video
- Image-to-video
- Reference-to-video

Reference-to-video supports four prompt-routed workflows:

### Reference

```text
Refer to <Subject 1> in <Image 1> to generate ...
```

### Edit

```text
Strictly edit <Video 1>, changing its ...
```

Name only the intended changes and state preservation requirements.

### Extend

```text
Extend <Video 1>, generate ...
```

Seedance returns newly generated continuation by default. Explicitly request inclusion of the original clip when required.

### Stitch

```text
<Video 1> + transition description + followed by <Video 2>
```

Use canonical case-sensitive reference tokens with one space before the number. Do not create a `workflow` request field.

## TTS

Use `audio.tts`. Required input is text; model-specific voice compatibility is loaded from `/models` where available.

Potential controls include:

- Voice
- Response format
- Speed
- Language hint
- Delivery/style prompt
- Temperature/top-p for models that advertise support

The bridge disables streaming because the host-agent contract expects one completed artifact path.

## Generated audio and music

Use `audio.generate`. The supported fields vary by model and can include:

- Duration
- Lyrics or lyrics optimizer
- Instrumental toggle
- Voice
- Language code
- Speed

Quote first, then queue. Preserve the queue ID.

## Transcription

Use `audio.transcribe` with an explicit local audio file. The bridge sends multipart form data. JSON or text output is written locally with a metadata sidecar.
