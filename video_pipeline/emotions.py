"""Feelings for episode lines (spec 017): the allowed list, how each changes delivery, and the
sound-tag and feeling counts generated banter must meet. Read from `config/emotions.json`
(kept apart from voices.json so editing a feeling does not change the voice settings hash)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from video_pipeline.contract import ConfigError

NEUTRAL = "neutral"
EMOTIONS_FILE = "emotions.json"


class Feeling(BaseModel):
    """How a feeling changes a synthesized line: speed factor, pitch offset, loudness change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    speed: float = Field(ge=0.8, le=1.25)
    semitones: float = Field(ge=-2, le=2)
    gain_db: float = Field(ge=-9, le=3)

    @property
    def is_neutral(self) -> bool:
        return self.speed == 1 and self.semitones == 0 and self.gain_db == 0


class Generation(BaseModel):
    """Counts newly generated banter must meet (per Q&A moment)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tags_per_moment_min: int = Field(ge=0)
    tags_per_moment_max: int = Field(ge=0)
    feelings_per_moment_min: int = Field(ge=0, le=5)

    @model_validator(mode="after")
    def _min_not_above_max(self) -> Generation:
        if self.tags_per_moment_min > self.tags_per_moment_max:
            raise ValueError("tags_per_moment_min must not be above tags_per_moment_max")
        return self


class EmotionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feelings: dict[str, Feeling]
    generation: Generation

    @model_validator(mode="after")
    def _neutral_changes_nothing(self) -> EmotionSettings:
        neutral = self.feelings.get(NEUTRAL)
        if neutral is None:
            raise ValueError("feeling 'neutral' is required")
        if not neutral.is_neutral:
            raise ValueError("feeling 'neutral' must be speed 1.0, semitones 0, gain_db 0")
        return self

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.feelings)

    def feeling(self, name: str | None) -> Feeling:
        """The delivery for `name`; a missing feeling is neutral."""
        return self.feelings[name or NEUTRAL]


def _describe(err: dict) -> str:
    loc = [str(part) for part in err["loc"]]
    if len(loc) >= 3 and loc[0] == "feelings":
        return f"feeling {loc[1]!r}: {'.'.join(loc[2:])}: {err['msg']}"
    where = ".".join(loc)
    return f"{where}: {err['msg']}" if where else err["msg"]


def load_emotions(config_dir: Path) -> EmotionSettings:
    """Load and check `config_dir/emotions.json`; errors name the feeling and field (AC-3.4)."""
    path = Path(config_dir) / EMOTIONS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"feelings configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"feelings configuration is not valid JSON: {path} ({exc})") from None
    try:
        return EmotionSettings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(_describe(err) for err in exc.errors())
        raise ConfigError(f"{path}: {problems}") from None
