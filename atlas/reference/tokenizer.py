from collections import Counter


class ByteBPE:
    def __init__(self):
        self.merges: list[tuple[int, int]] = []
        self.vocabulary = {index: bytes([index]) for index in range(256)}

    @staticmethod
    def _merge(tokens: list[int], pair: tuple[int, int], token: int) -> list[int]:
        output, index = [], 0
        while index < len(tokens):
            if index + 1 < len(tokens) and (tokens[index], tokens[index + 1]) == pair:
                output.append(token)
                index += 2
            else:
                output.append(tokens[index])
                index += 1
        return output

    def train(self, text: str, merges: int = 64):
        if self.merges:
            raise ValueError("Train a fresh tokenizer")
        tokens = list(text.encode("utf-8"))
        for _ in range(merges):
            counts = Counter(zip(tokens, tokens[1:], strict=False))
            if not counts:
                break
            pair, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
            if count < 2:
                break
            token = len(self.vocabulary)
            self.vocabulary[token] = self.vocabulary[pair[0]] + self.vocabulary[pair[1]]
            self.merges.append(pair)
            tokens = self._merge(tokens, pair, token)

    def encode(self, text: str) -> list[int]:
        tokens = list(text.encode("utf-8"))
        for index, pair in enumerate(self.merges, 256):
            tokens = self._merge(tokens, pair, index)
        return tokens

    def decode(self, tokens: list[int]) -> str:
        return b"".join(self.vocabulary[token] for token in tokens).decode("utf-8")
