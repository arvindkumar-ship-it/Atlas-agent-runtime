"""A local byte-language-model experiment. This never supplies the agent's planner."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import torch
from torch.nn import functional as F

from atlas.reference.torch_model import TorchTinyTransformer
from atlas.reference.transformer import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=Path, help="Optional UTF-8 training text")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("data/reference-model.pt"))
    parser.add_argument("--report", type=Path, default=Path("data/reference-training.json"))
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("steps must be positive")
    torch.manual_seed(42)
    torch.set_num_threads(2)
    text = (
        args.text.read_text(encoding="utf-8")
        if args.text
        else "Plan, inspect, repair, verify. Keep evidence.\n" * 128
    )
    tokens = torch.tensor(list(text.encode("utf-8")), dtype=torch.long)
    length, batch = 32, 8
    if len(tokens) < length + 2:
        parser.error("training text must contain at least 34 UTF-8 bytes")
    model = TorchTinyTransformer(Config())
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.003)
    rows = []
    for step in range(args.steps):
        starts = torch.randint(0, len(tokens) - length, (batch,))
        windows = torch.stack([tokens[index : index + length + 1] for index in starts])
        logits, _ = model(windows[:, :-1])
        loss = F.cross_entropy(logits.flatten(0, 1), windows[:, 1:].flatten())
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
        optimizer.step()
        rows.append({"step": step + 1, "loss": loss.item()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"config": asdict(model.config), "state_dict": model.state_dict()}, args.output)
    args.report.write_text(
        json.dumps(
            {
                "seed": 42,
                "device": "cpu",
                "purpose": "toy training sanity check",
                "training_bytes": len(tokens),
                "held_out_evaluation": None,
                "losses": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"Training loss: {rows[0]['loss']:.4f} -> {rows[-1]['loss']:.4f}; checkpoint: {args.output}"
    )


if __name__ == "__main__":
    main()
