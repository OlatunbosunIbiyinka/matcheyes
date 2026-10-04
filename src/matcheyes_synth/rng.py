"""Named, independent random streams.

Each stream is seeded from a hash of (namespace, seed, name), so adding a random draw in one
part of the model does not shift the randomness used everywhere else. Only the standard
library `random` is used; nothing depends on wall-clock time, hash randomisation or set order.
"""

import hashlib
import random


def derive_seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class Streams:
    def __init__(self, *namespace: object) -> None:
        self._namespace = namespace
        self._streams: dict[str, random.Random] = {}

    def __getitem__(self, name: str) -> random.Random:
        stream = self._streams.get(name)
        if stream is None:
            stream = random.Random(derive_seed(*self._namespace, name))  # noqa: S311 - simulation, not crypto
            self._streams[name] = stream
        return stream
