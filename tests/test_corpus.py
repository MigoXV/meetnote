import json

import pytest

from meetnote.corpus.reader import read_transcripts, validate_corpus


def write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")


def test_reader_never_loads_reference_annotations(tmp_path):
    sample = {
        "id": "demo_clean",
        "meeting_id": "demo",
        "variant": "clean",
        "title": "合成会议",
        "meeting_date": "2026-09-01",
        "segments": [{"id": "s1", "speaker": "甲", "text": "周五验收"}],
    }
    write_jsonl(tmp_path / "transcripts.jsonl", [sample])
    (tmp_path / "annotations.jsonl").write_text("故意无效的参考答案文件")
    result = read_transcripts(tmp_path)
    assert result[0].segments[0].text == "周五验收"


def test_validator_rejects_missing_variant(tmp_path):
    write_jsonl(
        tmp_path / "transcripts.jsonl",
        [
            {
                "id": "demo",
                "meeting_id": "demo",
                "variant": "clean",
                "title": "测试",
                "meeting_date": "2026-09-01",
                "segments": [{"id": "s1", "speaker": "甲", "text": "内容"}],
            }
        ],
    )
    with pytest.raises(ValueError, match="三版本不完整"):
        validate_corpus(tmp_path)


def test_formal_reader_keeps_metadata_without_reading_gold(tmp_path, monkeypatch):
    import hashlib
    import json
    from pathlib import Path

    from meetnote.corpus.reader import read_transcripts

    raw = "测试会议\n会议日期：2026年10月1日\n[u0001] 王宁：确认验收。\n"
    (tmp_path / "case.txt").write_text(raw)
    (tmp_path / "case.gold.json").write_text('{"secret": "不能输入模型"}')
    (tmp_path / "dataset_manifest.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "case",
                    "title": "测试会议",
                    "file": "case.txt",
                    "utterances": 1,
                    "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                }
            ]
        )
    )
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert not path.name.endswith(".gold.json")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    sample = read_transcripts(tmp_path)[0]
    assert sample.meeting_date == "2026-10-01"
    assert sample.segments[0].speaker == "王宁"
    assert sample.segments[0].text == "确认验收。"
