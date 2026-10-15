"""全文 HTTP 契约；只校验、提交任务及投影公开响应。"""

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from meetnote.domain.fulltext import DEFAULT_PIPELINE, Capability
from meetnote.sessions.fulltext import FulltextBusy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/fulltext", tags=["fulltext"])


class FulltextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    pipeline: list[Capability] | None = None


class MarkdownProduct(BaseModel):
    markdown: str


class FulltextResponse(BaseModel):
    id: str
    message: str | None = None
    polished_text: str | None = None
    final_abstract: str | None = None
    minutes: MarkdownProduct | None = None
    keywords: str | None = None
    next_actions: MarkdownProduct | None = None


def failure(request_id, pipeline, message):
    result = {"id": request_id, "message": message}
    for name in pipeline:
        if name == "keywords":
            result["keywords"] = None
        elif name in {"minutes", "actions"}:
            result["minutes" if name == "minutes" else "next_actions"] = {"markdown": message}
        else:
            result["polished_text" if name == "polish" else "final_abstract"] = message
    return result


@router.post("/generate", response_model=FulltextResponse, response_model_exclude_unset=True)
async def generate(body: FulltextRequest, request: Request):
    request_id, text = body.id.strip(), body.text.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id):
        raise HTTPException(400, "id must use letters, digits, '.', '_', ':' or '-'")
    if not text:
        raise HTTPException(400, "text must not be empty")
    if len(text) > 60_000:
        raise HTTPException(413, "text exceeds the current limit of 60000 characters")
    pipeline = tuple(body.pipeline) if body.pipeline is not None else DEFAULT_PIPELINE
    if not pipeline or len(set(pipeline)) != len(pipeline):
        raise HTTPException(400, "pipeline must not be empty or contain duplicates")
    # 与参考接口相同：明显无效输入不调用模型，提示放入所选产物字段。
    informative = sum(c.isalnum() for c in text)
    chinese = sum("\u4e00" <= c <= "\u9fff" for c in text)
    if len(text) < 80 or informative < 60 or chinese < 20 or informative / len(text) < 0.35:
        return JSONResponse(failure(request_id, pipeline, "输入内容不足或噪声过多，请提供完整会议转写。"))
    try:
        return await request.app.state.fulltext.run(
            request_id, text, pipeline, disconnected=request.is_disconnected
        )
    except FulltextBusy as exc:
        return JSONResponse(failure(request_id, pipeline, str(exc)), status_code=429)
    except Exception as exc:
        logger.warning("fulltext request failed id=%s category=%s", request_id, type(exc).__name__)
        return JSONResponse(
            failure(request_id, pipeline, "服务内部处理异常，请稍后重试。"), status_code=500
        )
