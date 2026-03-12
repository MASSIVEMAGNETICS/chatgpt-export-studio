# =============================================================
# FILE: bandofy_export_studio/neural_net.py
# PURPOSE: Pure-Python synthetic neural network engine.
#          Implements a 1-hidden-layer autoencoder with 1024
#          hidden neurons visualised as a 32×32 grid.
#          Each cell colour = HSV(column_hue, saturation,
#          activation_intensity); top-activated cells receive
#          overlay symbols.  No external dependencies.
# =============================================================

from __future__ import annotations

import math
import random
import re
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

# ── Grid constants ──────────────────────────────────────────────────────────

GRID_SIDE: int = 32                        # 32 × 32 = 1 024 cells
GRID_NEURONS: int = GRID_SIDE * GRID_SIDE  # 1 024
DEFAULT_VOCAB_SIZE: int = 60               # default bag-of-words feature dim
DEFAULT_LR: float = 0.005
DEFAULT_EPOCHS: int = 40

# Overlay symbols for top-activated neurons (index 0 = no symbol)
_SYMBOLS: Tuple[str, ...] = (
    "", "∑", "φ", "λ", "δ", "∇", "∞", "◆", "★", "✦",
    "Ω", "π", "α", "β", "γ", "ε", "ζ", "η", "θ", "ι",
)

_STOPWORDS = frozenset([
    "the", "a", "an", "is", "it", "in", "on", "of", "to", "and",
    "i", "you", "we", "that", "this", "was", "for", "are", "with",
    "my", "your", "have", "has", "be", "do", "at", "from", "by",
    "but", "or", "not", "can", "will", "they", "their", "there",
    "what", "how", "when", "where", "which", "who", "if", "as",
    "so", "then", "than", "about", "also", "just", "its", "our",
    "he", "she", "his", "her", "me", "him", "us", "them", "been",
    "had", "one", "two", "get", "got", "ll", "re", "ve", "did",
    "does", "said", "say", "use", "see", "like", "think", "know",
    "want",
])


# ── Colour helper ────────────────────────────────────────────────────────────

def _hsv_to_hex(h: float, s: float, v: float) -> str:
    """Convert HSV (all values 0-1) to #rrggbb hex string."""
    if s == 0.0:
        c = int(v * 255)
        return f"#{c:02x}{c:02x}{c:02x}"
    hi = int(h * 6) % 6
    f = (h * 6) - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t),
               (p, q, v), (t, p, v), (v, p, q)][hi]
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


# ── Vocabulary ───────────────────────────────────────────────────────────────

class Vocabulary:
    """Bag-of-words vocabulary built from conversation message texts."""

    def __init__(self, max_vocab: int = DEFAULT_VOCAB_SIZE) -> None:
        self.max_vocab = max_vocab
        self.words: List[str] = []
        self.w2i: Dict[str, int] = {}
        self.built: bool = False

    def build(self, texts: List[str]) -> int:
        """Build vocabulary from a list of raw text strings.

        Returns the actual vocabulary size (may be < *max_vocab*).
        """
        counts: Counter = Counter()
        for t in texts:
            for w in re.findall(r"\b[a-zA-Z]{3,}\b", t.lower()):
                if w not in _STOPWORDS:
                    counts[w] += 1
        self.words = [w for w, _ in counts.most_common(self.max_vocab)]
        self.w2i = {w: i for i, w in enumerate(self.words)}
        self.built = True
        return len(self.words)

    def encode(self, text: str) -> List[float]:
        """Return an L2-normalised bag-of-words vector for *text*."""
        n = len(self.words)
        if not n:
            return []
        vec = [0.0] * n
        for w in re.findall(r"\b[a-zA-Z]{3,}\b", text.lower()):
            idx = self.w2i.get(w)
            if idx is not None:
                vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def to_dict(self) -> Dict:
        return {"max_vocab": self.max_vocab, "words": self.words}

    @classmethod
    def from_dict(cls, d: Dict) -> "Vocabulary":
        v = cls(d.get("max_vocab", DEFAULT_VOCAB_SIZE))
        v.words = list(d.get("words", []))
        v.w2i = {w: i for i, w in enumerate(v.words)}
        v.built = bool(v.words)
        return v


# ── Neural Network Engine ────────────────────────────────────────────────────

class NeuralNetEngine:
    """One-hidden-layer autoencoder with 1 024 hidden neurons.

    Architecture
    ------------
    input  -(W1, b1, ReLU)→  hidden[1024]  -(W2, b2, sigmoid)→  output

    Visualisation
    -------------
    The hidden layer is mapped to a 32×32 grid where every cell is
    coloured by HSV(column_hue, saturation, activation_intensity) and
    optionally labelled with a symbol (∑ φ λ …) for the top 4 % most
    activated neurons.  Colour and symbol lists are updated after every
    training step and read (safely under the GIL) by the UI thread.
    """

    def __init__(
        self,
        vocab_size: int = DEFAULT_VOCAB_SIZE,
        learning_rate: float = DEFAULT_LR,
        seed: int = 42,
    ) -> None:
        self.n_in: int = max(1, vocab_size)
        self.n_h: int = GRID_NEURONS      # 1 024
        self.n_out: int = self.n_in
        self.lr: float = learning_rate
        self._seed = seed
        self._init_weights()

        # ── Visualisation state (written by train thread, read by UI) ──
        self.colors: List[str] = ["#080d14"] * self.n_h
        self.symbols: List[str] = [""] * self.n_h
        self.activations: List[float] = [0.0] * self.n_h

        # ── Training metrics ────────────────────────────────────────────
        self.epoch: int = 0
        self.step: int = 0
        self.loss: float = 0.0
        self.loss_history: List[float] = []
        self.running: bool = False

    # ── Weight initialisation ────────────────────────────────────────────

    def _init_weights(self) -> None:
        std1 = math.sqrt(2.0 / self.n_in)
        std2 = math.sqrt(2.0 / self.n_h)
        rng = random.Random(self._seed)
        # W1[j][i]: weight from input i to hidden neuron j
        self.W1: List[List[float]] = [
            [rng.gauss(0.0, std1) for _ in range(self.n_in)]
            for _ in range(self.n_h)
        ]
        self.b1: List[float] = [0.0] * self.n_h
        # W2[k][j]: weight from hidden neuron j to output k
        self.W2: List[List[float]] = [
            [rng.gauss(0.0, std2) for _ in range(self.n_h)]
            for _ in range(self.n_out)
        ]
        self.b2: List[float] = [0.0] * self.n_out

    # ── Forward pass ─────────────────────────────────────────────────────

    def _forward(self, x: List[float]) -> Tuple[List[float], List[float]]:
        """Compute hidden activations and output.  Returns (h, out)."""
        W1, b1, n_h = self.W1, self.b1, self.n_h
        n = len(x)
        # Hidden layer: ReLU
        h = [
            max(0.0, sum(W1[j][i] * x[i] for i in range(n)) + b1[j])
            for j in range(n_h)
        ]
        # Output layer: sigmoid with clamped exponent
        W2, b2, n_out = self.W2, self.b2, self.n_out
        out = [
            1.0 / (1.0 + math.exp(
                -max(-15.0, min(15.0,
                     sum(W2[k][j] * h[j] for j in range(n_h)) + b2[k]))
            ))
            for k in range(n_out)
        ]
        return h, out

    # ── Backward pass ────────────────────────────────────────────────────

    def _backward(
        self,
        x: List[float],
        h: List[float],
        out: List[float],
    ) -> float:
        """Update weights via backpropagation.  Returns MSE loss."""
        n = self.n_out
        lr = self.lr

        # Output delta: dL/dout × sigmoid'
        d_out = [
            (out[k] - x[k]) * out[k] * (1.0 - out[k])
            for k in range(n)
        ]

        # Update W2, b2
        for k in range(n):
            dk = d_out[k] * lr
            W2k = self.W2[k]
            for j in range(self.n_h):
                W2k[j] -= dk * h[j]
            self.b2[k] -= dk

        # Update W1, b1 — skip dead (h[j]==0) neurons for speed
        for j in range(self.n_h):
            if h[j] <= 0.0:
                continue
            # Compute hidden delta on-demand
            dj = (sum(d_out[k] * self.W2[k][j] for k in range(n))) * lr
            if dj == 0.0:
                continue
            W1j = self.W1[j]
            for i in range(len(x)):
                W1j[i] -= dj * x[i]
            self.b1[j] -= dj

        return sum((out[k] - x[k]) ** 2 for k in range(n)) / n

    # ── Visualisation update ─────────────────────────────────────────────

    def _refresh_vis(self, h: List[float]) -> None:
        """Recompute colours and symbols from hidden activations *h*."""
        self.activations = h
        max_act = max(h) if h and max(h) > 0.0 else 1.0
        gs = GRID_SIDE

        # ── Symbols: top 4 % of neurons ──────────────────────────────
        threshold = max_act * 0.6
        ranked = sorted(range(self.n_h), key=lambda j: h[j], reverse=True)
        n_sym = max(1, int(self.n_h * 0.04))
        new_sym: List[str] = [""] * self.n_h
        for rank, idx in enumerate(ranked[:n_sym]):
            if h[idx] >= threshold:
                new_sym[idx] = _SYMBOLS[(rank % (len(_SYMBOLS) - 1)) + 1]
        self.symbols = new_sym

        # ── Colours: hue = col/GRID_SIDE cycling across the grid ─────
        new_colors: List[str] = []
        for j in range(self.n_h):
            col = j % gs
            norm = min(1.0, h[j] / max_act)
            hue = col / gs            # full spectrum 0..1 across columns
            sat = 0.55 + 0.45 * norm
            val = 0.07 + 0.88 * norm
            new_colors.append(_hsv_to_hex(hue, sat, val))
        self.colors = new_colors

    # ── Training ─────────────────────────────────────────────────────────

    def train_step(self, samples: List[List[float]]) -> float:
        """Train on one randomly chosen sample.  Returns loss."""
        if not samples:
            return 0.0
        x = list(random.choice(samples))
        pad = self.n_in
        if len(x) != pad:
            x = (x + [0.0] * pad)[:pad]
        h, out = self._forward(x)
        loss = self._backward(x, h, out)
        self.step += 1
        self.loss = loss
        self._refresh_vis(h)
        return loss

    def run_epoch(self, samples: List[List[float]]) -> float:
        """Train one full pass over all samples.  Returns average loss."""
        if not samples:
            return 0.0
        random.shuffle(samples)
        total = sum(self.train_step([x]) for x in samples)
        avg = total / len(samples)
        self.epoch += 1
        self.loss_history.append(avg)
        return avg

    def reset(self, vocab_size: Optional[int] = None) -> None:
        """Re-initialise weights and clear metrics."""
        if vocab_size is not None:
            self.n_in = self.n_out = max(1, vocab_size)
        self._seed = random.randint(0, 2 ** 31)
        self._init_weights()
        self.colors = ["#080d14"] * self.n_h
        self.symbols = [""] * self.n_h
        self.activations = [0.0] * self.n_h
        self.epoch = 0
        self.step = 0
        self.loss = 0.0
        self.loss_history = []

    # ── Serialisation ────────────────────────────────────────────────────

    def export_model(self, vocab: Optional[Vocabulary] = None) -> Dict:
        """Return a JSON-serialisable dict of the trained model."""
        return {
            "schema_version": "2.0",
            "name": "ChatGPT Export Studio — Synthetic Neural Model",
            "architecture": {
                "type": "autoencoder",
                "activation": "relu+sigmoid",
                "input_size": self.n_in,
                "hidden_size": self.n_h,
                "output_size": self.n_out,
                "grid_shape": [GRID_SIDE, GRID_SIDE],
            },
            "hyperparameters": {"learning_rate": self.lr},
            "training": {
                "epochs": self.epoch,
                "total_steps": self.step,
                "final_loss": round(self.loss, 8),
                "loss_history": [round(v, 8) for v in self.loss_history[-200:]],
            },
            "vocabulary": vocab.to_dict() if vocab else None,
            "weights": {
                "W1": [[round(v, 8) for v in row] for row in self.W1],
                "b1": [round(v, 8) for v in self.b1],
                "W2": [[round(v, 8) for v in row] for row in self.W2],
                "b2": [round(v, 8) for v in self.b2],
            },
        }
