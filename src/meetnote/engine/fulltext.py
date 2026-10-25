"""MeetingEngine 的全文能力；每项独立读取原文，沿用主模型任务预算。"""

import logging

from meetnote.domain.fulltext import AbstractText, ActionsText, PolishedText
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.llm.client import ModelFailure

logger = logging.getLogger(__name__)
SCHEMAS = {"polish": PolishedText, "abstract": AbstractText, "actions": ActionsText}
FAILURE_MESSAGE = "未能生成有效内容，请检查模型服务或稍后重试。"
INCOMPLETE_PRODUCT = "product_incomplete_at_character_limit"


def validate_product_text(result):
    text = result.text.rstrip()
    if not text:
        raise ModelFailure("capability_output_empty")
    limit = type(result).model_json_schema()["properties"]["text"].get("maxLength")
    if limit and limit >= 32 and len(text) >= limit - 2 and text[-1] not in "。！？.!?）)】]”\"』」":
        raise ModelFailure(INCOMPLETE_PRODUCT)


def chunks(text):
    for seq, start in enumerate(range(0, len(text), 16000), 1):
        part = text[start : start + 16000]
        yield TranscriptChunk(
            text=part, chunk_id=f"fulltext-{seq}", seq=seq, start=start, end=start + len(part)
        )


def windows(context, task, text, schema, phase):
    """按实际预算拆分，覆盖所有字符；优先使用句末或换行。"""
    limit = (
        min(1024, max(1, context.settings.max_output_tokens // 2))
        if task == "fulltext_polish"
        else max(1, context.settings.input_token_budget // 2)
    )
    while text:
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            part = text[:mid]
            if context.budget.count(part) <= limit and context.fits(
                task, {"text": part, "phase": phase}, schema
            ):
                low = mid
            else:
                high = mid - 1
        if not low:
            raise ModelFailure("input_budget_exceeded")
        if low < len(text):
            boundary = max(text.rfind(c, low // 2, low) for c in "。！？；\n")
            if boundary >= 0:
                low = boundary + 1
        yield text[:low]
        text = text[low:]


async def text_product(engine, name, text):
    context = engine.context
    task, schema = "fulltext_" + name, SCHEMAS[name]

    async def generate(value, phase):
        result = await context.invoke(
            task, {"text": value, "phase": phase}, schema, validate_product_text
        )
        return result.text

    async def finish(value):
        if name in {"abstract", "actions"}:
            from meetnote.embeddings.evidence import review_product

            return await review_product(context, name, text, value)
        return value

    parts = [
        await generate(part, "source")
        for part in windows(context, task, text, schema, "source")
        if part.strip()
    ]
    if name == "polish":
        return "\n".join(parts)
    # 摘要与工作计划逐层汇总；每层完整读取上一层，禁止截断后假装全文成功。
    for _ in range(8):
        if len(parts) <= 1:
            return await finish(parts[0]) if parts else ""
        merged = "\n\n".join(parts)
        parts = [
            await generate(part, "merge")
            for part in windows(context, task, merged, schema, "merge")
            if part.strip()
        ]
    raise ModelFailure("fulltext_merge_did_not_converge")


async def generate_fulltext(engine, request_id, text, pipeline):
    output = {"id": request_id}
    issues = []
    for name in pipeline:
        try:
            if name == "keywords":
                output["keywords"] = None
                continue
            if name == "minutes":

                async def inputs():
                    for chunk in chunks(text):
                        yield chunk

                # 全文接口只交付最终产物；仍由主引擎消费、核验及最终刷新。
                await engine.run(inputs(), rolling_minutes=False)
                draft = engine.knowledge.draft
                if not draft or engine.stream.consumed != len(text):
                    raise ModelFailure("capability_output_empty")
                product = draft.markdown
            else:
                product = await text_product(engine, name, text)
            if not product.strip():
                raise ModelFailure("capability_output_empty")
        except Exception as exc:
            # 日志只记录定位信息；原文、响应正文和异常内容不落盘。
            logger.warning(
                "fulltext capability failed id=%s capability=%s category=%s",
                request_id,
                name,
                type(exc).__name__,
            )
            product = FAILURE_MESSAGE
            if product not in issues:
                issues.append(product)
        if name == "minutes":
            output["minutes"] = {"markdown": product}
        elif name == "actions":
            output["next_actions"] = {"markdown": product}
        else:
            output["polished_text" if name == "polish" else "final_abstract"] = product
    if issues:
        output["message"] = "；".join(issues)
    return output
