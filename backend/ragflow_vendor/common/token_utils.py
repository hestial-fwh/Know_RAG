def num_tokens_from_string(text):
    if not text:
        return 0
    return max(1, len(str(text)) // 2)

class _Encoder:
    def encode(self, text):
        return list(str(text))
    def decode(self, tokens):
        return ''.join(tokens)
encoder = _Encoder()
