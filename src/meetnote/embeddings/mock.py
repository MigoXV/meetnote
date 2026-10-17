"""仅用于 mock 接线验证；固定向量不代表真实语义或召回质量。"""

from array import array


class MockEncoder:
    async def encode(self, texts):
        return "mock-embedding", [array("f", [1.0] + [0.0] * 1023) for _ in texts], 0
