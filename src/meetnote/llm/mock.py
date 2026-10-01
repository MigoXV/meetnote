"""可重复的接线测试模型，输出明确标识为模拟结果。"""

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class MockClient:
    async def generate(self, task: str, instructions: str, context: dict, schema: type[T]) -> T:
        data: dict
        if task == "marker":
            data = {"commit_points": 2, "commit_level": "mid", "semantic_hints": ["模拟会议"]}
        elif task == "summary":
            text = context["buffer_text"]
            data = {
                "should_emit": True,
                "summary_end_anchor": text[-100:],
                "summary_atoms": [
                    {"summary": "[模拟摘要] " + text, "key_points": [text], "keywords": []}
                ],
                "remaining_text": "",
            }
        elif task == "lens":
            data = {"orientation": ["会议讨论"], "confidence": 0.8}
        elif task == "hint":
            data = {"section_candidates": ["会议要点", "行动与待确认"]}
        elif task == "plan":
            data = {
                "section_order": ["会议要点", "待确认"],
                "composer_brief": "[模拟] 根据已提交摘要整理",
            }
        elif task == "compose":
            summaries = context["summaries"]
            data = {
                "title": context.get("title") or "模拟会议纪要",
                "markdown": "# 模拟会议纪要\n\n" + "\n\n".join(s["summary"] for s in summaries),
                "evidence_trace": [s["id"] for s in summaries],
                "delivery_warnings": ["此结果来自 mock，仅用于验证接线，不代表摘要质量。"],
            }
        else:
            raise ValueError(task)
        return schema.model_validate(data)

    async def close(self) -> None:
        pass
