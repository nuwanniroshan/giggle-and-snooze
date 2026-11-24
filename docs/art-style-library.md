# Giggle & Snooze art library

The initial library lives in `video_pipeline/art_styles.py`. New blueprint generation defaults
to **Animated Storybook**; style selection in the studio is deferred.

| Preset | Look |
| --- | --- |
| `animated-storybook` | Expressive 2D characters, tapered contours, smooth fills, restrained soft shading. |
| `bold-playful` | Chunky geometric silhouettes, crisp flat colour, exaggerated readable poses. |
| `soft-rounded` | Rounded bean shapes, gentle pastel colours, small expressive features, matte fills. |

Shared identity: original characters, rounded bean and crescent shape language, warm playful
expressions, small visual jokes, and a calm cosy atmosphere. One clear action per scene, broad
background shapes and only story-relevant props. No aged-paper styling, heavy textures, intricate
surface detail or franchise imitation. Keep visual jokes subtle and subordinate to the story.

The writer receives the fixed preset; the saved blueprint's rendering fields are normalized to
it before validation. Each book retains its own palette, characters, mood and story direction.
Illustration and cover generation consume the saved blueprint, so a later library edit does not
silently change existing books. Existing books are unchanged until explicitly redesigned.

`apply_style(blueprint, style_id)` supports the other two presets for future selection. Keep one
style throughout a book, including its covers. Before adding a picker, compare all three using
the same sample scene and validate the resulting artwork for consistency.

First production trial: **The Cloud Who Packed a Picnic**, at most seven story pages, Animated
Storybook. The digital story-only PDF follows that page count; complete-reader and print editions
include their usual publishing extras.
