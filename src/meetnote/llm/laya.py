"""Laya HTTP 传输层；不持有会议规则或业务 Prompt。"""

from time import perf_counter

import httpx

from meetnote.llm.client import ModelFailure, request_observer


class LayaClient:
    def __init__(self, url, timeout=30, transport=None):
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/", timeout=timeout, transport=transport
        )

    async def info(self):
        try:
            response = await self.client.get("v1/info")
            response.raise_for_status()
            info = response.json()
            if not info.get("ready") or not info.get("fingerprint"):
                raise ModelFailure("laya_not_ready")
            return info
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelFailure("laya_unavailable") from exc

    async def decide(self, task, state, questions, fingerprint):
        started = perf_counter()
        metadata = {"task": task, "backend": "laya", "usage": None, "error": None}
        try:
            response = await self.client.post(
                "v1/decisions", json={"state": state, "questions": questions}
            )
            if response.status_code != 200:
                raise ModelFailure(f"laya_http_{response.status_code}:{response.text[:200]}")
            data = response.json()
            if data.get("model", {}).get("fingerprint") != fingerprint:
                raise ModelFailure("laya_model_changed")
            answers = data["answers"]
            if set(answers) != set(questions):
                raise ModelFailure("laya_answer_ids_invalid")
            for key, question in questions.items():
                if (
                    answers[key].get("type") != "choice"
                    or answers[key].get("choice") not in question["criteria"]
                ):
                    raise ModelFailure("laya_choice_invalid")
            metadata.update(
                usage=data.get("usage"), timings=data.get("timings"), model_fingerprint=fingerprint
            )
            return answers
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            metadata["error"] = type(exc).__name__
            raise ModelFailure("laya_response_invalid") from exc
        except ModelFailure as exc:
            metadata["error"] = str(exc)
            raise
        finally:
            metadata["elapsed_ms"] = round((perf_counter() - started) * 1000)
            observer = request_observer.get()
            if observer:
                observer(metadata)

    async def close(self):
        await self.client.aclose()
