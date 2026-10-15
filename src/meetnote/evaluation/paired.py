"""评估用的内存抽取复用工具，不复用字段或关系核验结果。"""

import hashlib
import json

from meetnote.llm.client import strict_schema


class ExtractionReplay:
    def __init__(self, base, records):
        self.base, self.records = base, records
        self.hits = self.misses = 0

    async def generate(self, task, instructions, context, schema):
        if task != "extract":
            return await self.base.generate(task, instructions, context, schema)
        key = hashlib.sha256(
            json.dumps(
                [instructions, context, strict_schema(schema)], ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        if key in self.records:
            self.hits += 1
            return schema.model_validate_json(self.records[key])
        self.misses += 1
        result = await self.base.generate(task, instructions, context, schema)
        self.records[key] = result.model_dump_json()
        return result

    async def close(self):
        await self.base.close()
