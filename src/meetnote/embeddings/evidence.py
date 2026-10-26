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


def review_page_schema(cap):
    return create_model(
        "EvidenceReviewPage",
        __base__=Result,
        text=(
            str,
            Field(
                min_length=1,
                max_length=cap,
                description=f"本页草稿审校后的完整替代；上限 {cap} 字符，尽量控制在 {max(1, cap * 3 // 4)} 字符内；只处理本页事项，保留条件和状态，压缩重复，用完整句子结束，不截断末句，不扩写整场计划",
            ),
        ),
    )


async def review_product(context, name, source, product):
    from meetnote.engine.fulltext import INCOMPLETE_PRODUCT, SCHEMAS, validate_product_text

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

    def fits(payload, schema):
        # 为实际纠正包预留输入空间；不能初次请求刚好塞满，随后无法纠正断尾。
        return context.fits(
            "fulltext_evidence", context.validation_input(payload, INCOMPLETE_PRODUCT), schema
        )

    payload = payload_for(evidence, product)
    if fits(payload, base_schema):
        result = await context.invoke(
            "fulltext_evidence", payload, base_schema, validate_product_text
        )
        return result.text

    # 按请求预算打包多条待审内容；同一窗口只发送一次，不能退化为每句一份完整计划。
    pages, current = [], []
    limit = base_schema.model_json_schema()["properties"]["text"]["maxLength"]
    packing_schema = review_page_schema(limit)
    for item in evidence:
        candidate = current + [item]
        draft = "\n".join(row["claim"] for row in candidate)
        if current and not fits(payload_for(candidate, draft), packing_schema):
            pages.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        pages.append(current)
    drafts = ["\n".join(row["claim"] for row in page) for page in pages]
    # 输出字符预算在分页前分配，含页间换行；不截断已生成正文来规避产物契约。
    remaining = limit - len(pages) + 1
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
        schema = review_page_schema(cap)
        result = await context.invoke(
            "fulltext_evidence", payload_for(page, draft), schema, validate_product_text
        )
        revised.append(result.text)
    return base_schema.model_validate({"text": "\n".join(revised)}).text
