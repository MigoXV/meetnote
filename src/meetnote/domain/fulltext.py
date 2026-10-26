"""全文能力及模型产物；不依赖 HTTP 或会话。"""

from typing import Literal

from pydantic import Field

from meetnote.domain.results import Result

Capability = Literal["polish", "abstract", "minutes", "keywords", "actions"]
DEFAULT_PIPELINE: tuple[Capability, ...] = ("abstract", "minutes", "keywords", "actions")
ABSTRACT_CHARS = 500
ACTIONS_CHARS = 6000


class PolishedText(Result):
    text: str = Field(min_length=1, description="完整润色正文，保留原文全部事实、数字、否定和条件")


class AbstractText(Result):
    text: str = Field(
        min_length=1, max_length=ABSTRACT_CHARS,
        description=f"紧凑会议摘要，尽量控制在 {ABSTRACT_CHARS * 3 // 4} 字符内，用三到五个完整句子涵盖各主题核心结论、条件与未决事项；压缩枝节，不写长清单，为末句留出余量，不截断句子",
    )


class ActionsText(Result):
    text: str = Field(
        min_length=1,
        max_length=ACTIONS_CHARS,
        description=f"紧凑 Markdown 工作计划，尽量控制在 {ACTIONS_CHARS * 3 // 4} 字符内；每项另起一行，用完整句子结束，压缩重复并预留末句余量；区分已定安排、建议和待确认事项，只填写原文明示的负责人和期限；没有安排时明确说明",
    )
