# Spec 009: Motion plan and layer cutting for story pages

**Status:** Approved
**Created:** 2026-11-10
**Branch:** `feat/009-animation-plan-layers`
**PRD:** [prd.md](prd.md)
**ADRs:** [009-layer-segmentation-runtime](../../adr/009-layer-segmentation-runtime.md) (Accepted), [009-depth-parallax-engine](../../adr/009-depth-parallax-engine.md) (Rejected)
**Depends on:** specs 004 (studio), 005 (book folders), 006 (idea to book, illustrations), 008 (picture ratios)
**Feeds:** spec 010 (animate pages)

---

## Problem Statement

Every story page in the video is a still picture. The producer wants the pictures to move (layers that sway, bob and drift, plus rain, water, smoke and similar effects), made locally and repeatably. AI is used once per picture to **plan** what moves; local tools then **cut** the picture into layers. This spec covers the plan and the cut. Spec 010 renders the motion.

## Goals

- Every story page picture can have a hand-editable, validated motion plan describing its layers, motions and effects.
- Plans are written automatically when a page picture is painted, and on demand for pictures that already exist.
- A local cut step turns each plan into transparent layer images and a filled background, cached and offline.
- The producer can check plans, masks and the filled background per page in the studio before anything moves.

## Non-Goals

- Rendering any motion or effect (spec 010).
- A visual plan editor; plans are edited as files.
- Planning or cutting covers, maps or the intro/outro nook scenes.
- Painting separate layer images with the image generator.
- Local language or vision models; depth estimation.

## Users and Context

**Primary user:** the producer, on the M1 Pro (16 GB), using the CLI and the studio.

**Context:**
- Pictures are painted by `illustrate` (spec 006) or already exist (both current books are square, 1024×1024). New books may be 16:9 (spec 008).
- Plans live in the book folder next to `episode/`, as committed source files. Cut layers are generated output under `build/`.

## User Stories

### Story 1: A plan for every page picture
As the producer, I want each story page picture to have a motion plan, so that the layers, motions and effects for that page are written down once and reused.

- [ ] **AC-1.1** Given a book whose page pictures exist and have no plans, When the producer runs the plan command for the book, Then a plan file `books/<slug>/animation/page_NN.json` is written for every story page with a picture, and none for covers or maps.
- [ ] **AC-1.2** Given a page that already has a plan, When the plan command runs without force, Then that plan file is left byte-for-byte unchanged and the command reports it as kept.
- [ ] **AC-1.3** Given a page that already has a plan, When the plan command runs with force, Then a new plan is written and the previous file is kept as `page_NN.json.bak`.
- [ ] **AC-1.4** Given a plan was written, When the producer opens it, Then it records the picture's sha256, width and height, and contains 1 to the configured maximum of layers (default 8) and 0 to the configured maximum of effects (default 3).
- [ ] **AC-1.5** Given a plan command for one page fails (provider error or invalid result after the allowed retries), When the run finishes, Then the other pages' plans are still written, and the failed page is named in the output with the reason, with a non-zero exit code.
- [ ] **AC-1.6** Given the plan command is run for a single page (`--page N`), When it finishes, Then only that page's plan is written or kept.

### Story 2: Plans written when pictures are painted
As the producer, I want a new book's pictures to come with plans, so that I don't need an extra step.

- [ ] **AC-2.1** Given `illustrate` paints a page picture successfully, When painting of that page finishes, Then a plan for that page is written before the next picture is painted.
- [ ] **AC-2.2** Given `illustrate` paints a cover, When it finishes, Then no plan is written for the cover.
- [ ] **AC-2.3** Given a page picture is painted but planning it fails, When `illustrate` finishes, Then the picture is kept, the failure is reported with the page and reason, and running the plan command later writes the missing plan.
- [ ] **AC-2.4** Given a page picture is repainted with `illustrate --force`, When the repaint finishes, Then the old plan is kept as `page_NN.json.bak` and a new plan for the new picture replaces it.
- [ ] **AC-2.5** Given a page picture exists and `illustrate` skips it (no force), When `illustrate` runs, Then that page's plan, if any, is not changed.

### Story 3: Plans are checked
As the producer, I want every plan checked against fixed rules, so that a bad plan fails early with a clear message instead of producing broken layers or motion.

- [ ] **AC-3.1** Given a plan that uses a motion or effect name outside the fixed vocabulary, When it is validated (on save or on load), Then validation fails naming the file, the field path and the unknown name.
- [ ] **AC-3.2** Given a plan with a box, point or pivot outside the picture's width and height, When it is validated, Then validation fails naming the file, the field path, the value and the picture size.
- [ ] **AC-3.3** Given a plan with a motion amplitude or speed beyond the configured limits, When it is saved by the planning tool, Then the value is rejected with the limit named, and the provider is asked to correct it. When the plan is a hand-edited file being loaded, the value fails validation with the limit named. Values are never silently changed.
- [ ] **AC-3.4** Given a plan with two layers of the same name, or an effect region naming a layer that does not exist, When it is validated, Then validation fails naming the duplicate or missing layer.
- [ ] **AC-3.5** Given a plan whose recorded picture sha256 differs from the current picture file, When the plan is loaded by any command or the studio, Then the plan is reported as **stale**.
- [ ] **AC-3.6** Given a valid plan, When it is loaded, saved and loaded again, Then the second load equals the first (round-trip), and the file is written atomically with 2-space indent and UTF-8.

### Story 4: Cut layers locally
As the producer, I want the picture cut into the planned layers with the background filled, so that spec 010 can move layers without showing holes.

- [ ] **AC-4.1** Given a valid, non-stale plan and the downloaded models, When the producer runs `cut_layers --book <slug>`, Then for each planned page `books/<slug>/build/layers/page_NN/` contains one transparent PNG per planned layer, a `background.png` of the picture's full size, and a `layers.json` manifest listing each layer's file, offset, size and the cut hash.
- [ ] **AC-4.2** Given a cut layer PNG, When it is composited at its offset over `background.png`, Then the result matches the original picture inside every layer's mask (within a small tolerance at feathered edges, for example mean absolute difference ≤ 2/255 inside the mask).
- [ ] **AC-4.3** Given `background.png`, When it is compared with the original picture, Then pixels outside every layer's mask (dilated by the feather width) are unchanged, and pixels inside are filled (not transparent, not black).
- [ ] **AC-4.4** Given a page was cut and neither the picture, the cut-relevant plan fields (layer names, boxes, points) nor the model ids changed, When `cut_layers` runs again, Then that page is skipped without loading any model, and the output files are unchanged.
- [ ] **AC-4.5** Given only a motion, effect, depth, z-order or pivot was edited in a plan, When `cut_layers` runs, Then the page is not re-cut.
- [ ] **AC-4.6** Given a stale plan, When `cut_layers` runs, Then that page is refused with a message naming the page and saying to re-run the plan command. Other pages are still processed, and the exit code is non-zero.
- [ ] **AC-4.7** Given a page has no plan, When `cut_layers` runs, Then the page is listed as "no plan" and skipped.
- [ ] **AC-4.8** Given the models have not been downloaded, When `cut_layers` runs, Then it fails before any work, with a message naming the download command, and makes no network request.
- [ ] **AC-4.9** Given the producer runs the download command, When it finishes, Then the pinned segmentation and fill models are stored locally at pinned revisions, and a later `cut_layers` runs with networking disabled.
- [ ] **AC-4.10** Given a layer's mask comes out empty (the box held nothing the segmenter could find), When the page is cut, Then the page still completes, that layer is reported by name as empty, and the manifest marks it empty.

### Story 5: Check plans and layers in the studio
As the producer, I want to see each page's plan and cut result in the studio, so that I can spot bad boxes or masks before animating.

- [ ] **AC-5.1** Given a book with plans, When the producer opens the book in the studio, Then each story page shows its plan state: none, ok, stale or invalid (with the validation message).
- [ ] **AC-5.2** Given a page with a valid plan, When the producer opens that page's animation view, Then they see the picture with each layer's box and points drawn and labelled, the list of layers with z, depth, pivot and motions, and the list of effects with their regions.
- [ ] **AC-5.3** Given a page that has been cut, When the producer opens its animation view, Then they also see each layer's mask overlaid on the picture in a distinct colour and the filled background on its own, and the page shows "cut" or "needs cut" depending on whether the cut matches the current plan.
- [ ] **AC-5.4** Given the studio is idle, When the producer starts "Plan pictures" or "Cut layers" for a book, Then the job runs as a studio job (one at a time, with a log, and stoppable like other stages), and the page states refresh when it ends.
- [ ] **AC-5.5** Given a plan file is changed on disk, When the producer reloads the page, Then the studio shows the updated plan and state with no restart.

## Functional Requirements

- **FR-1 Plan vocabulary.** One definition, shared with spec 010.
  - Motions: `sway`, `bob`, `rotate`, `breathe`, `shift`. Each has an amplitude, a period in seconds and a phase (0–1), and `shift` also has a direction.
  - Effects: `rain`, `snow`, `fireflies`, `falling_leaves`, `water_ripple`, `smoke`, `mist`, `glow`. Each has an optional region (a box, or a layer name), an intensity (0–1) and an optional colour hint.
- **FR-2 Plan contents.**
  - Picture identity: sha256, width, height.
  - Plan meta: the provider and the time it was written.
  - Layers: name (snake_case, unique), description, box `[x0, y0, x1, y1]` in picture pixels, optional include and exclude points, z-order (integer), depth (0 = far … 1 = near), pivot `[x, y]`, and a list of motions.
  - Effects: a list.
  - All keys are snake_case.
- **FR-3 Limits.** The maximum layers, maximum effects, maximum rotation (degrees), maximum shift and bob (fraction of the picture's shorter side), minimum and maximum period, and maximum breathe scale live in one config file. Defaults are gentle: 8 layers, 3 effects, 4°, 2%, 2–12 s, 3%.
- **FR-4 Planning input.** The provider gets:
  - the painted picture itself;
  - the page text and the page's illustration prompt;
  - the book's style bible character notes;
  - the vocabulary and limits;
  - the picture's pixel size.

  It returns the plan only through a validating save tool, which rejects invalid plans with the reasons so the provider can correct them, up to a configured number of attempts.
- **FR-5 Provider.** Planning uses the project's existing default provider (Codex through the ChatGPT app), with no fallback between providers. Agent run logs go to `build/agent/`, as for other stages.
- **FR-6 Plan command.** CLI `plan --book <slug> [--page N] [--force]`. Writes are atomic. It never edits pictures.
- **FR-7 Cut command.** CLI `cut_layers --book <slug> [--page N] [--force]`. `--force` re-cuts even when cached. It processes one page at a time, holding at most one page's full-resolution images at once.
- **FR-8 Cut outputs.**
  - Layer PNGs are cropped to the layer's mask bounds, with their offset recorded.
  - Mask edges are feathered by a configured width (default 2 px).
  - `background.png` is the full picture size with every layer area filled.
  - `layers.json` records the files, offsets and sizes, the empty flag, the cut hash, the model ids and the picture sha256.
- **FR-9 Cut hash.** sha256 over the picture sha256, the cut-relevant plan fields (layer names, boxes, include and exclude points, in a stable order), the feather width and the model ids.
- **FR-10 Source of truth.** z-order, depth, pivot, motions and effects are read from the plan only, never from the manifest.
- **FR-11 Download command.** It fetches the pinned model files once into a local model folder outside `build/` and prints what was stored. Cutting never downloads.
- **FR-12 Studio.** Adds a per-book plan and cut status, a per-page animation view (AC-5.2/5.3), and two jobs ("Plan pictures", "Cut layers") that use the existing job runner. The studio stays bound to 127.0.0.1, and the images it serves stay on the media whitelist.
- **FR-13 Illustrate integration.** After each successfully painted page picture, `illustrate` runs planning for that page (AC-2.1–2.5). A planning failure never deletes or repaints a picture.

## Non-Functional Requirements

- **NFR-1 Local and offline.** Cutting runs with no network access after the download. Planning is the only step that uses a network provider.
- **NFR-2 Speed.** Cutting takes ≤ 10 s per page for all its layers plus the fill, measured on the M1 Pro after the models load. A no-change re-run of the whole pilot book takes ≤ 5 s.
- **NFR-3 Memory.** The cut step's peak memory is ≤ 4 GB. It runs as its own process, never at the same time as voice synthesis or rendering.
- **NFR-4 Repeatability.** Cutting the same picture with the same plan and models gives identical masks (bit-exact layer and background files on the same machine).
- **NFR-5 No impact on existing outputs.** Books without plans render, synthesise and build exactly as before. No existing file format changes.
- **NFR-6 Licences.** Every model used allows commercial use, and its licence and source are recorded next to its pinned revision.
- **NFR-7 Mask quality (acceptance).** On the pilot book, at least 80% of planned layers are judged cleanly cut by the producer in the studio overlay. Every other layer can be fixed by editing its box or points and re-cutting.

## Error Scenarios

- **Provider unavailable or not logged in** → the plan command fails before any page, with the same guidance as other Codex stages. `illustrate` reports planning as failed for each page and keeps the pictures.
- **Provider keeps returning invalid plans** → after the configured attempts, that page fails with the last validation message; other pages continue.
- **A hand-edited plan is invalid JSON or breaks a rule** → every command that reads it fails for that page, naming the file, field and rule. The studio shows the page as invalid with the message.
- **Stale plan** → plan state "stale" in the studio. `cut_layers` refuses the page (AC-4.6). Spec 010 renders the page still.
- **Picture missing for a planned page** → the plan state is "no picture", and cut skips it with that reason.
- **Models missing** → AC-4.8.
- **Segmentation gives an empty mask** → AC-4.10.
- **Disk write interrupted** → no partial plan or manifest is left (atomic writes). A half-written layer folder is detected by a missing or mismatched manifest and is re-cut on the next run.

## Open Questions

None. Defaults are set from the brainstorm decisions; the limit values (FR-3) are config and can be tuned after the spike.

## Out of Scope

- Animating layers and effects and putting them in the video (spec 010).
- Blinks, extra painted frames or closed-eye variants.
- Covers, maps and intro/outro nook scenes.
- Cloud segmentation or layer-decomposition services, and depth estimation.
- Background music.
