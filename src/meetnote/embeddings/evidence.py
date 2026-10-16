"""业务证据召回与审校；向量服务只做编码，业务 Prompt 留在 prompts。"""

import re

from pydantic import Field, create_model

from meetnote.domain.results import Result
from meetnote.embeddings.client import recall
from meetnote.llm.client import ModelFailure


def source_windows(text, target=240):
    windows, start = [], 0
    while start < len(text):
        end = min(start + target, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, start + target // 2, end) for mark in "。！？；\n")
            if boundary >= 0:
                end = boundary + 1
        windows.append(
            {"id": f"w{len(windows)}", "start": start, "end": end, "text": text[start:end]}
        )
        start = end
    return windows


async def review_product(context, name, source, product):
    if context.vectors is None:
        return product
    from meetnote.engine.fulltext import SCHEMAS

    base_schema = SCHEMAS[name]
    windows = source_windows(source)
    claims = [part.strip() for part in re.split(r"(?<=[。！？；])|\n+", product) if part.strip()]
    matches = await recall(context.vectors, claims, [w["text"] for w in windows], 3, -1)
    evidence = [
        {"claim": claim, "sources": [windows[i] for i, _ in rows]}
        for claim, rows in zip(claims, matches)
    ]

    def payload_for(items, draft):
        sources = {s["id"]: s for item in items for s in item["sources"]}
        return {
            "capability": name,
            "draft": draft,
            "evidence": [
                {"claim": item["claim"], "source_ids": [s["id"] for s in item["sources"]]}
                for item in items
            ],
            "sources": list(sources.values()),
        }

    payload = payload_for(evidence, product)
    if context.fits("fulltext_evidence", payload, base_schema):
        result = await context.invoke("fulltext_evidence", payload, base_schema)
        if not result.text.strip():
            raise ModelFailure("embedding_evidence_review_empty")
        return result.text

    # 按请求预算打包多条待审内容；同一窗口只发送一次，不能退化为每句一份完整计划。
    pages, current = [], []
    for item in evidence:
        candidate = current + [item]
        draft = "\n".join(row["claim"] for row in candidate)
        if current and not context.fits(
            "fulltext_evidence", payload_for(candidate, draft), base_schema
        ):
            pages.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        pages.append(current)
    drafts = ["\n".join(row["claim"] for row in page) for page in pages]
    # 输出字符预算在分页前分配，含页间换行；不截断已生成正文来规避产物契约。
    remaining = base_schema.model_json_schema()["properties"]["text"]["maxLength"] - len(pages) + 1
    weight = sum(len(draft) for draft in drafts)
    if remaining < len(pages) or not weight:
        raise ModelFailure("evidence_review_output_budget_exceeded")
    revised = []
    for index, (page, draft) in enumerate(zip(pages, drafts)):
        cap = (
            remaining
            if index == len(pages) - 1
            else max(1, min(remaining - (len(pages) - index - 1), remaining * len(draft) // weight))
        )
        remaining -= cap
        weight -= len(draft)
        schema = create_model(
            "EvidenceReviewPage",
            __base__=Result,
            text=(
                str,
                Field(
                    min_length=1,
                    max_length=cap,
                    description="本页草稿审校后的完整替代；只处理本页事项，保留条件和状态，不扩写整场计划",
                ),
            ),
        )
        result = await context.invoke("fulltext_evidence", payload_for(page, draft), schema)
        if not result.text.strip():
            raise ModelFailure("embedding_evidence_review_empty")
        revised.append(result.text)
    return base_schema.model_validate({"text": "\n".join(revised)}).text
