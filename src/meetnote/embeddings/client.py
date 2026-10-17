"""轻量 gRPC 客户端与每会议有界内存缓存；不加载 Torch 或模型权重。"""

from array import array
from collections import OrderedDict
from math import isfinite, sqrt
from time import perf_counter

import grpc

from meetnote.embeddings.proto import embedding_pb2 as pb
from meetnote.embeddings.proto import embedding_pb2_grpc as rpc
from meetnote.llm.client import ModelFailure


class GrpcEncoder:
    def __init__(self, settings):
        self.settings = settings

    async def ready(self):
        async with grpc.aio.insecure_channel(self.settings.embedding_target) as channel:
            try:
                result = await rpc.EmbeddingServiceStub(channel).Metadata(
                    pb.MetadataRequest(), timeout=min(5, self.settings.embedding_timeout)
                )
            except grpc.aio.AioRpcError as exc:
                raise ModelFailure("embedding_rpc_" + exc.code().name.lower()) from exc
        if not result.ready or result.dimensions != 1024:
            raise ModelFailure("embedding_not_ready")
        return {"ready": True, "model_id": result.model_id, "generation": result.generation}

    async def encode(self, texts):
        config = self.settings
        # 每次 RPC 明确释放 channel；取消不影响其他会议，也不留下跨事件循环连接。
        async with grpc.aio.insecure_channel(
            config.embedding_target,
            options=[("grpc.max_receive_message_length", 4 * 1024 * 1024)],
        ) as channel:
            stub = rpc.EmbeddingServiceStub(channel)
            try:
                result = await stub.Encode(
                    pb.EncodeRequest(
                        texts=texts, max_tokens=config.embedding_max_tokens, truncate=True
                    ),
                    timeout=config.embedding_timeout,
                )
            except grpc.aio.AioRpcError as exc:
                raise ModelFailure("embedding_rpc_" + exc.code().name.lower()) from exc
        if not result.model_id or len(result.embeddings) != len(texts):
            raise ModelFailure("embedding_response_invalid")
        vectors = []
        for row in result.embeddings:
            values = array("f", row.values)
            if len(values) != 1024 or not all(isfinite(x) for x in values):
                raise ModelFailure("embedding_vector_invalid")
            norm = sqrt(sum(x * x for x in values))
            if abs(norm - 1) > 0.01:
                raise ModelFailure("embedding_not_normalized")
            vectors.append(values)
        return result.model_id, vectors, sum(row.truncated for row in result.embeddings)


class Vectors:
    def __init__(self, settings, stats, emit, encoder=None):
        self.settings, self.stats, self.emit = settings, stats, emit
        self.encoder = encoder or GrpcEncoder(settings)
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.model_id = None

    async def encode(self, texts):
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        fresh = {}
        for start in range(0, len(missing), 64):
            batch = missing[start : start + 64]
            started = perf_counter()
            model_id, vectors, truncated = await self.encoder.encode(batch)
            if self.model_id is not None and model_id != self.model_id:
                raise ModelFailure("embedding_model_changed_during_meeting")
            self.model_id = model_id
            elapsed = round((perf_counter() - started) * 1000, 3)
            for key, value in (
                ("embedding_requests", 1),
                ("embedding_texts", len(batch)),
                ("embedding_elapsed_ms", elapsed),
                ("embedding_truncated", truncated),
            ):
                self.stats[key] = self.stats.get(key, 0) + value
            self.emit(
                "embedding.completed",
                {
                    "texts": len(batch),
                    "elapsed_ms": elapsed,
                    "truncated": truncated,
                    "model_id": model_id,
                },
            )
            fresh.update(zip(batch, vectors))
        # 先收集输出，防止单次输入比缓存大时 LRU 淘汰导致缺项。
        result = [fresh[text] if text in fresh else self.cache[text][0] for text in texts]
        for text in texts:
            if text in self.cache:
                self.cache.move_to_end(text)
        for text, vector in fresh.items():
            size = len(text.encode("utf-8")) + len(vector) * vector.itemsize
            if (
                not self.settings.embedding_cache_entries
                or size > self.settings.embedding_cache_bytes
            ):
                continue
            while self.cache and (
                len(self.cache) >= self.settings.embedding_cache_entries
                or self.cache_bytes + size > self.settings.embedding_cache_bytes
            ):
                _, (_, evicted) = self.cache.popitem(last=False)
                self.cache_bytes -= evicted
            self.cache[text] = vector, size
            self.cache_bytes += size
        return result


async def recall(vectors, queries, documents, top_k, min_score, allowed=None):
    """只返回检索下标和分数；不作任何事实修订或业务门禁。"""
    if not queries or not documents:
        return [[] for _ in queries]
    encoded = await vectors.encode([*queries, *documents])
    left, right = encoded[: len(queries)], encoded[len(queries) :]
    result = []
    for index, query in enumerate(left):
        candidates = range(len(right)) if allowed is None else allowed[index]
        scores = ((i, sum(a * b for a, b in zip(query, right[i]))) for i in candidates)
        ranked = sorted(scores, key=lambda row: (-row[1], row[0]))[:top_k]
        result.append([(i, score) for i, score in ranked if score >= min_score])
    return result
