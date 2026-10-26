import httpx
import pytest

from scripts.replay_meeting_stream import read_snapshot


async def test_expired_replay_uses_completed_snapshot_kept_in_memory():
    snapshot = {"status": "completed", "remaining_chars": 0, "stats": {"requests": 12}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as client:
        result = await read_snapshot(client, "http://summary/session", snapshot)
    assert result is snapshot


@pytest.mark.parametrize("status,cached", [
    (404, None), (404, {"status": "running"}), (503, {"status": "completed"}),
])
async def test_replay_never_masks_unfinished_session_or_service_failure(status, cached):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await read_snapshot(client, "http://summary/session", cached)
