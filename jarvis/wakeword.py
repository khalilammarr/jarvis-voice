"""Local "hey jarvis" detection with openWakeWord (ONNX, CPU, a few ms per frame)."""

from __future__ import annotations

import sys
import types

import numpy as np


def _no_training(*args, **kwargs):
    raise RuntimeError("openWakeWord custom-verifier training is not available inside Jarvis.")


def _openwakeword():
    """Import openWakeWord without its custom-verifier module.

    ``openwakeword/__init__`` imports a training helper that pulls in scikit-learn and
    scipy.stats: about 1.5 s of Jarvis's start-up, for a feature Jarvis never uses.
    A stand-in module keeps that import cheap.
    """
    name = "openwakeword.custom_verifier_model"
    if name not in sys.modules:
        stub = types.ModuleType(name)
        stub.train_custom_verifier = _no_training
        sys.modules[name] = stub
    import openwakeword.model
    import openwakeword.utils
    return openwakeword


class WakeWordDetector:
    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        oww = _openwakeword()
        oww.utils.download_models([model_name])  # no-op once cached
        self._model = oww.model.Model(wakeword_models=[model_name], inference_framework="onnx")
        self.threshold = threshold

    def score(self, frame: np.ndarray) -> float:
        return max(self._model.predict(frame).values(), default=0.0)

    def detected(self, frame: np.ndarray) -> bool:
        return self.score(frame) >= self.threshold

    def reset(self) -> None:
        """Clear the rolling buffers so the trigger phrase doesn't fire twice."""
        self._model.reset()
