"""A GPU timeout in the voice engine is retried, not fatal (the Metal watchdog can fire under load)."""

from __future__ import annotations

import numpy as np
import pytest

from video_pipeline import synthesize_voices as sv

GPU_TIMEOUT = RuntimeError(
    "[METAL] Command buffer execution failed: Caused GPU Timeout Error "
    "(00000002:kIOGPUCommandBufferCallbackErrorTimeout)."
)


class FlakyEngine:
    """Raises the given errors in order, then speaks."""

    sample_rate = 24000

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.recovered = 0

    def synthesize(self, text, voice, seed):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return np.zeros(10, dtype=np.float32)

    def recover(self):
        self.recovered += 1


def test_gpu_timeout_is_recognised():  # classification
    assert sv.is_gpu_timeout(GPU_TIMEOUT)
    assert not sv.is_gpu_timeout(RuntimeError("model exploded"))
    assert not sv.is_gpu_timeout(ValueError("kIOGPUCommandBufferCallbackErrorTimeout"))


def test_gpu_timeouts_are_retried_then_succeed():  # recovery
    engine = FlakyEngine([GPU_TIMEOUT, GPU_TIMEOUT])
    sleeps = []
    audio = sv.synthesize_retrying(engine, "Hi.", "tara", 7, sleep=sleeps.append)
    assert audio.shape == (10,)
    assert engine.calls == 3 and engine.recovered == 2
    assert sleeps == list(sv.GPU_BACKOFF_S[:2])


def test_persistent_gpu_timeout_gives_up_after_the_retries():  # no endless loop
    engine = FlakyEngine([GPU_TIMEOUT] * 10)
    with pytest.raises(RuntimeError, match="GPU Timeout"):
        sv.synthesize_retrying(engine, "Hi.", "tara", 7, sleep=lambda s: None)
    assert engine.calls == sv.GPU_RETRIES + 1


def test_other_errors_are_not_retried():  # only GPU timeouts are retried
    engine = FlakyEngine([RuntimeError("model exploded")])
    with pytest.raises(RuntimeError, match="model exploded"):
        sv.synthesize_retrying(engine, "Hi.", "tara", 7, sleep=lambda s: None)
    assert engine.calls == 1 and engine.recovered == 0
