"""A valid blueprint dict and a context over the real sound library (spec 011)."""

from __future__ import annotations

import copy

from video_pipeline.blueprint import Context

VALID = {
    "version": 1,
    "mood": {
        "key": "sleepy-drift",
        "label": "Sleepy drift",
        "summary": "Slow, hushed, dreamy.",
        "roles": {
            "storyteller": {
                "pace": "slow",
                "energy": "hushed",
                "quirk": "Whispers the big moments.",
            },
            "kid_older": {"pace": "steady", "energy": "calm", "quirk": "Yawns between guesses."},
            "kid_younger": {"pace": "slow", "energy": "calm", "quirk": "Hugs a pillow."},
        },
    },
    "art": {
        "medium_key": "chalk-pastel",
        "medium_label": "Chalk pastel",
        "palette": [
            {"name": "midnight", "hex": "#1b2a49"},
            {"name": "moon", "hex": "#f4efd8"},
            {"name": "lantern", "hex": "#f2b441"},
            {"name": "moss", "hex": "#5d8a66"},
            {"name": "rose", "hex": "#d98c9a"},
        ],
        "line": "soft smudged edges",
        "lighting": "moonlight and lantern glow",
        "composition": "low horizons, big skies",
        "style_prompt_prefix": "Soft chalk-pastel picture-book art on dark blue paper.",
        "avoid": ["photographic realism"],
    },
    "qa": {
        "format_key": "bedtime-wonderings",
        "format_label": "Bedtime wonderings",
        "question_types": ["what if", "who is sleepy"],
        "intro_hook": "The kids yawn and settle in.",
        "outro_hook": "The kids say goodnight.",
        "moments_min": 7,
        "moments_max": 8,
        "kid_notes": {"kid_older": "Whispers guesses.", "kid_younger": "Counts stars."},
    },
    "sound": {
        "ambience": ["crickets_night", "owl_hoot"],
        "sfx": ["bell_chime"],
        "music_direction": "slow music box lullaby",
    },
    "animation": {"intensity": 0.5, "effects": ["fireflies"]},
    "rationale": "Hushed night look unlike the sunny gouache pilot.",
}


def valid_blueprint(**changes) -> dict:
    """A deep copy of VALID; dotted keys change nested fields.

    Example: valid_blueprint(**{"art.medium_key": "x"}).
    """
    data = copy.deepcopy(VALID)
    for dotted, value in changes.items():
        *path, last = dotted.split(".")
        node = data
        for part in path:
            node = node[part]
        node[last] = value
    return data


def context() -> Context:
    return Context(
        terms=["gun"],
        gentle=["monster"],
        sounds={
            "crickets_night": "ambience",
            "owl_hoot": "ambience",
            "rain_soft": "ambience",
            "bell_chime": "sfx",
            "music_box": "music",
        },
        effects=frozenset({"rain", "snow", "fireflies"}),
    )
