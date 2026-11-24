"""Giggle & Snooze art library. Selection UI is intentionally deferred."""

from copy import deepcopy

DEFAULT_STYLE = "animated-storybook"
BRAND = (
    "Original Giggle & Snooze character design: expressive asymmetric poses, rounded bean and "
    "crescent shapes, warm playful faces, a small visual joke and a calm cosy atmosphere. "
    "Clean contemporary shapes, generous breathing room, one clear story action. "
    "Simplify hair, clothing and scenery into broad shapes; include only story-relevant props."
)
AVOID = [
    "aged paper, sepia washes and vintage styling",
    "heavy brush texture, grain and crosshatching",
    "ornate decoration and cluttered scenery",
    "individual fur strands and intricate clothing detail",
    "photorealism, glossy 3D and cinematic lighting",
    "existing franchise characters or imitation of an artist",
]
STYLES = {
    "animated-storybook": {
        "medium_label": "Animated Storybook",
        "style_prompt_prefix": BRAND
        + " Contemporary 2D animation shapes, distinctive silhouettes, expressive eyes, smooth colour fills and restrained soft shading.",
        "line": "Clean tapered contours; use coloured edges instead of heavy black outlines.",
        "lighting": "Soft warm light with one simple shadow tone; no dramatic lighting.",
        "composition": "Large expressive characters, one focal action, two or three broad background shapes and clear space around faces and hands.",
    },
    "bold-playful": {
        "medium_label": "Bold & Playful",
        "style_prompt_prefix": BRAND
        + " Bold flat 2D cartoons, chunky geometric silhouettes, lively exaggerated poses and crisp solid colour blocks.",
        "line": "Crisp silhouette edges, almost no internal lines and no outlines.",
        "lighting": "Flat colour with no gradients; a single simple grounding shadow if needed.",
        "composition": "One playful focal gesture, oversized readable shapes, strong colour contrast and ample uncluttered negative space.",
    },
    "soft-rounded": {
        "medium_label": "Soft & Rounded",
        "style_prompt_prefix": BRAND
        + " Soft rounded 2D cartoons, plump bean-shaped bodies, small expressive facial features, gentle pastel colours and smooth matte fills.",
        "line": "Soft clean coloured contours, minimal internal detail and no heavy outlines.",
        "lighting": "Gentle even light with subtle soft shading and no glossy highlights.",
        "composition": "Close friendly characters, calm spacious framing, simple curved scenery and one clear emotional beat.",
    },
}


def style_art(style_id: str = DEFAULT_STYLE) -> dict:
    """Fixed rendering rules; each book supplies its own five-colour palette."""
    return {"medium_key": style_id, **deepcopy(STYLES[style_id]), "avoid": list(AVOID)}


def apply_style(blueprint: dict, style_id: str = DEFAULT_STYLE) -> dict:
    result = deepcopy(blueprint)
    result["art"].update(style_art(style_id))
    return result
