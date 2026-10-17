import json

from meetnote.config import Settings
from meetnote.evaluation import strategy


async def test_strategy_alternates_without_replay_and_reports_complete_pairs(monkeypatch, tmp_path):
    calls = []

    async def info(_):
        return {"precision": "bf16", "backend": "vllm"}

    async def formal(settings, directory, names, emit, *, stream_input):
        calls.append(
            (
                stream_input,
                settings.extraction_window,
                settings.local_extraction_retry,
                settings.parallel_chapters,
                settings.model,
            )
        )
        emit(
            json.dumps(
                {
                    "record": "result",
                    "error": None,
                    "remaining": 0,
                    "elapsed_seconds": 7 if stream_input else 10,
                }
            )
        )
        return 0

    monkeypatch.setattr(strategy, "model_info", info)
    monkeypatch.setattr(strategy, "run_formal", formal)
    result = await strategy.compare_strategy(
        Settings(_env_file=None), tmp_path, "sample", lambda _: None
    )
    assert [c[0] for c in calls] == [False, True, True, False, False, True]
    assert all(
        c[1:] == ((2, True, True, "spark25") if c[0] else (1, False, False, "spark25"))
        for c in calls
    )
    assert result["complete"]
    assert result["reduction_percent"] == 30


async def test_failed_runs_are_not_reported_as_speedups(monkeypatch, tmp_path):
    async def info(_):
        return {}

    async def formal(settings, directory, names, emit, *, stream_input):
        emit(
            json.dumps(
                {
                    "record": "result",
                    "error": "failed" if stream_input else None,
                    "remaining": 1 if stream_input else 0,
                    "elapsed_seconds": 1,
                }
            )
        )
        return int(stream_input)

    monkeypatch.setattr(strategy, "model_info", info)
    monkeypatch.setattr(strategy, "run_formal", formal)
    result = await strategy.compare_strategy(
        Settings(_env_file=None), tmp_path, "sample", lambda _: None
    )
    assert not result["complete"]
    assert "speedup" not in result
    assert result["failures"] == 3
