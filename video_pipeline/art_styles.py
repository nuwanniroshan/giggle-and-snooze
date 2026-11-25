"""Giggle & Snooze house style and compatible legacy rendering presets."""

from copy import deepcopy

DEFAULT_STYLE = "pencil-and-brush"
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
    "pencil-and-brush": {
        "medium_label": "Vibrant Pencil & Brush",
        "style_prompt_prefix": "Original Giggle & Snooze pencil-and-brush illustration: distinctive asymmetric silhouettes, angular faces and varied proportions, warm expressive characters. Define each character by unique face geometry, hair silhouette, wardrobe and a signature detail; preserve these across pages. Confident imperfect graphite and coloured pencil with visible searching strokes, matte gouache and broken dry-brush edges on fresh light paper. Vibrant rich teal, coral, leaf green, copper, lilac, gold and plum adapted to the story; preserve natural skin tones. Avoid interchangeable round bean-shaped characters.",
        "line": "Broken pencil contours, visible overshoots, sparse construction strokes and light hatching; irregular brush edges, never uniform vector outlines.",
        "lighting": "Rich local colour, restrained matte painted shadows and fresh paper highlights; no dusty grey veil, neon or glossy gradients.",
        "composition": "One clear action, readable faces and hands, distinctive silhouettes and breathing room. Asymmetric scenery: crooked roofs, mismatched windows and bent trunks where appropriate to the setting.",
    },
    "paper-cut": {
        "medium_label": "Paper Cutout",
        "style_prompt_prefix": BRAND
        + " Layered cut-paper cartoons with broad scalloped shapes, crisp cut edges, overlapping colour planes and playful off-centre silhouettes.",
        "line": "No drawn outlines; define forms with clean cut-paper edges.",
        "lighting": "Small soft shadows between paper layers; matte colours, no glossy highlights.",
        "composition": "Stage-like layers, bold readable silhouettes and simple curved scenery framing one expressive action.",
    },
    "ink-and-colour": {
        "medium_label": "Ink & Colour",
        "style_prompt_prefix": BRAND
        + " Modern comic-strip cartoons with springy ink contours, expressive eyebrows and flat cheerful colours on clean backgrounds.",
        "line": "Confident dark rounded outlines with a few lively interior strokes; no crosshatching.",
        "lighting": "Flat colour with one small graphic shadow; no gradients.",
        "composition": "Playful asymmetry, expressive full-body gestures and broad uncluttered scenery; no panels or speech bubbles.",
    },
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
    "watercolour-pencil": {
        "medium_label": "Watercolour & Pencil",
        "style_prompt_prefix": BRAND
        + " Transparent watercolour washes with delicate coloured-pencil contours, gentle pigment pooling and luminous white paper; tender hand-painted cartoon expressions.",
        "line": "Fine lightly irregular pencil contours; no heavy ink outlines.",
        "lighting": "Luminous translucent washes with soft natural colour transitions.",
        "composition": "Airy scenes with broad washes, readable gestures and generous white-paper space.",
    },
    "clean-line-cartoon": {
        "medium_label": "Clean-Line Cartoon",
        "style_prompt_prefix": BRAND
        + " Thin tidy uniform ink contours, simple flat gentle colours and expressive simplified faces; light contemporary cartoon drawing.",
        "line": "Thin precise uniform outlines, minimal interior marks and no heavy borders.",
        "lighting": "Flat colour with minimal simple shading.",
        "composition": "Airy uncluttered scenery, clear silhouettes and expressive full-body gestures.",
    },
    "pastel-chalk": {
        "medium_label": "Pastel & Chalk",
        "style_prompt_prefix": BRAND
        + " Velvety dry-pastel cartoons with powdery chalk strokes, softly broken edges and gentle colours on lightly toothed paper.",
        "line": "Soft broken chalk contours and visible delicate dry-pastel marks.",
        "lighting": "Gentle diffuse colour with soft chalk shading; no glossy highlights.",
        "composition": "Calm spacious framing, broad soft scenery and clearly readable faces and hands.",
    },
    "gouache-storybook": {
        "medium_label": "Gouache Storybook",
        "style_prompt_prefix": BRAND
        + " Opaque matte gouache cartoons with broad visible brushwork, layered painted shapes and rich balanced colours; softly irregular painted edges.",
        "line": "Define forms with painted edges and a few confident brush accents.",
        "lighting": "Matte opaque colour with restrained painted shadow shapes.",
        "composition": "Broad painted scenery frames one expressive action; keep brush detail subordinate to faces.",
    },
    "retro-cartoon": {
        "medium_label": "Retro Cartoon",
        "style_prompt_prefix": BRAND
        + " Original retro rubber-hose cartoons with rounded heads, flexible limbs and lively walking gestures, rendered in a fresh clean palette rather than aged paper.",
        "line": "Smooth confident curved outlines and simple expressive facial features.",
        "lighting": "Fresh flat colours with one simple grounding shadow; no sepia.",
        "composition": "Playful readable poses, uncluttered scenery and generous space; preserve each character's skin tone and outfit.",
    },
    "felt-fabric": {
        "medium_label": "Felt & Fabric",
        "style_prompt_prefix": BRAND
        + " Handcrafted felt-applique cartoons with layered soft fabric characters and scenery, subtle blanket-stitch edges and fine fleece texture; flat sewn storybook artwork.",
        "line": "Soft cut-felt edges with subtle stitching; no drawn ink outlines.",
        "lighting": "Small soft shadows between matte fabric layers; no plastic or glossy highlights.",
        "composition": "Simple layered fabric scenery, clear character silhouettes and one welcoming emotional beat.",
    },
}

# Legacy presets remain readable for saved books; new books share one house style.
RANDOM_STYLES = (DEFAULT_STYLE,)
TEXTURED_STYLES = {
    "pencil-and-brush",
    "watercolour-pencil",
    "pastel-chalk",
    "gouache-storybook",
    "felt-fabric",
}


def choose_style(used: list[str], *, rng=None) -> str:
    """All new books use the approved house style, independent of previous usage."""
    return DEFAULT_STYLE


def style_art(style_id: str = DEFAULT_STYLE) -> dict:
    """Fixed rendering rules; each book supplies its own five-colour palette."""
    avoid = list(AVOID)
    if style_id in TEXTURED_STYLES:
        avoid.remove("heavy brush texture, grain and crosshatching")
        avoid.append(
            "texture that obscures faces, gestures or story-relevant props"
            + ("" if style_id == DEFAULT_STYLE else "; crosshatching")
        )
    if style_id == "retro-cartoon":
        avoid.remove("aged paper, sepia washes and vintage styling")
        avoid.append("aged paper, sepia washes and distressed print effects")
    return {"medium_key": style_id, **deepcopy(STYLES[style_id]), "avoid": avoid}


def apply_style(blueprint: dict, style_id: str = DEFAULT_STYLE) -> dict:
    result = deepcopy(blueprint)
    result["art"].update(style_art(style_id))
    return result
