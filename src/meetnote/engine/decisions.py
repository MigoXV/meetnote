"""将字段与事实关系任务转换为有限选项；不旁路 MeetingEngine。"""

from importlib.resources import files

from meetnote.llm.client import ModelFailure, request_observer


class DecisionTasks:
    def __init__(self, client, fingerprint):
        self.client, self.fingerprint = client, fingerprint

    async def choose(self, task, state, options, prompt):
        if not 2 <= len(options) <= 10:
            raise ModelFailure("laya_options_exceeded")
        instruction = files("meetnote.prompts").joinpath(prompt + ".txt").read_text().strip()
        answers = await self.client.decide(
            task,
            state,
            {"answer": {"type": "choice", "instructions": instruction, "criteria": options}},
            self.fingerprint,
        )
        return answers["answer"]["choice"]

    async def fields(self, payload):
        output = {}
        for claim in payload["claims"]:
            item = {
                "owner_supported": False,
                "due_supported": False,
                "reason": "Laya 有限选项核验；说明由程序生成",
            }
            for name, label in (("owner", "负责人"), ("due", "期限")):
                if not claim.get(name):
                    continue
                ids = claim.get(name + "_source_ids", [])
                sources = [s for s in payload["sources"] if s["id"] in ids]
                state = {
                    "事项": claim["text"],
                    "字段": label,
                    "候选值": claim[name],
                    "原文": sources,
                }
                choice = await self.choose(
                    "fields",
                    state,
                    {"supported": "支持", "unsupported": "不支持", "uncertain": "无法判断"},
                    "laya_fields",
                )
                if choice == "uncertain":
                    raise ModelFailure("laya_uncertain_field")
                item[name + "_supported"] = choice == "supported"
            output[claim["id"]] = item
        return output

    async def reconcile(self, payload, schema):
        output = {}
        labels = {
            "none": "无关系",
            "supersedes": "新决定替代旧安排",
            "retracts": "撤销旧安排",
            "resolves": "回答或解决旧问题",
            "conflicts": "两条陈述冲突",
        }
        document = schema.model_json_schema()
        for key, pair in payload["pairs"].items():
            # 允许关系由引擎生成的 Pydantic 契约决定，不能重新放宽。
            field = document["properties"][key]
            allowed = {"none"}
            for branch in field.get("anyOf", [field]):
                definition = document["$defs"][branch["$ref"].split("/")[-1]]
                kind = definition["properties"]["kind"]
                allowed.update(kind.get("enum", [kind.get("const")]))
            options = {k: v for k, v in labels.items() if k in allowed}
            evidence = [s for s in payload["sources"] if s["id"] in pair["evidence_ids"]]
            facts = {f["id"]: f for f in [*payload.get("new", []), *payload.get("candidates", [])]}
            pair_facts = [facts[k] for k in (pair.get("new_id"), pair.get("old_id")) if k in facts]
            refs = {sid for f in pair_facts for sid in f["source_ids"]}
            sources = [
                s
                for s in payload["sources"]
                if not pair_facts or s.get("source_id", s["id"]) in refs
            ]
            state = {
                "新事项": pair["new_claim"],
                "旧事项": pair["old_claim"],
                "新状态": pair["new_status"],
                "旧状态": pair["old_status"],
                "原文": sources,
            }
            kind = await self.choose("reconcile", state, options, "laya_relation")
            evidence_id = None
            if kind != "none":
                # 即使只有一条证据，也必须判断它是否支持该关系。
                evidence_options = {
                    "none": "无依据",
                    **{f"e{i}": s["id"] for i, s in enumerate(evidence)},
                }
                if len(evidence_options) > 10:
                    raise ModelFailure("laya_evidence_options_exceeded")
                selected = await self.choose(
                    "reconcile",
                    {**state, "所选关系": labels[kind], "候选证据": evidence},
                    evidence_options,
                    "laya_evidence",
                )
                if selected == "none":
                    raise ModelFailure("laya_relation_unsupported")
                evidence_id = evidence[int(selected[1:])]["id"]
            output[key] = {
                "kind": kind,
                "evidence_id": evidence_id,
                "reason": "Laya 有限选项判断，证据 ID 已校验；说明由程序生成",
            }
        return output


class DecisionRouter:
    def __init__(self, base, client, fallback=False):
        self.base, self.client, self.fallback = base, client, fallback
        self.fingerprint = None

    async def cache_identity(self, task):
        if task not in {"fields", "reconcile"}:
            return ["responses"]
        try:
            info = await self.client.info()
            self.fingerprint = info["fingerprint"]
        except ModelFailure:
            self.fingerprint = None
            if not self.fallback:
                raise
        prompts = [
            files("meetnote.prompts").joinpath(p + ".txt").read_text()
            for p in ("laya_fields", "laya_relation", "laya_evidence")
        ]
        return ["laya-v1", self.fingerprint, self.fallback, prompts]

    async def generate(self, task, instructions, context, schema):
        if task not in {"fields", "reconcile"}:
            return await self.base.generate(task, instructions, context, schema)
        try:
            if not self.fingerprint:
                await self.cache_identity(task)
            if not self.fingerprint:
                raise ModelFailure("laya_unavailable")
            adapter = DecisionTasks(self.client, self.fingerprint)
            result = (
                await adapter.fields(context)
                if task == "fields"
                else await adapter.reconcile(context, schema)
            )
            return schema.model_validate(result)
        except ModelFailure as exc:
            if not self.fallback:
                raise
            observer = request_observer.get()
            if observer:
                observer({"task": task, "backend": "routing", "fallback": str(exc), "usage": None})
            return await self.base.generate(task, instructions, context, schema)

    async def close(self):
        try:
            await self.client.close()
        finally:
            await self.base.close()
