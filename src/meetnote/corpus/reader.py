"""应用仅读取转写文件，参考标注只由校验命令访问。"""

import hashlib
import json
from pathlib import Path

from meetnote.domain.transcripts import Transcript


def read_transcripts(directory: Path) -> list[Transcript]:
    path = directory / "transcripts.jsonl"
    if not path.exists():
        return []
    return [
        Transcript.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_corpus(directory: Path) -> dict:
    transcripts = read_transcripts(directory)
    if not transcripts:
        raise ValueError("语料不存在或为空")
    by_id = {item.id: item for item in transcripts}
    if len(by_id) != len(transcripts):
        raise ValueError("转写 ID 重复")
    meetings: dict[str, set[str]] = {}
    for item in transcripts:
        variants = meetings.setdefault(item.meeting_id, set())
        if item.variant in variants:
            raise ValueError("同会议版本重复")
        variants.add(item.variant)
        if len({s.id for s in item.segments}) != len(item.segments):
            raise ValueError("片段 ID 重复")
    if any(v != {"clean", "colloquial", "asr_noisy"} for v in meetings.values()):
        raise ValueError("会议三版本不完整")
    annotations = [
        json.loads(line)
        for line in (directory / "annotations.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len(annotations) != len(transcripts) or {a["transcript_id"] for a in annotations} != set(
        by_id
    ):
        raise ValueError("标注与转写不一一对应")
    for annotation in annotations:
        sample = by_id[annotation["transcript_id"]]
        segments = {s.id: s for s in sample.segments}
        for category in ("facts", "decisions", "actions", "open_questions"):
            for record in annotation.get(category, []):
                if not record["segment_ids"] or not set(record["segment_ids"]) <= segments.keys():
                    raise ValueError("无效证据引用")
        errors = annotation.get("errors", [])
        if sample.variant != "asr_noisy" and errors:
            raise ValueError("非噪声版本不应有 ASR 错误标注")
        if sample.variant == "asr_noisy":
            original = next(
                t
                for t in transcripts
                if t.meeting_id == sample.meeting_id and t.variant == "colloquial"
            )
            source = {s.id: s for s in original.segments}
            if len({e["category"] for e in errors}) < 4:
                raise ValueError("噪声类别不足四种")
            for error in errors:
                segment_id = error["segment_id"]
                field = error.get("field", "text")
                if field not in {"text", "speaker"}:
                    raise ValueError("错误标注 field 只能是 text 或 speaker")
                if (
                    not error["original"]
                    or not error["observed"]
                    or error["original"] not in getattr(source[segment_id], field)
                    or error["observed"] not in getattr(segments[segment_id], field)
                ):
                    raise ValueError(f"错误跨度与转写不符: {sample.id}/{segment_id}")
    return {
        "meetings": len(meetings),
        "transcripts": len(transcripts),
        "characters": {t.id: sum(len(s.text) for s in t.segments) for t in transcripts},
        "sha256": hashlib.sha256((directory / "transcripts.jsonl").read_bytes()).hexdigest(),
    }
