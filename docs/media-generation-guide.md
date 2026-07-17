# Media generation guide

## Image generation

Use `image.generate` with the native `/image/generate` endpoint. The bridge defaults to `safe_mode=false`, `hide_watermark=true`, WebP, one variant, and binary response.

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

## Image editing

Use:

- `image.edit` for one base image
- `image.multi_edit` for 1–3 images, with the first as the base and remaining images as references/layers

Editing prompts should identify what changes and what remains invariant:

```text
Replace the overcast sky with a warm sunset; preserve the subject, camera angle, facial features, clothing, and foreground geometry.
```

## Upscale and background removal

`image.upscale` accepts scale 2 or 4 and creativity from 0 to 0.02. Lower creativity stays closer to the source.

`image.background_remove` returns PNG transparency. The operation does not need a model ID in the request manifest.

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
