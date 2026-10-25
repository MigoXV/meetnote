"""会中正文任务只读证据快照；正式结果由引擎唯一消费者提交。"""

import asyncio
import re
from copy import copy, deepcopy

from pydantic_core import from_json

SECTIONS = ("overview", "conclusions", "discussion", "actions", "open_questions")


def text_blocks(raw, complete_only=False):
    try:
        data = from_json(raw, allow_partial=True if complete_only else "trailing-strings")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    blocks = {}
    for section in SECTIONS:
        values = [data.get(section)] if section == "overview" else data.get(section, [])
        if not isinstance(values, list):
            continue
        for index, paragraph in enumerate(values):
            text = paragraph.get("text") if isinstance(paragraph, dict) else None
            if isinstance(text, str) and not re.search(
                r"f\d{6}|s\d+\.\d+|【(?:已确认|待确认)】|完整事项清单|原文索引", text
            ):
                blocks[f"{section}.{index}"] = text
    return blocks


def narrative_blocks(draft):
    if not draft or not draft.narrative:
        return {}
    return text_blocks(draft.narrative.model_dump_json())


class RollingMinutes:
    def __init__(self, engine):
        self.engine = engine
        self.task = None
        self.covered = -1
        self.started = float("-inf")
        self.round = 0
        self.sequence = 0
        self.preview = None
        self.target = 0
        self.reviewed = -1
        self.reviewing = False

    @property
    def dirty(self):
        return self.covered != self.engine.stream.consumed

    def emit(self, kind, **data):
        self.sequence += 1
        payload = {
            "round": self.round,
            "sequence": self.sequence,
            "covered_chars": self.target,
            **data,
        }
        self.engine.context.emit(kind, payload)
        return payload

    def launch(self, review=False):
        engine = self.engine
        snapshot = copy(engine)
        snapshot.state = deepcopy(engine.state)
        snapshot.state["stream"].sources = [
            s for s in snapshot.stream.sources if s.end <= snapshot.stream.consumed
        ]
        snapshot.state["stream"].cursor = snapshot.stream.consumed
        snapshot.state["stream"].pending = []
        snapshot.source_index = {s.id: s for s in snapshot.stream.sources}
        self.target = engine.stream.consumed
        self.reviewing = review
        self.round += 1
        self.started = engine.clock()
        base = narrative_blocks(engine.knowledge.draft)
        raw, phase = "", None

        def output(task, delta):
            nonlocal raw, phase, base
            if delta is None:
                if phase is not None and phase != task and self.preview:
                    base = dict(self.preview["blocks"])
                phase, raw = task, ""
                self.preview = self.emit("minutes.started", blocks=base, baseline=base)
                return
            raw += delta
            # 已有草稿在替代段落的 text 字段完整前继续显示，避免逐字回退再增长。
            partial = text_blocks(raw, complete_only=bool(base))
            blocks = {
                **base,
                **{
                    key: text
                    for key, text in partial.items()
                    if not base.get(key, "").startswith(text)
                },
            }
            if self.preview is None or self.preview.get("blocks") != blocks:
                self.preview = self.emit("minutes.delta", blocks=blocks, baseline=base)

        self.preview = self.emit("minutes.started", blocks=base, baseline=base)
        self.task = asyncio.create_task(
            snapshot.compose(on_output=output, commit=False, review=review)
        )

    async def tick(self, force=False):
        if self.task and self.task.done():
            task, self.task = self.task, None
            try:
                draft = task.result()
            except Exception as exc:
                self.preview = None
                self.emit("minutes.aborted", error=str(exc))
                if force:
                    raise
            else:
                self.engine.knowledge.draft = draft
                self.covered = self.target
                if self.reviewing:
                    self.reviewed = self.target
                self.preview = None
                self.emit("minutes.updated", minutes=draft.model_dump(mode="json"))
        if (
            self.task is None
            and self.dirty
            and (
                force
                or self.engine.clock() - self.started
                >= self.engine.context.settings.minutes_interval
            )
            and (self.engine.knowledge.facts or force)
        ):
            self.launch(review=force)

    async def finish(self):
        while self.dirty:
            await self.tick(force=True)
            if self.task:
                await asyncio.wait({self.task})
        # 会中每轮只生成草稿；最终覆盖全部输入后审校一次，审校过程不覆盖正文预览。
        draft = self.engine.knowledge.draft
        if self.reviewed != self.covered:
            draft = await self.engine.review_minutes(draft)
            self.engine.knowledge.draft = draft
            self.reviewed = self.covered
            self.emit("minutes.updated", minutes=draft.model_dump(mode="json"))
        self.engine.context.emit(
            "minutes.completed", draft.model_dump(mode="json")
        )

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.preview = None
