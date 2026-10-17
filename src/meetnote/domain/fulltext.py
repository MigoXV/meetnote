"""全文能力及模型产物；不依赖 HTTP 或会话。"""

from typing import Literal

from pydantic import Field

from meetnote.domain.results import Result

Capability = Literal["polish", "abstract", "minutes", "keywords", "actions"]
DEFAULT_PIPELINE: tuple[Capability, ...] = ("abstract", "minutes", "keywords", "actions")


class PolishedText(Result):
    text: str = Field(min_length=1, description="完整润色正文，保留原文全部事实、数字、否定和条件")


class AbstractText(Result):
    text: str = Field(min_length=1, max_length=500, description="会议摘要，保留核心结论、条件与未决事项")


class ActionsText(Result):
    text: str = Field(
        min_length=1,
        max_length=1800,
        description="Markdown 工作计划；区分已定安排、建议和待确认事项，只填写原文明示的负责人和期限；没有安排时明确说明",
    )
