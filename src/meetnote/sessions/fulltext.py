"""同步全文请求的有界调度与生命周期；不保留会议结果。"""

import asyncio

from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine


class FulltextBusy(RuntimeError):
    pass


class FulltextService:
    def __init__(self, settings, model):
        self.settings, self.model = settings, model
        self.capacity = asyncio.Semaphore(settings.fulltext_max_active)
        self.tasks = set()
        self.closed = False

    async def run(self, request_id, text, pipeline, disconnected=None):
        if self.closed:
            raise FulltextBusy("服务正在关闭，请稍后重试。")
        if len(self.tasks) >= self.settings.fulltext_max_active + self.settings.fulltext_max_waiting:
            raise FulltextBusy("服务繁忙，请稍后重试。")

        async def execute():
            try:
                await asyncio.wait_for(
                    self.capacity.acquire(), self.settings.fulltext_queue_timeout
                )
            except asyncio.TimeoutError as exc:
                raise FulltextBusy("排队等待超时，请稍后重试。") from exc
            try:
                context = Context(self.settings, self.model, lambda *_: None)
                engine = MeetingEngine(context)
                return await engine.generate_fulltext(request_id, text, pipeline)
            finally:
                self.capacity.release()

        async def watch():
            while True:
                if await disconnected():
                    return
                await asyncio.sleep(0.25)

        job = asyncio.create_task(execute())
        self.tasks.add(job)
        watcher = asyncio.create_task(watch()) if disconnected else None
        try:
            if watcher:
                done, _ = await asyncio.wait({job, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if job not in done:
                    watcher.result()
                    raise asyncio.CancelledError()
            return await job
        finally:
            job.cancel()
            if watcher:
                watcher.cancel()
            await asyncio.gather(job, *([watcher] if watcher else []), return_exceptions=True)
            self.tasks.discard(job)

    async def close(self):
        self.closed = True
        jobs = list(self.tasks)
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
