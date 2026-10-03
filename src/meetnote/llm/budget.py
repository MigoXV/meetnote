"""本地 tokenizer 计数；从不下载权重或加载模型。"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4)
def load_tokenizer(path: str):
    from tokenizers import Tokenizer

    data = Path(path).read_bytes()
    return Tokenizer.from_str(data.decode()), hashlib.sha256(data).hexdigest()


class Budget:
    def __init__(self, settings):
        self.settings = settings
        self.tokenizer = None
        self.fingerprint = "utf8-upper-bound"
        if settings.provider != "mock":
            self.tokenizer, self.fingerprint = load_tokenizer(str(settings.tokenizer_path))

    def count(self, value) -> int:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if self.tokenizer is None:
            return len(text.encode("utf-8"))
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def input_count(self, instructions, context, schema):
        from meetnote.llm.client import strict_schema

        return self.count(instructions) + self.count(context) + self.count(strict_schema(schema))

    def check(self, instructions, context, schema):
        count = self.input_count(instructions, context, schema)
        if count > self.settings.input_token_budget:
            from meetnote.llm.client import ModelFailure

            raise ModelFailure("input_budget_exceeded")
        return count
