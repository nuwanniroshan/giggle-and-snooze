# Spec 010: Animated story pages in the video

**Status:** Approved
**Created:** 2026-11-10
**Branch:** `feat/010-animate-pages`
**PRD:** [prd.md](prd.md)
**ADRs:** [010-layer-animation-renderer](../../adr/010-layer-animation-renderer.md) (Accepted)
**Depends on:** specs 003 (render), 004 (studio), 007 (ambience), 009 (motion plan and cut layers); works with 008 (picture ratios) when it lands

---

## Problem Statement

Spec 009 gives each story page a motion plan and cut layers, but the video still shows each page as a still picture moved only by the camera. The producer wants each page to move gently in the video: layers sway, bob and drift, near layers move against far ones (parallax), and rain, snow, fireflies, falling leaves, water, smoke, mist and glow play where the plan puts them. It must be made locally, be repeatable, and leave everything else about the video as it is.

## Goals

- Every story page with a current plan and matching cut layers is shown as an animated picture in its video section.
- The animation follows the plan exactly: its layers, z-order, depth, pivots, motions and effects.
- Camera moves, captions, transitions, ambience and audio keep their current meaning and limits.
- The same inputs always give the same frames; unchanged pages are reused from the cache.
- The producer can preview one animated page quickly, from the CLI or the studio, before rendering the whole episode.
- Pages that can't be animated fall back to today's still picture and say why.

## Non-Goals

- Writing plans or cutting layers (spec 009).
- Animating the intro/outro nook scenes, covers or the map.
- Lip-sync, blinks, or any newly painted frames.
- Physically accurate simulation; effects are stylised and gentle.
- Studio controls for tuning motion (the producer edits the plan file or the config).
- Background music.

## Users and Context

**Primary user:** the producer, rendering on the M1 Pro (16 GB) with `render_episode` (CLI) or the studio.

**Context:**
- Inputs per page: the plan `books/<slug>/animation/page_NN.json` and the cut `books/<slug>/build/layers/page_NN/` (`layers.json`, layer PNGs, `background.png`).
- Today each page section is the whole picture over a blurred backdrop (square pictures) with an FFmpeg camera move, caption cards and page-turn transitions built from each section's real first and last frames.
- Both current books have square 1024×1024 pictures. Spec 008 may add 16:9 pictures, shown full-frame.
- The voice approval and review mark are tied to the episode script's hash (spec 002), which already ignores `ambience` (spec 007).

## User Stories

### Story 1: Pages move in the video
As the producer, I want each planned page to move gently in the rendered video, so that the story feels alive without new art.

- [ ] **AC-1.1** Given a page with a current plan and a matching cut, When the episode is rendered with animation on, Then that page's section shows the background with every non-empty layer composited in plan z-order (higher z in front), each layer moving by its planned motions around its pivot.
- [ ] **AC-1.2** Given a plan layer with depth d, When the page is animated, Then the layer also drifts horizontally by a gentle periodic parallax offset proportional to d (depth 0 does not drift; depth 1 drifts the configured maximum), and the background does not move.
- [ ] **AC-1.3** Given a plan with effects, When the page is animated, Then each effect plays: inside its region box, or inside the named layer's area (following that layer as it moves), or over the whole picture when it has no region. Particle effects (rain, snow, fireflies, falling leaves) and their density and speed follow the effect's intensity; distortion and haze effects (water ripple, smoke, mist, glow) follow the intensity in strength; a colour hint, when present, tints the effect.
- [ ] **AC-1.4** Given an animated page section, When its first frame is compared with the same section rendered as a still (animation off), Then they are identical within encoder tolerance (every motion, parallax drift and effect starts from rest and eases in over a configured time).
- [ ] **AC-1.5** Given any animated frame, When it is inspected, Then no part of a layer or effect is drawn outside the picture area (the backdrop around a square picture is unchanged), and no transparent or blank area shows inside the picture.
- [ ] **AC-1.6** Given an animated page section, When its length is measured, Then it has exactly the same frame count as the same section rendered as a still.

### Story 2: The rest of the video stays the same
As the producer, I want camera moves, captions, transitions and sound to behave as before, so that animation adds motion without changing the episode.

- [ ] **AC-2.1** Given an animated page section, When it is rendered, Then the section's camera move (push, pan, Q&A zoom targets), backdrop and caption cards are the same as for the still version, applied to the animated picture frame by frame.
- [ ] **AC-2.2** Given two adjacent sections where either is animated, When the transition between them is rendered, Then it is built from the real last frame of the first section and the real first frame of the second, so there is no jump into or out of the transition.
- [ ] **AC-2.3** Given any episode, When it is rendered with animation on or off, Then the audio (voices, page turns, ambience) is identical.
- [ ] **AC-2.4** Given animation is turned off in the config, When an episode is rendered, Then every section, transition and the final video are byte-identical to today's output, with the same cache keys.

### Story 3: Control and override
As the producer, I want to switch animation on or off and make a page calmer, stronger or still, so that I can fix a page without editing its plan.

- [ ] **AC-3.1** Given the render config's animation block, When the producer sets it off, Then every page renders still; when on (the default), pages with a current plan and cut are animated.
- [ ] **AC-3.2** Given the global intensity multiplier m, When pages are animated, Then every motion amplitude, parallax drift and effect intensity is scaled by m (effect intensity capped at 1).
- [ ] **AC-3.3** Given a page in `episode_script.json` with `"animation": k` (a number in the allowed range), When it is animated, Then its motions, parallax and effects are scaled by k on top of the global multiplier.
- [ ] **AC-3.4** Given a page with `"animation": false`, When the episode is rendered, Then that page renders still, with exactly today's section cache key.
- [ ] **AC-3.5** Given an `animation` value that is neither `false` nor a number in the allowed range, or an `animation` key on the intro or outro, When the script is validated, Then validation fails naming the page (or block) and the allowed values.
- [ ] **AC-3.6** Given an approved episode, When the producer adds, changes or removes a page's `animation` override, Then the voice approval and the review mark stay valid (the override is ignored by the script hash, like `ambience`), and a script with neither key hashes exactly as before.

### Story 4: Fall back to still, and say why
As the producer, I want a page that can't be animated to render still with a clear reason, so that a missing or outdated plan never breaks a render.

- [ ] **AC-4.1** Given a page with no plan, a stale plan, an invalid plan, no cut, a cut whose hash does not match the plan's current cut fields, or missing layer files, When the episode is rendered, Then that page renders exactly as today (same cache key), the render continues, and the render log names the page and the reason with the command that fixes it (for example "re-run cut_layers").
- [ ] **AC-4.2** Given a book with no plans at all, When it is rendered, Then the whole render is byte-identical to today's, with the same cache keys.
- [ ] **AC-4.3** Given the studio, When the producer opens a book's animation view, Then each page shows whether it will be animated or rendered still, and if still, why.

### Story 5: Caching and repeatability
As the producer, I want unchanged pages reused and every render repeatable, so that re-renders are quick and predictable.

- [ ] **AC-5.1** Given an episode rendered once, When it is rendered again with no change, Then no section is re-rendered and the output is byte-identical.
- [ ] **AC-5.2** Given one page's plan motion or effect is edited (or its cut is redone, or its override changes), When the episode is re-rendered, Then only that page's section and its two neighbouring transitions are re-rendered.
- [ ] **AC-5.3** Given the animation settings in the config change, When the episode is re-rendered, Then the animated sections are re-rendered and the still sections are reused.
- [ ] **AC-5.4** Given the same inputs, When an animated section is rendered twice (with its cache cleared in between), Then the frames are identical. Every random element is seeded from the book slug, the page number and the effect's position and type.
- [ ] **AC-5.5** Given an animated page's plan, cut, override or the animation settings changed since the last render, When the studio or CLI checks render status, Then the render is reported as out of date.

### Story 6: Preview one page
As the producer, I want to preview one animated page quickly, so that I can judge the motion without rendering the whole episode.

- [ ] **AC-6.1** Given a page whose voice track exists (approval is not required), When the producer runs the preview command for that page, Then one video file of that full page section (animation, camera, captions, and that page's voice) is written under the book's build folder, and the command prints its path and whether the page was animated or still (with the reason).
- [ ] **AC-6.2** Given the studio's animation view of a page, When the producer starts "Preview", Then it runs as a studio job (one at a time, with a log, stoppable), and when it ends the page shows a player for the preview.
- [ ] **AC-6.3** Given a page whose voice track does not exist, When a preview is requested, Then it fails with a message naming the voices command.

## Functional Requirements

- **FR-1 Composition.** An animated page frame is the cut background, with every non-empty layer drawn in ascending z at its offset, transformed by its motions and parallax, then the effects. Equal z keeps plan order. Layers and effects are clipped to the picture area.
- **FR-2 Motions** (vocabulary from spec 009; amplitude limits already enforced by the plan). Each is periodic with the planned period and phase:
  - `sway`: rotation around the pivot between −amplitude and +amplitude degrees.
  - `rotate`: rotation around the pivot between 0 and +amplitude degrees (one-sided rocking).
  - `bob`: vertical offset between −amplitude and +amplitude × the picture's shorter side.
  - `shift`: offset along the normalised direction between −amplitude and +amplitude × the shorter side.
  - `breathe`: scale around the pivot between 1 − amplitude and 1 + amplitude.
  - Several motions on one layer combine.
- **FR-3 Ease-in.** Every motion, parallax drift and effect is multiplied by an ease-in from 0 to 1 over the configured ease-in time at the start of the section, so the first frame equals the still picture.
- **FR-4 Parallax.** Each layer drifts horizontally by depth × the configured maximum parallax (fraction of the shorter side) × a periodic curve with the configured period. The background does not move.
- **FR-5 Effects.** One shared vocabulary with spec 009: `rain`, `snow`, `fireflies`, `falling_leaves`, `water_ripple`, `smoke`, `mist`, `glow`.
  - Region: a box (fixed), a layer name (follows that layer's transform and area), or none (whole picture).
  - Intensity 0–1 scales density or strength; per-effect defaults (counts, speeds, sizes, strengths) live in the config.
  - Colour hint tints the effect; otherwise a per-effect default colour.
  - Effects are drawn above the layers, except `water_ripple`, which distorts the area it covers (background and layers inside it).
- **FR-6 Seeds.** Every random value is derived from the book slug, the page number and the effect's index and type, so frames are reproducible.
- **FR-7 Intensity.** Final scale for a page = global multiplier × page override (if any). It multiplies motion amplitudes, parallax and effect intensity (intensity capped at 1). A scale of 0 is not allowed for the override (use `false`).
- **FR-8 Settings.** `config/render.json` gains an `animation` block: `enabled` (default true), `intensity` (default 1.0), `ease_in_s`, `parallax` (`max_shift`, `period_s`), the override's allowed range (default 0.25–2.0), and per-effect defaults.
- **FR-9 Page override.** An optional per-page `animation` key in `episode_script.json`: `false` or a number in the allowed range. Allowed only on story pages. Validated by the script contract.
- **FR-10 Script hash.** The script hash ignores both `ambience` and `animation` keys (a set of excluded keys); a script with none of them hashes exactly as before (raw-bytes shortcut).
- **FR-11 Eligibility.** A page is animated only when all hold: animation enabled, no `false` override, the plan exists, validates and is not stale, the cut manifest exists, its cut hash equals the hash computed from the current plan, settings and models (the spec 009 rule), and every listed layer file and the background exist. Otherwise it renders still and the reason is logged (AC-4.1). Geometry (z, depth, pivot, motions, effects) comes only from the plan; files, offsets and sizes from the manifest.
- **FR-12 Integration.** The animated picture replaces the still picture inside the section: the existing backdrop (square pictures) or full frame (16:9), the camera, captions and encoding are unchanged. Transitions keep being built from each section's real first and last frames.
- **FR-13 Cache keys.** A still section's key is computed exactly as today (render version unchanged). An animated section's key adds: the plan's hash, the manifest's hash, the animation settings, the page's effective scale and an animation renderer version. The episode's final key includes the section keys as today.
- **FR-14 Render status.** The render check (CLI and studio) treats a change to any animated section's key as out of date.
- **FR-15 Preview.** `render_episode --book <slug> --preview-page N` renders that page's full section with its voice track into `build/video/preview/page_NN.mp4`, using the same section code path and cache as the full render. It needs the page's voice track but not the audio approval.
- **FR-16 Studio.** The book's animation view shows per page "animated" or "still: <reason>", a "Preview" job per page, and a player for an existing preview. Jobs use the existing runner (one at a time, logged, stoppable).

## Non-Functional Requirements

- **NFR-1 Speed.** A full pilot episode with every page animated renders in at most about 2× today's time (8.2 min → ≤ 16.5 min, up to 10% overrun accepted), measured on the M1 Pro. An animated page section renders at ≤ 0.85 s of wall time per second of video (ADR-010 gate).
- **NFR-2 Memory.** Peak memory of the render process stays ≤ 3 GB above today's render.
- **NFR-3 Repeatability.** Same inputs give identical frames and an identical final video (AC-5.4).
- **NFR-4 Local and offline.** Rendering uses no network and no models; it reads only files.
- **NFR-5 No impact when off.** With animation off, or for a book without plans, the output and cache keys are byte-identical to today's (AC-2.4, AC-4.2). Existing approvals and review marks stay valid (AC-3.6).
- **NFR-6 Look (acceptance).** On the pilot book, the producer judges every animated page's motion gentle, with no visible seams, holes or tearing at normal viewing, and no jump into or out of any section or transition.

## Error Scenarios

- **Plan missing, stale or invalid; cut missing, mismatched or incomplete** → that page renders still with the reason logged and shown in the studio (AC-4.1, AC-4.3); the render continues and exits 0.
- **Invalid `animation` override in the script** → script validation fails naming the page and allowed values; render refuses to start, like any other script error.
- **Animation config block invalid** (unknown effect name, value out of range) → the render fails before any work, naming the config field.
- **A layer image can't be read during rendering** → that page falls back to still with the reason; the render continues.
- **Preview of a page without a voice track** → fails naming the voices command (AC-6.3).
- **Render interrupted** → as today: unfinished clips are never reused (temporary file, then rename).

## Open Questions

None. Effect styling details (counts, speeds, colours) are config defaults tuned during the ADR-010 spike and the producer's review.

## Out of Scope

- Plan generation and layer cutting (spec 009).
- Intro/outro, covers and map animation.
- Blinks, lip-sync, extra painted frames.
- GPU shaders, unless the ADR-010 spike shows the CPU path is too slow.
- Background music.
