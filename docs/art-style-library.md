# Giggle & Snooze art direction

All new books use `pencil-and-brush`, the approved Vibrant Pencil & Brush house style.
`choose_style` always returns it. Earlier presets remain readable for existing saved books.
The blueprint stores the rendering rules and each book's own vibrant five-colour palette.

Characters have distinct asymmetric silhouettes, angular face geometry, expressive poses,
and memorable hair, clothing and prop details. Environments share the same hand-drawn
shape language. Use searching graphite and coloured-pencil contours, sparse hatching,
matte gouache, broken dry-brush edges and fresh paper showing through. Rich teal, coral,
leaf green, copper, lilac, gold and plum are palette cues, adapted to each story. Preserve
natural skin tones, readable faces and hands, and one clear action. Avoid generic bean
bodies, uniform vector outlines, glossy gradients and a dusty grey colour veil.

Approved references and exact exploration prompts are under
`books/a-pocketful-of-hello/style-exploration/`. Existing images and saved blueprints
are not automatically repainted by a default change.

Video front covers are 16:9 with only the exact book title centered horizontally and
vertically, using imperfect brush lettering and pencil strokes. No subtitle or branding.
Read-back validation expects no subtitle for video. Print covers retain their square
composition and subtitle, with lettering matching the book's illustration medium.

## Narration camera focus

The Ground camera stage sees the picture and indexed narration lines. It chooses up to
eight relevant face/object boxes, omitting unnecessary moves. Face cues support emotion,
reaction and speech; object/hand cues support actions and discoveries. Cues use actual
voice-line timing, ease for up to 1.2 seconds, then hold. Zoom is capped at 2x, the subject
box gets a 15% margin, and the crop stays inside the canvas. Q&A follows the final
narration pose without a jump. Both CPU animation and FFmpeg use the same camera path.

Autopilot grounds narration and Q&A before rendering. Standalone workflows can run
`python -m video_pipeline.ground_camera --book SLUG` after generating the script.
Changed pictures or scripts require fresh focus plans. Missing, failed or stale narration
cues fall back to the original wide/pan camera. Re-render videos to see new focus plans.
The optional low-frame-rate animation treatment has not been enabled.

**The Night the Lighthouse Hiccupped** now specifies Paper Cutout in its style bible and page
prompts. This is an art-direction update only: existing pictures and editions remain unchanged.
Use a full repaint when adopting the new look, so old page references do not pull individual
repaints back toward the previous style.

Animation playback intensity is 1.4 in `config/render.json`. New motion plans target perceptible
movement within 2–4 seconds, favouring 3–6 second cycles and 60–90% of allowed amplitudes where
clearance permits. These are guidance, not minimums. Existing near-static plans may still need
regeneration; existing videos need rendering again. Missing layer cuts still mean camera-only
playback, regardless of intensity.

Subject selection comes before amplitude: the planner receives the page's character sheet and
assesses complete main characters first, then substantial scenery, with small details as accents.
New saves reject plans whose only moving subjects have tight boxes smaller than 5% of the frame,
unless there is a substantial scene-appropriate effect. This box-area check is a visibility
proxy, not a guarantee of mask size or safe movement. Existing plans stay readable and Studio
warns when only small details animate. Plan pictures automatically revisits these weak plans
without requiring force, backing up the old plan only after a replacement passes. Genuine
camera-only plans remain valid when characters
are clipped or no meaningful local movement is safe; never detach limbs to satisfy the check.

First production trial: **The Cloud Who Packed a Picnic**, at most seven story pages, Animated
Storybook. The digital story-only PDF follows that page count; complete-reader and print editions
include their usual publishing extras.
