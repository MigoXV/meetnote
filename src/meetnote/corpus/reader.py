"""应用仅读取转写文件，参考标注只由校验命令访问。"""

import hashlib
import json
import re
from pathlib import Path

from meetnote.domain.transcripts import Transcript


def read_transcripts(directory: Path) -> list[Transcript]:
    path = directory / "transcripts.jsonl"
    if not path.exists():
        return read_formal(directory)
    return [
        Transcript.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_corpus(directory: Path) -> dict:
    if (directory / "dataset_manifest.json").exists():
        samples = {s.id: s for s in read_formal(directory)}
        manifest = json.loads((directory / "dataset_manifest.json").read_text())
        checks = 0
        for item in manifest:
            sample = samples[item["case_id"]]
            ids = {s.id for s in sample.segments}
            gold = json.loads((directory / item["file"]).with_suffix(".gold.json").read_text())
            if len(gold["checks"]) != item["checks"]:
                raise ValueError("标注数量不符")
            for check in gold["checks"]:
                if not check["source_ids"] or not set(check["source_ids"]) <= ids:
                    raise ValueError("无效证据引用")
                checks += 1
        return {"transcripts": len(samples), "checks": checks, "format": "formal"}
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


def read_formal(directory: Path) -> list[Transcript]:
    """仅读取 manifest 指向的原文；不读取 gold 或噪声标注。"""
    manifest = directory / "dataset_manifest.json"
    if not manifest.is_file():
        return []
    output = []
    for item in json.loads(manifest.read_text(encoding="utf-8")):
        path = (directory / item["file"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("语料路径越界")
        raw = path.read_text(encoding="utf-8")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("语料校验失败: " + item["case_id"])
        segments = []
        for match in re.finditer(
            r"^\[(u\d+)\]\s*([^：:\n]+)[：:]\s*(.*?)(?=^\[u\d+\]|\Z)", raw, re.M | re.S
        ):
            segments.append({"id": match[1], "speaker": match[2].strip(), "text": match[3].strip()})
        if len(segments) != item["utterances"]:
            raise ValueError("语料片段数不符: " + item["case_id"])
        date = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", raw.split("[u", 1)[0])
        output.append(
            Transcript(
                id=item["case_id"],
                meeting_id=item["case_id"],
                variant="formal",
                title=item["title"],
                meeting_date=f"{date[1]}-{int(date[2]):02d}-{int(date[3]):02d}" if date else "",
                segments=segments,
            )
        )
    return output
