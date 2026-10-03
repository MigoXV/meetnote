"""真实双模型联调；会议与事件仅在内存中处理。"""

import asyncio
import json
from time import monotonic

import httpx

from meetnote.evaluation.short import run_short

CASES = ["owners", "explicit_today", "proposal", "amount", "question", "venue_cancel"]


def check_snapshot(snapshot):
    errors = []
    if snapshot["status"] != "completed":
        errors.append("session_not_completed")
    if snapshot["accepted_count"] != snapshot["processed_count"] or snapshot["remaining_chars"]:
        errors.append("input_not_fully_consumed")
    if not snapshot["minutes"] or not snapshot["facts"]:
        errors.append("missing_results")
    if snapshot["error"] or snapshot["warnings"]:
        errors.append("error_or_degradation")
    stats = snapshot["stats"]
    if not stats.get("responses_requests") or not stats.get("laya_requests"):
        errors.append("both_models_not_observed")
    if stats.get("decision_fallbacks", 0):
        errors.append("unexpected_fallback")
    source_ids = {s["id"] for s in snapshot["sources"]}
    if any(not set(f["source_ids"]) <= source_ids for f in snapshot["facts"]):
        errors.append("invalid_fact_reference")
    return errors


async def run_integration(settings, url, emit=print):
    if (
        settings.provider != "responses"
        or settings.decision_backend != "laya"
        or settings.laya_fallback
    ):
        raise ValueError("联调要求真实 Responses + laya，且禁用回退")
    failures = 0

    def report(line):
        nonlocal failures
        row = json.loads(line)
        stats = row["stats"]
        if not stats.get("responses_requests") or not stats.get("laya_requests"):
            row["errors"].append("both_models_not_observed")
        if stats.get("decision_fallbacks", 0):
            row["errors"].append("unexpected_fallback")
        row["passed"] = not row["errors"]
        failures += not row["passed"]
        emit(
            json.dumps(
                {k: row[k] for k in ["case", "passed", "errors", "stats"]}, ensure_ascii=False
            )
        )

    await run_short(settings, CASES, report)
    if failures:
        return failures
    return await run_http(url, emit)


async def run_http(url, emit=print):
    failures = 0
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        info = (await client.get("/api/v1/info")).raise_for_status().json()
        assert info["provider"] == "responses" and info["decision_backend"] == "laya"
        assert not info["decision_fallback"]
        assert info["decision_model"]["precision"] == "bf16"
        sample = (await client.get("/api/v1/samples/product_clean")).raise_for_status().json()
        session = (
            (
                await client.post(
                    "/api/v1/sessions",
                    json={"title": sample["title"], "meeting_date": sample["meeting_date"]},
                )
            )
            .raise_for_status()
            .json()
        )
        sid = session["id"]
        event_types = set()

        async def collect():
            async with client.stream(
                "GET", f"/api/v1/sessions/{sid}/events", timeout=httpx.Timeout(30, read=60)
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        event_types.add(json.loads(line[5:].strip())["type"])

        events = asyncio.create_task(collect())
        try:
            for segment in sample["segments"]:
                (
                    await client.post(
                        f"/api/v1/sessions/{sid}/chunks",
                        json={
                            "chunk_id": segment["id"],
                            "text": segment["text"],
                            "speaker": segment.get("speaker"),
                        },
                    )
                ).raise_for_status()
            (await client.post(f"/api/v1/sessions/{sid}/finalize")).raise_for_status()
            deadline = monotonic() + 900
            while monotonic() < deadline:
                snapshot = (await client.get(f"/api/v1/sessions/{sid}")).raise_for_status().json()
                if snapshot["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(1)
            await asyncio.wait_for(events, 30)
            errors = check_snapshot(snapshot)
            if not event_types:
                errors.append("missing_sse_events")
            if snapshot["accepted_count"] != len(sample["segments"]):
                errors.append("missing_segments")
            emit(
                json.dumps(
                    {
                        "case": "product_clean",
                        "passed": not errors,
                        "errors": errors,
                        "session_error": snapshot["error"],
                        "accepted": snapshot["accepted_count"],
                        "processed": snapshot["processed_count"],
                        "remaining_chars": snapshot["remaining_chars"],
                        "stats": snapshot["stats"],
                        "event_types": sorted(event_types),
                    },
                    ensure_ascii=False,
                )
            )
            failures += bool(errors)
        finally:
            events.cancel()
            await asyncio.gather(events, return_exceptions=True)
            (await client.delete(f"/api/v1/sessions/{sid}")).raise_for_status()
        assert (await client.get("/health/live")).status_code == 200
    return failures
