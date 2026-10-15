"""仅验证接线的可重复替身，不能用来测量模型事实准确率。"""


class MockClient:
    async def generate(self, task, instructions, context, schema):
        if task.startswith("fulltext_"):
            if task == "fulltext_polish":
                return schema.model_validate({"text": "[模拟] " + context["text"]})
            limit = 500 if task == "fulltext_abstract" else 1800
            return schema.model_validate({"text": "[模拟] " + context["text"][:limit - 5]})
        if task == "extract":
            data = {
                "facts": [
                    {
                        "text": "[模拟] " + s["text"],
                        "kind": "fact",
                        "topic": "模拟会议",
                        "subject": "模拟",
                        "status": "confirmed",
                        "source_ids": [s["id"]],
                    }
                    for s in context["sources"]
                ],
                "ignored": [],
            }
        elif task == "reconcile":
            data = {"relations": []}
        elif task == "minutes_review":
            data = context["draft"]
        elif task == "minutes":
            sources = {s["id"]: s for s in context["sources"]}
            items = [
                {
                    "text": "；".join(
                        (sources[sid].get("speaker") or "") + sources[sid]["text"]
                        for sid in f["source_ids"]
                        if sid in sources
                    )
                    or "模拟纪要",
                    "fact_ids": [f["id"]],
                }
                for f in context["facts"]
            ]
            data = {
                "overview": {
                    "text": "；".join(i["text"] for i in items)[:100],
                    "fact_ids": [f["id"] for f in context["facts"]],
                },
                "conclusions": [],
                "discussion": [],
                "actions": [],
                "open_questions": [],
            }
        elif task == "chapter":
            data = {
                "fact_ids": list(
                    dict.fromkeys(fid for item in context["items"] for fid in item["fact_ids"])
                )[: context.get("selection_limit", 6)]
            }
        elif task == "fields":
            data = {
                f["id"]: {"owner_supported": True, "due_supported": True, "reason": "模拟字段核对"}
                for f in context["claims"]
            }
        else:
            raise ValueError(task)
        return mock_result(task, context, schema, data)

    async def close(self):
        pass


def mock_result(task, context, schema, data):
    if task == "extract" and "facts" not in schema.model_fields:
        from meetnote.domain.results import CATEGORIES

        categories = {pair: name for name, pair in CATEGORIES.items()}
        ids = [s["id"] for s in context["sources"]]
        records = {sid: {"facts": [], "ignored_reason": ""} for sid in ids}
        for fact in data["facts"]:
            fact = dict(fact)
            fact["category"] = categories[(fact.pop("kind"), fact.pop("status"))]
            sid = next((sid for sid in fact["source_ids"] if sid in records), ids[0])
            records[sid]["facts"].append(fact)
        for ignored in data.get("ignored", []):
            records[ignored["source_id"]]["ignored_reason"] = ignored["reason"]
        data = records
    if task == "reconcile" and "relations" not in schema.model_fields:
        records = {
            key: {"kind": "none", "evidence_id": None, "reason": "无修订关系"}
            for key in context["pairs"]
        }
        for relation in data["relations"]:
            for key, pair in context["pairs"].items():
                if (pair["new_id"], pair["old_id"]) == (relation["new_id"], relation["old_id"]):
                    records[key] = {
                        "kind": relation["kind"],
                        "evidence_id": pair["evidence_ids"][0],
                        "reason": relation["reason"],
                    }
        data = records
    return schema.model_validate(data)
