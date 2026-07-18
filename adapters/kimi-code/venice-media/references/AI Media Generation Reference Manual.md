# AI Media Generation Reference Manual

## Purpose

This document provides operational guidance for AI agents responsible for planning, prompting, generating, evaluating, refining, and delivering AI-generated media.

It applies to:

* Text-to-image
* Image-to-image
* Image editing
* Image expansion and inpainting
* Image upscaling
* Text-to-video
* Image-to-video
* Video-to-video
* Text-to-speech
* Voice generation
* Speech-to-speech
* Sound-effect generation
* Music generation
* Multimodal campaigns and asset sets

The primary objective is not merely to produce a valid output. The objective is to produce media that is:

* Faithful to the user’s intent
* Visually or acoustically coherent
* Technically valid
* Consistent across iterations
* Appropriate for its destination
* Reproducible where possible
* Efficient in model usage and generation cost
* Easy to review, revise, and integrate

---

# 1. Core Operating Principles

## 1.1 Intent fidelity is the primary quality metric

A technically impressive output is still a failure when it does not match the requested subject, purpose, composition, tone, audience, or delivery format.

Agents should identify the following before generation:

* What is being created?
* Why is it being created?
* Who is the audience?
* Where will it be displayed or played?
* What must remain unchanged?
* What may be interpreted creatively?
* What would make the result unusable?

Convert vague user language into explicit production constraints.

Example:

User request:

> Create a cinematic image of a futuristic city.

Production interpretation:

* Medium: still image
* Subject: futuristic urban environment
* Mood: cinematic, large-scale, dramatic
* Likely composition: wide establishing shot
* Lighting: directional or atmospheric
* Detail level: high
* Intended use: unknown
* Required clarification only when consequential: orientation, visual era, daytime, human presence, realism level

Do not over-question the user when reasonable defaults can be selected.

---

## 1.2 Separate content, composition, and treatment

Prompts become more controllable when divided into three conceptual layers.

### Content

What exists in the media:

* Subjects
* Objects
* Environment
* Actions
* Wardrobe
* Props
* Architecture
* Weather
* Time period

### Composition

How the content is arranged:

* Camera position
* Framing
* Subject placement
* Perspective
* Depth
* Motion
* Shot size
* Lens behavior
* Negative space

### Treatment

How the output is rendered:

* Visual style
* Lighting
* Color palette
* Texture
* Film stock
* Illustration technique
* Audio character
* Vocal delivery
* Musical genre
* Production quality

Weak prompt:

> A cool cyberpunk woman.

Controlled prompt:

> Waist-up portrait of a cyberpunk courier standing beneath an elevated transit line, positioned slightly right of center with open negative space on the left. Eye-level camera, shallow depth of field, wet pavement reflections, soft magenta signage, cool cyan rim light, realistic skin texture, restrained futuristic clothing, cinematic photography.

---

## 1.3 Use explicit priorities

Not every instruction has equal importance. Agents should distinguish among:

* Mandatory constraints
* Strong preferences
* Optional enhancements
* Prohibited elements

Example:

Mandatory:

* 16:9 composition
* Product logo must remain unchanged
* No text generated inside the image

Strong preference:

* Premium editorial lighting
* Warm neutral palette

Optional:

* Light atmospheric haze
* Subtle reflections

Prohibited:

* Additional products
* Distorted packaging
* Watermarks
* Brand-name substitutions

Important requirements should appear early and be reinforced through generation parameters or negative constraints.

---

## 1.4 Preserve user-provided source material

For editing, compositing, image-to-video, and transformation tasks, identify invariants before modifying the source.

Common invariants include:

* Identity
* Facial structure
* Product geometry
* Logo shape
* Text
* Brand colors
* Clothing
* Pose
* Camera angle
* Background layout
* Object count

Do not assume that “improve,” “stylize,” or “make cinematic” authorizes changing the subject’s identity or the product’s design.

---

## 1.5 Generate for the destination

The destination affects composition, pacing, text size, aspect ratio, duration, and file properties.

Examples:

### Social feed

* Strong focal point
* Immediate readability
* Safe space for interface overlays
* Platform-native aspect ratio
* Fast visual comprehension

### Website hero

* Controlled negative space for copy
* Wide crop tolerance
* Reduced detail behind text
* Strong subject-background separation

### Mobile wallpaper

* Portrait orientation
* Important elements away from clock and icon zones
* Detail that remains readable behind UI elements
* Sufficient edge coverage for cropping

### Product listing

* Accurate object representation
* Neutral or intentional background
* Consistent perspective
* No misleading accessories
* Clear silhouette

### Short-form video

* Strong opening frame
* Motion established immediately
* Readable subject at mobile size
* Minimal dead time
* Loop-friendly ending where applicable

### Voice assistant output

* Natural pronunciation
* Moderate pacing
* Limited sentence complexity
* No excessive dramatic delivery
* Correct handling of names, acronyms, and numbers

---

# 2. Prompt Architecture

## 2.1 Recommended prompt structure

Use a prompt structure that follows this order:

1. Output type
2. Primary subject
3. Action or state
4. Environment
5. Composition
6. Camera and optics
7. Lighting
8. Color
9. Material and texture
10. Style or rendering treatment
11. Quality constraints
12. Exclusions

Template:

```text
Create a [output type] featuring [primary subject] [performing action or existing in a defined state] within [environment].

Composition: [framing, subject placement, perspective, depth, negative space].

Camera: [shot type, angle, lens behavior, focus behavior, movement if applicable].

Lighting: [source, direction, softness, contrast, atmosphere].

Color: [primary palette, accent colors, saturation and contrast characteristics].

Materials and detail: [surface characteristics, texture, realism requirements].

Treatment: [photographic, illustrative, cinematic, graphic, historical, commercial, or other stylistic direction].

Quality requirements: [coherence, anatomy, geometry, temporal stability, text accuracy, identity preservation].

Exclude: [unwanted objects, artifacts, styles, distortions, branding, text, watermarks].
```

Not every field must be included. Include fields that materially affect the output.

---

## 2.2 Avoid prompt keyword dumping

A long sequence of disconnected adjectives often produces inconsistent results.

Weak:

```text
masterpiece, best quality, cinematic, 8k, beautiful, amazing, detailed,
professional, realistic, dramatic, trending, award winning
```

Improved:

```text
Commercial studio photograph with controlled softbox lighting, realistic
surface detail, sharp focus on the product, gradual background falloff,
restrained contrast, and accurate proportions.
```

Use descriptive cause-and-effect language rather than generic quality tokens.

---

## 2.3 Resolve contradictions

Agents should inspect prompts for conflicting instructions.

Common contradictions:

* “Minimalist” and “extremely detailed”
* “Flat graphic design” and “photorealistic”
* “Soft lighting” and “hard noon shadows”
* “Static locked camera” and “dynamic handheld movement”
* “Whispered voice” and “powerful projected speech”
* “Slow ambient music” and “high-energy rapid rhythm”
* “Preserve the exact pose” and “change to a running pose”

When contradictions exist, prioritize:

1. Explicit user constraints
2. Most recent instructions
3. Instructions tied to the intended use
4. Conservative preservation of source material

---

## 2.4 Use negative prompting selectively

Negative prompts are most useful when they identify probable failure modes.

Effective exclusions:

* Extra fingers
* Duplicate objects
* Warped product labels
* Illegible text
* Floating accessories
* Camera shake
* Temporal flicker
* Sudden scene cuts
* Unmotivated zoom
* Background subject duplication
* Robotic speech cadence
* Excessive reverb
* Clipping or distortion

Avoid enormous generic negative lists. They may suppress desirable details or compete with the positive prompt.

Negative instructions should describe what must not occur, not replace a weak positive description.

---

## 2.5 Do not depend on generated text when exact typography matters

Generative models frequently produce malformed letters, incorrect spelling, inconsistent logos, and unstable text across frames.

Preferred workflow:

1. Generate the background or visual without embedded text.
2. Reserve intentional negative space.
3. Add exact typography using a deterministic graphics tool.
4. Validate spelling, alignment, contrast, and safe margins.

Use native text generation only when:

* The model has demonstrated reliable typography
* The text is short
* Minor stylistic variation is acceptable
* The output will still be reviewed manually

---

# 3. Image Generation

## 3.1 Composition fundamentals

Before generation, define:

* Aspect ratio
* Shot size
* Subject count
* Subject placement
* Camera elevation
* Viewing angle
* Perspective
* Foreground, midground, and background
* Negative-space requirements
* Crop tolerance

Common shot types:

* Extreme close-up
* Close-up
* Head-and-shoulders
* Medium shot
* Three-quarter shot
* Full-body shot
* Wide shot
* Establishing shot
* Overhead shot
* Macro shot
* Isometric view

Avoid relying on “cinematic” to determine composition. Specify the actual framing.

---

## 3.2 Camera and lens language

Camera terminology can guide perspective and depth when supported by the model.

Useful descriptions:

* Wide-angle environmental view
* Natural eye-level perspective
* Compressed telephoto perspective
* Shallow depth of field
* Deep focus
* Macro detail
* Tilted low-angle shot
* Symmetrical frontal view
* Over-the-shoulder framing
* Orthographic product view
* Isometric perspective

Do not overload prompts with incompatible lens descriptions.

Examples:

* A macro close-up should not simultaneously behave like a distant wide establishing shot.
* Strong telephoto compression conflicts with exaggerated wide-angle distortion.
* Deep focus conflicts with heavily blurred foreground and background unless intentionally staged.

---

## 3.3 Lighting design

Lighting should identify source, direction, quality, and purpose.

### Source

* Sunlight
* Overcast sky
* Window light
* Softbox
* Neon signage
* Candlelight
* Practical lamps
* Stage lights
* Screen glow
* Volumetric environmental light

### Direction

* Front
* Side
* Back
* Rim
* Top
* Underlighting
* Three-quarter key

### Quality

* Hard
* Soft
* Diffused
* Specular
* Dappled
* Low contrast
* High contrast

### Purpose

* Separate subject from background
* Reveal texture
* Hide detail
* Create tension
* Produce a commercial finish
* Simulate natural environmental conditions

Example:

```text
Soft directional window light from camera left, subtle cool fill from the
room, low-contrast shadows, and a narrow warm rim light separating the
subject from the background.
```

---

## 3.4 Color control

Define color using relationships rather than isolated color names.

Useful specifications:

* Warm subject against cool background
* Muted neutral base with one saturated accent
* Low-saturation pastel palette
* High-contrast complementary colors
* Analogous blue-green palette
* Monochromatic grayscale with warm highlights
* Filmic highlight roll-off
* Lifted shadows
* Restrained black levels
* Clean commercial whites

Agents should avoid asking for too many dominant colors. Establish:

* Base color
* Secondary color
* Accent color
* Relative saturation
* Contrast behavior

---

## 3.5 Material and surface accuracy

Describe how surfaces should interact with light.

Examples:

* Brushed aluminum with directional highlights
* Matte polymer with soft diffuse reflections
* Glossy ceramic with clean specular highlights
* Frosted glass with partial translucency
* Worn leather with fine creases
* Damp asphalt with broken reflections
* Translucent fabric illuminated from behind
* Natural skin with visible pores and restrained retouching

This is especially important for:

* Product imagery
* Architecture
* Food
* Vehicles
* Fashion
* Jewelry
* Industrial design

---

## 3.6 Human anatomy and identity

For people, specify:

* Number of subjects
* Relative positions
* Visible body regions
* Pose
* Hand placement
* Eye direction
* Expression
* Clothing
* Identity-preservation requirement
* Degree of retouching

Common failure modes:

* Extra fingers
* Fused hands
* Incorrect limb count
* Asymmetric eyes
* Inconsistent earrings or accessories
* Identity drift
* Incorrect reflections
* Clothing merging into the body
* Background figures resembling the main subject

Use simpler poses when reliability is more important than novelty.

For identity-sensitive editing:

* Minimize unnecessary changes
* Preserve facial proportions
* Avoid overly broad style transformations
* Use reference images with clear lighting and unobstructed features
* Validate identity separately from aesthetic quality

---

## 3.7 Product generation

Product imagery requires stricter geometry than general art generation.

Specify:

* Exact product count
* Orientation
* Camera view
* Packaging proportions
* Cap, handle, button, or connector placement
* Surface finish
* Label-preservation requirements
* Shadow direction
* Background surface
* Supporting props
* Whether props may touch the product

Do not permit the model to improvise packaging text, logos, ports, or controls when accuracy matters.

Recommended production method:

1. Preserve or composite the real product.
2. Generate the environment and supporting scene.
3. Match lighting and shadows.
4. Add deterministic labels and typography afterward.
5. Check geometry against the source.

---

## 3.8 Image editing and inpainting

An editing prompt should explicitly state:

* What region changes
* What region remains untouched
* What replaces the selected region
* Required lighting and perspective match
* Edge blending requirements
* Texture continuity
* Object interaction
* Shadow and reflection changes

Example:

```text
Replace only the selected wall area with exposed red brick. Preserve the
subject, furniture, floor, windows, lighting direction, camera position,
and all unmasked details. Match the existing perspective and depth of
field. Continue natural shadows across the new brick surface.
```

A weak edit prompt such as “make the background nicer” gives the model excessive freedom.

---

## 3.9 Upscaling

Upscaling may involve either:

* Resolution enhancement
* Detail synthesis
* Restoration
* Denoising
* Sharpening
* Compression cleanup

These are not identical.

Specify whether the upscaler should:

* Preserve the source exactly
* Add plausible fine detail
* Restore faces
* Remove noise
* Repair compression
* Avoid changing line art
* Avoid modifying text
* Avoid increasing skin texture
* Avoid creating false edges

Always compare the upscale against the original for:

* Identity drift
* Changed text
* New objects
* Altered patterns
* Over-sharpening
* Plastic skin
* Halo artifacts
* Fabric pattern mutation

---

# 4. Video Generation

## 4.1 Treat video as a sequence, not a moving image

A video prompt must describe temporal behavior.

Define:

* Initial state
* Subject action
* Environmental motion
* Camera motion
* Timing
* Ending state
* Transition behavior
* Elements that must remain stable

Example:

```text
The shot begins with a stationary medium-wide view of the cyclist beneath
the streetlight. Over the next four seconds, the cyclist slowly looks over
their shoulder while light rain falls and distant traffic passes. The
camera performs a subtle forward dolly without panning. End with the
cyclist facing three-quarters toward the camera. No cuts or sudden motion.
```

---

## 4.2 Motion hierarchy

Assign motion deliberately.

### Primary motion

The main subject action.

### Secondary motion

Hair, clothing, foliage, particles, water, traffic, background people.

### Camera motion

Pan, tilt, dolly, truck, crane, orbit, zoom, handheld drift, locked-off shot.

### Lighting motion

Flicker, passing reflections, changing sunlight, screen illumination.

Too many independent motions often produce instability. Use one dominant movement and a small number of supporting movements.

---

## 4.3 Camera movement

Use specific camera terminology.

### Locked camera

No camera translation, rotation, or zoom.

### Pan

Camera rotates horizontally.

### Tilt

Camera rotates vertically.

### Dolly

Camera physically moves forward or backward.

### Truck

Camera moves laterally.

### Crane or pedestal

Camera moves vertically.

### Orbit

Camera moves around the subject.

### Zoom

Lens changes focal length without moving the camera.

### Handheld

Small irregular camera movement.

Avoid combining several movements in a short clip unless the model reliably supports complex choreography.

---

## 4.4 Temporal consistency

Key consistency targets:

* Face
* Body proportions
* Clothing
* Product shape
* Object count
* Background geometry
* Light direction
* Color palette
* Camera axis
* Prop location
* Text and logos

Common temporal defects:

* Facial mutation
* Melting hands
* Object duplication
* Clothing changes
* Background warping
* Unexplained camera jumps
* Pulsing sharpness
* Exposure flicker
* Motion reversal
* Subject teleportation

Reduce these risks by:

* Keeping clips short
* Using a strong reference frame
* Limiting motion complexity
* Avoiding unnecessary scene transitions
* Keeping the camera path simple
* Explicitly naming stable elements
* Extending approved clips rather than regenerating complete long scenes

---

## 4.5 Image-to-video prompting

Do not redescribe the source image in ways that conflict with it.

Focus the prompt on:

* Motion
* Camera behavior
* Environmental changes
* Temporal constraints
* Preservation requirements

Example:

```text
Preserve the person’s identity, clothing, pose, environment, lighting, and
camera angle from the source image. Add a gentle breeze moving the loose
hair and jacket fabric. The subject blinks once and shifts their gaze
slightly toward camera right. Use a very slow forward camera push. No cuts,
no body repositioning, and no background transformation.
```

---

## 4.6 Shot planning

Complex videos should be divided into shots.

For each shot, define:

* Shot identifier
* Duration
* Starting frame
* Subject
* Action
* Camera
* Lighting
* Audio
* Transition
* Continuity requirements

Example:

```text
SHOT 01 — 0:00–0:04
Wide establishing shot of the empty station platform at night.
Locked camera. Light rain. Train lights appear in the distance.

SHOT 02 — 0:04–0:08
Medium profile shot of the traveler looking toward the incoming train.
Slow lateral camera movement. Preserve wardrobe and lighting continuity.

SHOT 03 — 0:08–0:12
Close-up of the traveler’s hand tightening around the ticket.
Shallow depth of field. Train sound increases. Hard cut at the end.
```

Generate and review shots independently when possible.

---

## 4.7 Loop construction

For seamless loops:

* Match initial and final subject position
* Avoid irreversible actions
* Use cyclic movement
* Avoid changing lighting states
* Keep camera movement circular or return it to origin
* Maintain particle continuity
* Avoid cuts unless hidden by motion

Suitable loop motions:

* Breathing
* Blinking
* Hair moving in wind
* Floating particles
* Rippling water
* Rotating signage
* Ambient machinery
* Gentle camera orbit returning to start

---

# 5. Text-to-Speech and Voice Generation

## 5.1 Prepare text for speech

Written prose is not always suitable for spoken delivery.

Speech scripts should use:

* Shorter sentences
* Clear punctuation
* Natural contractions
* Explicit pronunciation guidance
* Written-out ambiguous numbers
* Reduced parenthetical content
* Strategic paragraph breaks

Rewrite:

> Deployment will begin at 18:30 UTC, with phase-two validation occurring subsequently.

As:

> Deployment begins at eighteen thirty, coordinated universal time. Phase two validation will follow.

---

## 5.2 Voice direction

Define:

* Speaker profile
* Tone
* Energy
* Pace
* Pitch
* Emotional state
* Formality
* Accent or dialect only when appropriate
* Recording environment
* Distance from microphone
* Intended audience

Example:

```text
Calm adult narrator with a neutral conversational delivery. Moderate pace,
low dramatic emphasis, precise pronunciation, and subtle warmth. Recorded
close to a studio microphone with minimal room sound.
```

Avoid vague terms such as “good voice” or “professional voice.”

---

## 5.3 Prosody control

Prosody includes:

* Rhythm
* Stress
* Intonation
* Pauses
* Phrase length
* Emotional emphasis

Agents should use punctuation and sentence design to influence prosody.

Examples:

* Commas create short pauses.
* Periods create clear phrase boundaries.
* Em dashes may produce dramatic interruption.
* Ellipses may create hesitation but can sound unnatural.
* Exclamation marks increase intensity and should be used sparingly.
* Paragraph breaks can create longer pauses.

Do not overuse markup or punctuation unless the model explicitly supports it.

---

## 5.4 Pronunciation management

For difficult terms:

* Spell acronyms as spoken words or individual letters
* Use phonetic hints supported by the provider
* Rewrite symbols
* Expand abbreviations
* Test proper names independently
* Avoid leaving URLs in raw form

Examples:

```text
API → A P I
SQL → sequel or S Q L, depending on intent
2026 → twenty twenty-six
3.5% → three point five percent
example.com → example dot com
```

Maintain a pronunciation dictionary for recurring projects.

---

## 5.5 Speech quality review

Check:

* Mispronunciations
* Missing words
* Repeated words
* Unnatural pauses
* Incorrect emotional emphasis
* Inconsistent voice identity
* Excessive sibilance
* Clipping
* Noise
* Abrupt ending
* Poor loudness consistency

Review the complete output with headphones. Do not rely only on waveform inspection.

---

# 6. Sound Effects and Audio Generation

## 6.1 Describe sound in layers

A useful sound-effects prompt may include:

* Primary event
* Material
* Environment
* Distance
* Perspective
* Duration
* Intensity
* Tail or decay
* Background ambience
* Exclusions

Example:

```text
A heavy steel security door closing inside a large concrete corridor,
heard from approximately six meters away. Deep mechanical impact, short
metallic resonance, subtle room echo, and a quiet ventilation hum in the
background. No music, speech, alarms, or additional footsteps.
```

---

## 6.2 Acoustic perspective

Define where the listener is located.

Examples:

* Close-miked
* Across a room
* Behind a closed door
* Outdoors at a distance
* Underwater
* Inside a vehicle
* In a narrow hallway
* In a large reverberant hall
* From the subject’s first-person perspective

Perspective affects:

* Loudness
* Frequency balance
* Reverb
* Transient sharpness
* Stereo width

---

## 6.3 Avoid overloaded sound prompts

Generate complex soundscapes in stems where possible:

* Dialogue
* Foreground effects
* Background effects
* Ambience
* Music

This provides better control over:

* Timing
* Mixing
* Replacement
* Loudness
* Spatial placement
* Revision

---

# 7. Music Generation

## 7.1 Define musical structure

Describe:

* Genre
* Subgenre
* Mood
* Tempo
* Meter
* Key or tonal character
* Instrumentation
* Arrangement
* Energy curve
* Section structure
* Vocal presence
* Production style
* Duration
* Ending behavior

Example:

```text
A ninety-second instrumental synthwave cue at approximately 105 BPM.
Minor-key harmony, pulsing analog bass, gated snare, restrained arpeggios,
and a distant melodic lead.

Structure:
0:00–0:15 atmospheric introduction
0:15–0:40 main groove
0:40–1:05 expanded section with stronger drums
1:05–1:20 reduced breakdown
1:20–1:30 clean resolved ending

No vocals and no abrupt tempo changes.
```

---

## 7.2 Reference attributes, not protected imitation

Prefer describing musical attributes:

* Sparse piano-led arrangement
* Distorted industrial percussion
* Dreamlike layered vocals
* Seventies-style analog production
* Baroque counterpoint
* Modern cinematic trailer dynamics
* Lo-fi tape texture
* Minimal ambient composition

Do not depend on direct imitation of a living artist or exact reproduction of an existing work.

---

## 7.3 Functional music

Music should support its use case.

### Dialogue underscore

* Limited midrange congestion
* Controlled dynamics
* Minimal melodic distraction
* Predictable phrase structure

### Trailer

* Strong energy escalation
* Clear edit points
* Impact moments
* Defined ending

### Game loop

* Seamless loop
* Stable tempo
* No final cadence unless required
* Layer-friendly arrangement

### Meditation

* Slow evolution
* Minimal sudden transients
* Long sustained textures
* Consistent loudness

### Product advertisement

* Immediate identity
* Clean structure
* Short memorable motif
* Precise duration

---

# 8. Cross-Modal Consistency

## 8.1 Maintain a shared production brief

When generating images, video, speech, and music for the same project, use a shared brief containing:

* Project objective
* Audience
* Story
* Brand values
* Visual vocabulary
* Color palette
* Character descriptions
* Environment descriptions
* Camera language
* Voice direction
* Sound palette
* Music direction
* Prohibited elements

Do not allow each modality to independently reinterpret the project.

---

## 8.2 Character continuity sheet

For recurring characters, record:

* Name or identifier
* Age range
* Face shape
* Hair
* Eyes
* Skin tone
* Body proportions
* Clothing
* Accessories
* Distinguishing features
* Typical expressions
* Movement style
* Voice characteristics
* Prohibited mutations

Use the same descriptors consistently. Avoid replacing precise descriptors with synonyms between generations when consistency is required.

---

## 8.3 Environment continuity sheet

Record:

* Location
* Layout
* Architecture
* Materials
* Lighting
* Time of day
* Weather
* Signage
* Furniture
* Object positions
* Ambient sound
* Color palette

This is necessary for multi-shot sequences and repeated campaigns.

---

# 9. Parameters and Model Control

## 9.1 Model selection

Select models based on task characteristics rather than reputation alone.

Evaluate:

* Modality support
* Prompt adherence
* Identity preservation
* Text rendering
* Editing support
* Temporal consistency
* Maximum resolution
* Maximum duration
* Supported aspect ratios
* Generation speed
* Seed support
* Reference-image support
* Safety restrictions
* Commercial terms
* Cost
* API reliability

Use specialized models for specialized operations when appropriate.

Examples:

* A strong text-to-image model may still be poor at inpainting.
* An artistic model may be unsuitable for product accuracy.
* A fast video model may be useful for motion tests but not final delivery.
* A speech model with strong emotion may be inappropriate for technical narration.

---

## 9.2 Seed management

When supported, seeds help reproduce or explore variations.

Store:

* Model identifier
* Model version
* Seed
* Prompt
* Negative prompt
* Dimensions
* Guidance value
* Step count
* Sampler or scheduler
* Reference assets
* Strength or denoise value
* Timestamp

A seed does not guarantee identical results across:

* Model updates
* Provider changes
* Hardware changes
* Scheduler changes
* Parameter changes

Treat it as a reproducibility aid, not a permanent guarantee.

---

## 9.3 Guidance or prompt-strength values

Higher guidance may increase prompt adherence but can also create:

* Harsh contrast
* Oversaturation
* Artificial texture
* Reduced natural variation
* Composition rigidity

Lower guidance may produce:

* More natural imagery
* Greater creativity
* Weaker adherence
* Missing objects
* Style drift

Use moderate defaults and adjust based on observed failures.

---

## 9.4 Steps and quality settings

More inference steps do not always produce proportionally better output.

Excessive steps may:

* Increase cost
* Increase latency
* Overcook texture
* Produce diminishing returns

Use lower-cost exploratory settings for:

* Composition tests
* Prompt validation
* Motion tests
* Style selection

Use final-quality settings only after the direction is approved.

---

## 9.5 Image-to-image strength

Low transformation strength:

* Preserves composition
* Preserves identity
* Produces subtle changes
* May fail to implement major edits

High transformation strength:

* Allows substantial redesign
* Increases identity and geometry drift
* May ignore source details
* Can change camera position or layout

Choose strength according to the intended edit magnitude.

---

# 10. Iteration Strategy

## 10.1 Change one major variable at a time

When diagnosing output quality, avoid changing the model, seed, prompt, aspect ratio, reference image, and parameters simultaneously.

Use controlled iteration:

1. Establish a baseline.
2. Identify the largest failure.
3. Change one major factor.
4. Compare outputs.
5. Preserve successful settings.
6. Continue to the next failure.

This makes results interpretable.

---

## 10.2 Use staged generation

Recommended workflow:

### Stage 1: Brief

Define purpose, audience, format, and hard constraints.

### Stage 2: Direction exploration

Generate several clearly different concepts.

### Stage 3: Composition selection

Choose framing, layout, pacing, or shot design.

### Stage 4: Subject refinement

Correct identity, anatomy, geometry, wardrobe, and materials.

### Stage 5: Treatment refinement

Adjust color, lighting, texture, mood, and audio character.

### Stage 6: Technical finishing

Upscale, denoise, edit, mix, master, composite, or add typography.

### Stage 7: Validation

Review against the original requirements.

Do not spend final-quality compute before the composition is approved.

---

## 10.3 Distinguish correction from variation

A correction fixes a defect while preserving the approved direction.

A variation deliberately explores a different direction.

Correction prompt:

```text
Preserve the approved composition, lighting, wardrobe, color palette, and
camera angle. Correct only the left hand anatomy and remove the duplicate
background object.
```

Variation prompt:

```text
Create an alternate version using a lower camera angle, colder lighting,
and a more industrial environment while preserving the same character.
```

Mixing these goals causes uncontrolled drift.

---

# 11. Evaluation and Quality Assurance

## 11.1 Universal review criteria

Evaluate every output for:

* Intent fidelity
* Technical validity
* Composition
* Coherence
* Subject accuracy
* Artifact severity
* Style consistency
* Destination suitability
* Accessibility
* Legal or brand constraints
* Revision cost

Do not judge solely by aesthetic appeal.

---

## 11.2 Image QA checklist

Check:

* Correct dimensions and aspect ratio
* Correct subject count
* Accurate anatomy
* Accurate object geometry
* Clean hands and faces
* Correct lighting direction
* Consistent shadows
* Valid reflections
* Background coherence
* No accidental text
* No watermarks
* No duplicated elements
* No edge artifacts
* No unexpected crop
* Sufficient safe space
* Source identity preserved
* Product and brand details preserved

---

## 11.3 Video QA checklist

Check:

* Correct duration
* Correct frame size
* Correct frame rate
* Stable subject identity
* Stable object count
* Stable clothing and props
* Plausible motion
* Camera follows instructions
* No sudden cuts
* No temporal flicker
* No geometry melting
* No reversed motion
* No frame-edge intrusions
* Start and end frames are usable
* Loop closes correctly where required
* Audio remains synchronized
* Compression is acceptable

Review frame by frame when defects are subtle.

---

## 11.4 Audio QA checklist

Check:

* Correct duration
* Correct sample rate
* Correct channel count
* No clipping
* No digital distortion
* No unintended noise
* No missing words
* Correct pronunciation
* Stable voice identity
* Appropriate pacing
* Appropriate emotion
* Consistent loudness
* Clean beginning and ending
* Sufficient headroom
* No unintended music or speech

---

## 11.5 Scoring rubric

Use a five-point scale for each category.

### 5 — Production ready

Meets requirements with no meaningful correction required.

### 4 — Minor revision

Strong output with isolated correctable defects.

### 3 — Usable draft

Direction is valid, but several visible or audible issues remain.

### 2 — Major revision

Some useful elements exist, but the result does not reliably satisfy the brief.

### 1 — Reject

Fundamentally incorrect, incoherent, unsafe, corrupted, or unusable.

Suggested categories:

* Prompt adherence
* Subject accuracy
* Composition
* Technical quality
* Consistency
* Artifact control
* Destination readiness

Do not approve an output solely because the average score is acceptable. A critical failure in identity, branding, text, or technical validity may require rejection.

---

# 12. Common Failure Modes and Remedies

## 12.1 Output looks generic

Likely causes:

* Vague subject description
* Generic style words
* No composition instructions
* No material or environmental detail
* Default lighting

Remedies:

* Add a distinctive environment
* Define framing
* Define a specific lighting setup
* Add material relationships
* Specify a controlled palette
* Remove empty quality adjectives

---

## 12.2 Prompt is ignored

Likely causes:

* Too many competing instructions
* Important details placed late
* Contradictions
* Model capability limits
* Guidance too low
* Reference strength too high or low

Remedies:

* Shorten and prioritize the prompt
* Move hard constraints earlier
* Remove conflicts
* Test another model
* Adjust guidance or edit strength
* Break the task into stages

---

## 12.3 Subject identity changes

Likely causes:

* Excessive transformation strength
* Broad style change
* Poor reference image
* Complex pose
* Long video duration
* Model weakness

Remedies:

* Reduce transformation strength
* Use a clearer reference
* Simplify pose and motion
* Repeat preservation instructions
* Generate shorter clips
* Composite the original face or product where appropriate

---

## 12.4 Image is overprocessed

Symptoms:

* Plastic skin
* Excessive sharpness
* Crushed shadows
* Glowing edges
* Oversaturation
* Artificial microtexture

Remedies:

* Reduce guidance
* Request restrained processing
* Reduce upscaling creativity
* Specify natural skin and material response
* Avoid generic “ultra-detailed” language
* Use a more neutral model or style

---

## 12.5 Video flickers or mutates

Likely causes:

* Excessive motion
* Long duration
* Weak reference
* Multiple subjects
* Detailed repeating patterns
* Conflicting camera instructions

Remedies:

* Shorten the clip
* Simplify motion
* Reduce subject count
* Lock the camera
* Remove complex background action
* Generate separate shots
* Use continuation or interpolation workflows

---

## 12.6 Speech sounds robotic

Likely causes:

* Long sentences
* Formal written language
* Poor punctuation
* Unclear voice direction
* Excessive speed
* Unsupported markup

Remedies:

* Rewrite for speech
* Shorten sentences
* Add natural pauses
* Use conversational phrasing
* Reduce speed
* Generate difficult lines separately

---

## 12.7 Music lacks structure

Likely causes:

* Mood-only prompt
* No duration
* No section plan
* Too many genres
* No energy curve
* No ending instruction

Remedies:

* Define BPM and instrumentation
* Specify sections and timestamps
* Use one primary genre
* Define build, peak, breakdown, and ending
* State whether the track must loop or resolve

---

# 13. Agent Workflow

## 13.1 Intake

Extract:

* Requested modality
* Objective
* Audience
* Destination
* Subject
* Style
* Dimensions or duration
* Source assets
* Hard constraints
* Prohibited elements
* Required deliverables

Ask questions only when missing information materially affects the result.

---

## 13.2 Capability check

Before generation, confirm:

* Required model supports the operation
* Input type is accepted
* Dimensions are valid
* File size is valid
* Duration is valid
* Format is supported
* Required API parameters exist
* Model supports references, seeds, masks, or audio as needed
* Output licensing is acceptable for the use case

Do not invent unsupported parameters.

---

## 13.3 Prompt construction

Produce:

* Primary prompt
* Negative constraints
* Technical parameters
* Preservation instructions
* Output requirements
* Metadata record

Keep user-facing summaries separate from API-ready prompts.

---

## 13.4 Generation

During generation:

* Preserve the exact prompt and parameter set
* Capture request identifiers
* Capture model and version
* Capture provider errors
* Do not silently substitute models
* Do not silently change dimensions or duration
* Record any fallback behavior

---

## 13.5 Validation

Validate:

* File exists
* File opens
* MIME type matches
* Dimensions or duration match
* Output is not an error payload saved as media
* Required subject is present
* Hard constraints are satisfied
* Safety or policy failures are surfaced accurately
* No unexpected watermark or text exists
* Output passes modality-specific QA

---

## 13.6 Revision

When revising:

* State the observed defect
* Identify the likely cause
* Preserve approved elements
* Modify only relevant variables
* Record the new prompt and parameters
* Compare against the previous result

---

## 13.7 Delivery

Provide:

* Final media
* Format
* Dimensions or duration
* Model
* Relevant generation parameters
* Prompt summary
* Known limitations
* Alternate versions when requested

Do not claim perfection when visible or audible defects remain.

---

# 14. Metadata and Reproducibility

Store a generation manifest for each output.

Recommended schema:

```json
{
  "asset_id": "unique-asset-id",
  "created_at": "ISO-8601 timestamp",
  "modality": "image",
  "operation": "text-to-image",
  "provider": "provider-name",
  "model": "model-id",
  "model_version": "version-or-null",
  "prompt": "full prompt",
  "negative_prompt": "full negative prompt or null",
  "seed": null,
  "width": 1920,
  "height": 1080,
  "duration_seconds": null,
  "steps": null,
  "guidance": null,
  "strength": null,
  "input_assets": [],
  "output_format": "png",
  "request_id": "provider-request-id",
  "status": "completed",
  "qa_status": "approved",
  "qa_notes": [],
  "parent_asset_id": null
}
```

Never expose secrets, tokens, private URLs, or authentication headers in manifests.

---

# 15. Efficiency and Cost Control

Use a coarse-to-fine workflow.

### Exploration

* Lower resolution
* Short duration
* Faster model
* Fewer samples
* Reduced steps

### Refinement

* Approved composition
* Moderate quality
* Controlled variations
* Targeted edits

### Finalization

* High resolution
* Final model
* Upscaling
* Restoration
* Full-duration rendering
* Audio mastering

Avoid repeatedly generating expensive final-quality outputs while the creative direction is unresolved.

Track:

* Cost per generation
* Number of retries
* Failure rate
* Approval rate
* Average time to approved asset
* Models used
* Common failure categories

---

# 16. Accessibility

Consider:

* Sufficient text contrast
* Readable type size
* Avoidance of rapid flashing
* Captions for speech
* Transcripts for audio
* Alt text for images
* Audio descriptions where required
* Color-independent communication
* Safe subtitle margins
* Moderate speech rate
* Avoidance of critical information conveyed through sound alone

Generated media should be usable by the intended audience, not merely aesthetically successful.

---

# 17. Final Agent Rules

1. Do not generate before understanding the intended use.
2. Do not treat vague quality adjectives as a substitute for concrete direction.
3. Do not silently change user constraints.
4. Do not invent model parameters or capabilities.
5. Do not embed important text in generated imagery when deterministic typography is available.
6. Do not modify identities, logos, products, or source composition without authorization.
7. Do not use final-quality settings during early exploration.
8. Do not change multiple major variables when diagnosing a failure.
9. Do not approve outputs without opening and reviewing them.
10. Do not evaluate only aesthetics; validate technical and functional requirements.
11. Preserve prompts, parameters, model identifiers, seeds, and source references.
12. Use short, controlled shots for difficult video generation.
13. Use speech-oriented writing for text-to-speech.
14. Generate complex audio in separate stems where possible.
15. Maintain continuity sheets for recurring characters and environments.
16. Treat corrections and creative variations as separate operations.
17. Report limitations and defects honestly.
18. Prefer deterministic post-production for typography, branding, layout, and exact geometry.
19. Optimize for the destination platform.
20. Produce media that is reviewable, reproducible, and operationally useful.

---

# 18. Compact Agent Checklist

Before generation:

* Objective identified
* Audience identified
* Destination identified
* Modality selected
* Model capability verified
* Source invariants documented
* Aspect ratio or duration confirmed
* Hard constraints extracted
* Prohibited elements extracted
* Prompt contradictions resolved

During generation:

* Prompt stored
* Parameters stored
* Model stored
* Source files tracked
* Request identifier captured
* Fallbacks disclosed
* Errors preserved accurately

After generation:

* File validity checked
* Dimensions or duration checked
* Intent fidelity reviewed
* Artifacts reviewed
* Identity and geometry reviewed
* Text and branding reviewed
* Destination suitability reviewed
* Metadata stored
* QA status assigned
* Required revisions documented
