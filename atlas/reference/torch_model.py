from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

from atlas.reference.transformer import Config, TinyTransformer


def rms(values, weight):
    return values * torch.rsqrt(values.square().mean(-1, keepdim=True) + 1e-6) * weight


def rotary(values, positions):
    frequency = 10000 ** (
        -torch.arange(0, values.shape[-1], 2, device=values.device, dtype=values.dtype)
        / values.shape[-1]
    )
    angles = positions[:, None] * frequency[None, :]
    even, odd = values[..., 0::2], values[..., 1::2]
    return torch.stack(
        (even * angles.cos() - odd * angles.sin(), even * angles.sin() + odd * angles.cos()), dim=-1
    ).flatten(-2)


class Block(nn.Module):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        dimension, head = config.dimension, config.dimension // config.heads
        self.q = nn.Linear(dimension, dimension, bias=False)
        self.k = nn.Linear(dimension, head * config.kv_heads, bias=False)
        self.v = nn.Linear(dimension, head * config.kv_heads, bias=False)
        self.o = nn.Linear(dimension, dimension, bias=False)
        self.gate = nn.Linear(dimension, config.hidden, bias=False)
        self.up = nn.Linear(dimension, config.hidden, bias=False)
        self.down = nn.Linear(config.hidden, dimension, bias=False)
        self.norm1 = nn.Parameter(torch.ones(dimension))
        self.norm2 = nn.Parameter(torch.ones(dimension))

    def forward(self, values, previous=None):
        config = self.config
        batch, length, _ = values.shape
        offset = previous[0].shape[-2] if previous is not None else 0
        normalized = rms(values, self.norm1)
        head = config.dimension // config.heads
        query = self.q(normalized).view(batch, length, config.heads, head).transpose(1, 2)
        key = self.k(normalized).view(batch, length, config.kv_heads, head).transpose(1, 2)
        value = self.v(normalized).view(batch, length, config.kv_heads, head).transpose(1, 2)
        positions = torch.arange(offset, offset + length, device=values.device, dtype=values.dtype)
        query, key = rotary(query, positions), rotary(key, positions)
        if previous is not None:
            key, value = (
                torch.cat((previous[0], key), dim=-2),
                torch.cat((previous[1], value), dim=-2),
            )
        cache = key, value
        groups = config.heads // config.kv_heads
        key, value = key.repeat_interleave(groups, dim=1), value.repeat_interleave(groups, dim=1)
        mask = (
            torch.arange(key.shape[-2], device=values.device)[None, :]
            <= torch.arange(offset, offset + length, device=values.device)[:, None]
        )
        attended = F.scaled_dot_product_attention(query, key, value, attn_mask=mask)
        values = values + self.o(attended.transpose(1, 2).reshape(batch, length, config.dimension))
        normalized = rms(values, self.norm2)
        values = values + self.down(F.silu(self.gate(normalized)) * self.up(normalized))
        return values, cache


class TorchTinyTransformer(nn.Module):
    """Trainable counterpart to the NumPy reference; deliberately small and untrained."""

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config()
        if self.config.dimension % self.config.heads or self.config.heads % self.config.kv_heads:
            raise ValueError("Invalid head configuration")
        if self.config.dimension // self.config.heads % 2:
            raise ValueError("RoPE needs an even head dimension")
        self.embedding = nn.Embedding(self.config.vocabulary, self.config.dimension)
        self.layers = nn.ModuleList(Block(self.config) for _ in range(self.config.layers))
        self.output = nn.Linear(self.config.dimension, self.config.vocabulary, bias=False)

    def forward(self, tokens, cache=None):
        values, current = self.embedding(tokens), []
        for index, layer in enumerate(self.layers):
            values, pair = layer(values, None if cache is None else cache[index])
            current.append(pair)
        logits = self.output(
            rms(values, torch.ones(self.config.dimension, device=values.device, dtype=values.dtype))
        )
        return logits, current

    @torch.no_grad()
    def load_numpy(self, reference: TinyTransformer):
        if reference.config != self.config:
            raise ValueError("Reference configurations differ")
        self.embedding.weight.copy_(torch.from_numpy(reference.embedding))
        self.output.weight.copy_(torch.from_numpy(reference.output.T))
        for layer, weights in zip(self.layers, reference.layers, strict=True):
            for name, values in weights.items():
                target = getattr(layer, name)
                if isinstance(target, nn.Linear):
                    target.weight.copy_(torch.from_numpy(values.T))
                else:
                    target.copy_(torch.from_numpy(values))
