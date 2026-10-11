from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import numpy as np


@dataclass
class Page:
    id: int
    key: np.ndarray | None
    value: np.ndarray | None
    count: int = 0
    references: int = 1
    last_used: float = 0
    pinned: bool = False


class PagedKVCache:
    """Exact CPU tensors, copy-on-write prefixes and disk spill for reference experiments."""

    def __init__(self, page_size: int, resident_pages: int, directory: Path):
        if min(page_size, resident_pages) < 1:
            raise ValueError("Invalid page capacity")
        self.page_size, self.capacity, self.directory = page_size, resident_pages, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.pages, self.sequences = {}, {}
        self.next_id = 0
        self.spills = self.restores = 0

    def _resident(self):
        return [page for page in self.pages.values() if page.key is not None]

    def _space(self, exclude: int | None = None):
        if len(self._resident()) < self.capacity:
            return
        candidates = [page for page in self._resident() if not page.pinned and page.id != exclude]
        if not candidates:
            raise MemoryError("All resident pages are pinned")
        victim = min(candidates, key=lambda page: (page.last_used, page.id))
        np.savez(self.directory / f"{victim.id}.npz", key=victim.key, value=victim.value)
        victim.key = victim.value = None
        self.spills += 1

    def _load(self, page_id: int) -> Page:
        page = self.pages[page_id]
        if page.key is None:
            self._space(exclude=page_id)
            with np.load(self.directory / f"{page_id}.npz", allow_pickle=False) as arrays:
                page.key, page.value = arrays["key"], arrays["value"]
            self.restores += 1
        page.last_used = monotonic()
        return page

    def _new(self, key: np.ndarray, value: np.ndarray) -> Page:
        self._space()
        page = Page(
            self.next_id,
            np.zeros((key.shape[0], self.page_size, key.shape[-1]), dtype=key.dtype),
            np.zeros((value.shape[0], self.page_size, value.shape[-1]), dtype=value.dtype),
            last_used=monotonic(),
        )
        self.pages[page.id] = page
        self.next_id += 1
        return page

    def append(self, sequence: str, key: np.ndarray, value: np.ndarray):
        if key.ndim != 3 or key.shape != value.shape:
            raise ValueError("Use heads × tokens × head-dimension tensors")
        table = self.sequences.setdefault(sequence, [])
        for index in range(key.shape[1]):
            page = self._load(table[-1]) if table else None
            if page is None or page.count == self.page_size:
                page = self._new(key, value)
                table.append(page.id)
            elif page.references > 1:
                original_key, original_value, count = page.key.copy(), page.value.copy(), page.count
                page.references -= 1
                page = self._new(key, value)
                page.key[:], page.value[:], page.count = original_key, original_value, count
                table[-1] = page.id
            page.key[:, page.count], page.value[:, page.count] = key[:, index], value[:, index]
            page.count += 1

    def fork(self, source: str, target: str):
        if target in self.sequences:
            raise ValueError("Target sequence already exists")
        self.sequences[target] = list(self.sequences[source])
        for page_id in self.sequences[target]:
            self.pages[page_id].references += 1

    def gather(self, sequence: str):
        keys, values = [], []
        for page_id in self.sequences[sequence]:
            page = self._load(page_id)
            keys.append(page.key[:, : page.count].copy())
            values.append(page.value[:, : page.count].copy())
        if not keys:
            raise ValueError("Empty sequence")
        return np.concatenate(keys, axis=1), np.concatenate(values, axis=1)

    def release(self, sequence: str):
        for page_id in self.sequences.pop(sequence, []):
            page = self.pages[page_id]
            page.references -= 1
            if not page.references:
                self.pages.pop(page_id)
                (self.directory / f"{page_id}.npz").unlink(missing_ok=True)

    def snapshot(self):
        return {
            "pages": len(self.pages),
            "resident_pages": len(self._resident()),
            "resident_bytes": sum(page.key.nbytes + page.value.nbytes for page in self._resident()),
            "spills": self.spills,
            "restores": self.restores,
            "unused_token_slots": sum(self.page_size - page.count for page in self.pages.values()),
        }
