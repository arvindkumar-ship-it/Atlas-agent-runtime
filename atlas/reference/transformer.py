from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def stable_softmax(values: np.ndarray, axis: int = -1) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    maximum = np.max(values, axis=axis, keepdims=True)
    safe_maximum = np.where(np.isfinite(maximum), maximum, 0)
    exponent = np.exp(values - safe_maximum)
    total = exponent.sum(axis=axis, keepdims=True)
    return np.divide(exponent, total, out=np.zeros_like(exponent), where=total != 0)


def rms_norm(values: np.ndarray, weight: np.ndarray, epsilon: float = 1e-6) -> np.ndarray:
    return values / np.sqrt(np.mean(values**2, axis=-1, keepdims=True) + epsilon) * weight


def rope(values: np.ndarray, positions: np.ndarray, base: float = 10000) -> np.ndarray:
    dimension = values.shape[-1]
    if dimension % 2:
        raise ValueError("RoPE head dimension must be even")
    frequency = base ** (-np.arange(0, dimension, 2) / dimension)
    angles = np.asarray(positions)[:, None] * frequency[None, :]
    cosine, sine = np.cos(angles), np.sin(angles)
    result = np.empty_like(values)
    even, odd = values[..., 0::2], values[..., 1::2]
    result[..., 0::2] = even * cosine - odd * sine
    result[..., 1::2] = even * sine + odd * cosine
    return result


def attention(
    query: np.ndarray, key: np.ndarray, value: np.ndarray, causal: bool = True, offset: int = 0
) -> np.ndarray:
    if query.shape[-1] != key.shape[-1] or key.shape != value.shape:
        raise ValueError("Incompatible attention tensors")
    if query.shape[1] % key.shape[1]:
        raise ValueError("Query heads must be divisible by KV heads")
    groups = query.shape[1] // key.shape[1]
    key, value = np.repeat(key, groups, axis=1), np.repeat(value, groups, axis=1)
    scores = query @ key.swapaxes(-1, -2) / np.sqrt(query.shape[-1])
    if causal:
        mask = np.arange(key.shape[-2])[None, :] > (np.arange(query.shape[-2]) + offset)[:, None]
        scores = np.where(mask, -np.inf, scores)
    return stable_softmax(scores) @ value


class KVCache:
    def __init__(self, layers: int, max_tokens: int = 4096):
        self.layers: list[tuple[np.ndarray, np.ndarray] | None] = [None] * layers
        self.max_tokens = max_tokens

    @property
    def tokens(self) -> int:
        first = self.layers[0]
        return first[0].shape[-2] if first is not None else 0

    @property
    def bytes_used(self) -> int:
        return sum(
            key.nbytes + value.nbytes
            for pair in self.layers
            if pair is not None
            for key, value in [pair]
        )

    def append(self, layer: int, key: np.ndarray, value: np.ndarray):
        old = self.layers[layer]
        if old is not None:
            key, value = (
                np.concatenate([old[0], key], axis=-2),
                np.concatenate([old[1], value], axis=-2),
            )
        if key.shape[-2] > self.max_tokens:
            raise MemoryError("Reference KV token capacity exceeded")
        self.layers[layer] = (key.copy(), value.copy())
        return key, value


@dataclass(frozen=True)
class Config:
    vocabulary: int = 256
    dimension: int = 32
    layers: int = 2
    heads: int = 4
    kv_heads: int = 2
    hidden: int = 64


class TinyTransformer:
    """Random-weight decoder for numerical experiments, not a pretrained language model."""

    def __init__(self, config: Config | None = None, seed: int = 42):
        config = config or Config()
        if config.dimension % config.heads or config.heads % config.kv_heads:
            raise ValueError("Invalid head configuration")
        self.config = config
        dimension = config.dimension
        head = dimension // config.heads
        random = np.random.default_rng(seed)

        def matrix(rows, columns):
            return random.standard_normal((rows, columns)) / np.sqrt(rows)

        self.embedding = matrix(config.vocabulary, dimension)
        self.layers = [
            dict(
                q=matrix(dimension, dimension),
                k=matrix(dimension, head * config.kv_heads),
                v=matrix(dimension, head * config.kv_heads),
                o=matrix(dimension, dimension),
                gate=matrix(dimension, config.hidden),
                up=matrix(dimension, config.hidden),
                down=matrix(config.hidden, dimension),
                norm1=np.ones(dimension),
                norm2=np.ones(dimension),
            )
            for _ in range(config.layers)
        ]
        self.output = matrix(dimension, config.vocabulary)

    def forward(self, token_ids: np.ndarray, cache: KVCache | None = None) -> np.ndarray:
        config = self.config
        batch, length = token_ids.shape
        offset = cache.tokens if cache is not None else 0
        if cache is not None and offset + length > cache.max_tokens:
            raise MemoryError("Reference KV token capacity exceeded")
        values = self.embedding[token_ids]
        positions = np.arange(offset, offset + length)
        head_dimension = config.dimension // config.heads
        for index, layer in enumerate(self.layers):
            normalized = rms_norm(values, layer["norm1"])
            query = (
                (normalized @ layer["q"])
                .reshape(batch, length, config.heads, head_dimension)
                .transpose(0, 2, 1, 3)
            )
            key = (
                (normalized @ layer["k"])
                .reshape(batch, length, config.kv_heads, head_dimension)
                .transpose(0, 2, 1, 3)
            )
            value = (
                (normalized @ layer["v"])
                .reshape(batch, length, config.kv_heads, head_dimension)
                .transpose(0, 2, 1, 3)
            )
            query, key = rope(query, positions), rope(key, positions)
            if cache is not None:
                key, value = cache.append(index, key, value)
            attended = (
                attention(query, key, value, offset=offset)
                .transpose(0, 2, 1, 3)
                .reshape(batch, length, config.dimension)
            )
            values = values + attended @ layer["o"]
            normalized = rms_norm(values, layer["norm2"])
            gate = normalized @ layer["gate"]
            sigmoid = 1 / (1 + np.exp(-np.clip(gate, -60, 60)))
            values = values + ((gate * sigmoid) * (normalized @ layer["up"])) @ layer["down"]
        return rms_norm(values, np.ones(config.dimension)) @ self.output

    def prefill(self, token_ids: np.ndarray, max_tokens: int = 4096):
        cache = KVCache(self.config.layers, max_tokens)
        return self.forward(token_ids, cache), cache

    def decode(self, token_id: int, cache: KVCache):
        return self.forward(np.array([[token_id]], dtype=np.int64), cache)


def sample(
    logits: np.ndarray,
    random: np.random.Generator,
    temperature: float = 1,
    top_k: int | None = None,
    top_p: float = 1,
) -> int:
    if temperature < 0 or not 0 < top_p <= 1 or (top_k is not None and top_k < 1):
        raise ValueError("Invalid sampling parameters")
    if temperature == 0:
        return int(np.argmax(logits))
    values = logits.astype(np.float64) / temperature
    indices = np.argsort(values)[::-1]
    if top_k is not None:
        indices = indices[:top_k]
    probabilities = stable_softmax(values[indices])
    size = min(len(indices), int(np.searchsorted(np.cumsum(probabilities), top_p)) + 1)
    indices, probabilities = indices[:size], probabilities[:size]
    return int(random.choice(indices, p=probabilities / probabilities.sum()))


def quantize_int8(weights: np.ndarray):
    scales = np.max(np.abs(weights), axis=-1, keepdims=True) / 127
    scales = np.where(scales == 0, 1, scales)
    return np.clip(np.rint(weights / scales), -127, 127).astype(np.int8), scales
