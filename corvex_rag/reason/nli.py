"""Natural-language-inference model behind an interface. Local cross-encoder default;
a no-op fallback that defers everything to the LLM path when the model isn't installed."""
from __future__ import annotations

import abc

from ..config import Config


class NLIModel(abc.ABC):
    name: str

    @abc.abstractmethod
    def predict(self, premise: str, hypothesis: str) -> tuple[str, float]:
        """Return (label, prob) with label in {entail, neutral, contradict}."""


class CrossEncoderNLI(NLIModel):
    """`cross-encoder/nli-deberta-v3-base` via sentence-transformers.CrossEncoder.
    Label order for that model is [contradiction, entailment, neutral]."""
    name = "cross-encoder-nli"
    _LABELS = ("contradict", "entail", "neutral")

    def __init__(self, cfg: Config) -> None:
        from sentence_transformers import CrossEncoder  # optional dep

        self.model_name = cfg.section("contradiction", "nli", "model")
        self._model = CrossEncoder(self.model_name, cache_folder=str(cfg.model_cache_dir))

    def predict(self, premise, hypothesis):
        import numpy as np

        scores = self._model.predict([(premise, hypothesis)])[0]
        probs = np.exp(scores) / np.exp(scores).sum()
        i = int(probs.argmax())
        return self._LABELS[i], float(probs[i])


class NullNLI(NLIModel):
    """Always 'neutral'. Used when contradiction.nli.enabled is false or deps missing —
    every candidate pair then goes straight to LLM adjudication (if enabled)."""
    name = "null"

    def predict(self, premise, hypothesis):
        return ("neutral", 0.0)


def get_nli(cfg: Config) -> NLIModel:
    if cfg.section("contradiction", "nli", "enabled", default=False):
        try:
            return CrossEncoderNLI(cfg)
        except (ImportError, NotImplementedError):
            return NullNLI()
    return NullNLI()
