"""有界证据流水线。所有入口复用此引擎，每会话单消费者。"""

import asyncio
import re
from collections import defaultdict
from copy import copy
from dataclasses import replace
from difflib import SequenceMatcher
from time import monotonic, perf_counter

from meetnote.domain.results import (
    Chapter,
    Fact,
    MinuteDraft,
    ReaderMinutes,
    Relation,
    Revision,
    SectionItem,
    chapter_selection_schema,
    extraction_schema,
    field_support_schema,
    reader_minutes_schema,
    relation_schema,
    unpack_extraction,
)
from meetnote.domain.state import KnowledgeState, Source, StreamState
from meetnote.engine.minutes import RollingMinutes
from meetnote.llm.client import ModelFailure

STAGES = ("ingest", "extract", "fields", "reconcile", "commit", "chapter", "render")
INACTIVE = {"superseded", "retracted", "resolved"}
KIND_LABELS = {
    "fact": "事实",
    "proposal": "提议",
    "decision": "决定",
    "action": "行动",
    "constraint": "约束",
    "question": "问题",
    "risk": "风险",
}
STATUS_LABELS = {
    "confirmed": "已确认",
    "proposed": "提议",
    "unresolved": "待确认",
    "conflicted": "有冲突",
    "superseded": "已修订",
    "retracted": "已撤销",
    "resolved": "已解决",
}


class ExtractionFailure(ModelFailure):
    def __init__(self, message, source_ids):
        super().__init__(message)
        self.source_ids = tuple(source_ids)


class NoProgress(RuntimeError):
    pass


def assignment_lacks_subject(fact):
    if not fact.owner:
        return False
    match = re.match(
        rf"^(?:仍由|由)?(?:{re.escape(fact.owner)}|我)(?:继续|仍然|仍)?负责(?:(?:这|该|此)一?项(?:落实|工作|任务)?)?(?:[，,。；;]|$)",
        fact.text,
    )
    if not match:
        return False
    tail = fact.text[match.end() :]
    if fact.due:
        tail = tail.replace(fact.due, "")
    tail = re.sub(
        r"交付|提交|完成|期限|截止|时间|日期|仍为|仍是|仍|为|是|到|继续|负责|落实|工作|任务|事项|按时|不变|[，,。；;：:\s]",
        "",
        tail,
    )
    # 对象也可以在逗号之后，如“何静负责，周五完成验收”。
    return not tail


def terms(text):
    text = re.sub(r"\s+", "", text.casefold())
    return {text[i : i + 2] for i in range(max(0, len(text) - 1))}


def relation_signals(text):
    signals = set()
    if re.search(r"冲突|分歧|不一致|未达成一致|意见不同|相反|各执", text):
        signals.add("conflicts")
    affirmative = re.sub(
        r"(?:尚未|没有|未|不能|无法|不作|不做|待|需要|需|必须|应|再)(?:正式|最终|明确|进一步|作出|做出)*(?:确认|确定|明确|决定|批准|解决)",
        "",
        text,
    )
    change_text = re.sub(
        r"(?:不允许|不能|不得|不应|不要|无需|尚未|没有|未|无法|不会|不)(?:直接|随意|擅自|再次|自动|一律|轻易|临时|自行|就)*(?:改为|改成|更正|纠正|调整|替代|取代|作废|取消|撤销|撤回|否决|拒绝)",
        "",
        affirmative,
    )
    change_text = re.sub(
        r"(?:不因|不是|并非)[^，,。；;！？]{0,24}?(?:改为|改成|更正|纠正|调整|替代|取代|作废|取消|撤销|撤回|否决|拒绝)",
        "",
        change_text,
    )
    if re.search(
        r"改为|改成|更正|纠正|调整为|调整到|替代|取代|作废|取消|不再|撤销|撤回", change_text
    ):
        signals.add("supersedes")
    if re.search(
        r"改为|改成|更正|纠正|调整|替代|作废|取消|不再|撤销|最终|最后|确认|确定|明确|决定|回复|核实结果|答复|解决|批准|采纳|就按|定为|更新|否决|拒绝|撤回",
        affirmative,
    ):
        signals.add("resolves")
    if re.search(r"取消|不再|撤销|作废|否决|拒绝|撤回", change_text):
        signals.add("retracts")
    return signals


def related(left, right):
    """候选召回只约束讨论对象，不根据此分数删除或修订事实。"""
    a, b = left.subject.strip(), right.subject.strip()
    if a == b or a in b or b in a:
        return True
    # 短对象名仍是有效线索，不能把“演练”当项目名前缀剥掉。
    prefix = 0
    for x, y in zip(a, b):
        if x != y:
            break
        prefix += 1
    if 2 <= prefix < 4 and any(
        relation_signals(f.text) & {"supersedes", "retracts"} for f in (left, right)
    ):
        return True
    at, bt = terms(a), terms(b)
    return bool(at & bt) and len(at & bt) / max(1, min(len(at), len(bt))) >= 0.34


def eligible_pair(new, old, *, scope_quotes=True):
    quotes = revision_quotes(new)
    if (
        scope_quotes
        and quotes
        and not any(quote_matches(quote, old.text) for quote in quotes)
        and not (
            old.status == "unresolved" and old.kind in {"question", "fact"} and related(new, old)
        )
    ):
        return False
    if old.id >= new.id or not (related(new, old) or explicit_reference(new, old)):
        return False
    # 再次提出问题或建议不是修订证据；保留其状态，等待明确答复。
    if new.status != "confirmed" and (
        new.kind in {"question", "proposal"} or old.kind in {"question", "proposal"}
    ):
        return False
    return True


def revision_quotes(fact):
    if not relation_signals(fact.text) & {"supersedes", "retracts"}:
        return []
    return re.findall(r'[“「『"]([^”」』"]{6,160})[”」』"]', fact.text)


def quote_matches(quote, text):
    if quote in text:
        return True
    # 只召回原命题的换序表达，不据此提交修订；完整句仍交给模型比较。
    core = re.sub(r"^(?:本期|本轮|当前|共有|原定|原计划|计划|先)+", "", quote)
    a, b = terms(core), terms(text)
    return len(a & b) >= 5 and len(a & b) / max(1, len(a)) >= 0.75


def explicit_reference(new, old):
    """原文明示引用或分歧时，不能因主题命名变化漏掉候选；这里只召回。"""
    signals = relation_signals(new.text)
    if not signals:
        return False
    if "conflicts" in signals:
        a, b = terms(new.subject), terms(old.subject)
        if a & b and len(a & b) / max(1, min(len(a), len(b))) >= 0.34:
            return True
    if "retracts" in signals and old.status in {"proposed", "unresolved"}:
        a, b = terms(new.text), terms(old.text)
        if len(a & b) >= 5 and len(a & b) / max(1, min(len(a), len(b))) >= 0.15:
            return True
    return any(quote_matches(quote, old.text) for quote in revision_quotes(new))


def identity(fact):
    # 只去掉首尾空白，绝不删除数字、否定或新字段。
    return (
        fact.kind,
        fact.text.strip(),
        fact.subject.strip(),
        fact.status,
        fact.owner.strip(),
        fact.due.strip(),
    )


def same_fact(left, right):
    if identity(left) == identity(right):
        return True
    # 同一原文证据、逐字相同命题及字段，仅主题命名变化不构成新事实。
    compatible_subject = left.subject in right.subject or right.subject in left.subject
    return (
        compatible_subject
        and bool(set(left.source_ids) & set(right.source_ids))
        and (
            left.kind,
            left.text.strip(),
            left.status,
            left.owner.strip(),
            left.due.strip(),
        )
        == (right.kind, right.text.strip(), right.status, right.owner.strip(), right.due.strip())
    )


def escape(text):
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", text).replace("\n", " ")


def fact_text(fact):
    text = fact.text
    if fact.owner and fact.owner not in text:
        text += f"；负责人：{fact.owner}"
    if fact.due and fact.due not in text:
        text += f"；期限：{fact.due}"
    return text


class MeetingEngine:
    def __init__(self, context, title="", meeting_date="", clock=monotonic):
        self.context, self.clock = context, clock
        self.title, self.meeting_date = title, meeting_date
        self.state = {"stream": StreamState(last_check=clock()), "knowledge": KnowledgeState()}
        self.source_index = {}
        self.fact_index = defaultdict(set)
        self.finalized = False
        self.next_fact_id = 1
        self.minutes = RollingMinutes(self)

    @property
    def stream(self):
        return self.state["stream"]

    @property
    def knowledge(self):
        return self.state["knowledge"]

    async def stage(self, name, work):
        started = perf_counter()
        self.context.emit("node.started", {"node": name})
        try:
            result = await work()
        except BaseException as exc:
            self.context.emit("node.failed", {"node": name, "error": str(exc)})
            raise
        self.context.emit(
            "node.completed", {"node": name, "elapsed_ms": round((perf_counter() - started) * 1000)}
        )
        return result

    def segment(self, chunk):
        """优先保留一次发言，超预算时在句子边界打包；永不丢弃字符。"""
        limit = self.context.settings.source_tokens
        sentences = re.findall(r".*?[。！？；\n]+|.+$", chunk.text, flags=re.S)
        pieces, pending = [], ""
        for sentence in sentences:
            if pending and self.context.budget.count(pending + sentence) > limit:
                pieces.append(pending)
                pending = ""
            while self.context.budget.count(sentence) > limit:
                low, high = 1, len(sentence)
                while low < high:
                    mid = (low + high + 1) // 2
                    if self.context.budget.count(sentence[:mid]) <= limit:
                        low = mid
                    else:
                        high = mid - 1
                pieces.append(sentence[:low])
                sentence = sentence[low:]
            pending += sentence
        if pending:
            pieces.append(pending)
        offset = 0
        for index, text in enumerate(pieces):
            start = chunk.start + offset
            yield Source(
                f"s{chunk.seq}.{index}",
                chunk.chunk_id,
                text,
                chunk.speaker,
                chunk.timestamp,
                start,
                start + len(text),
            )
            offset += len(text)

    def ingest(self, chunk):
        if self.finalized:
            raise ModelFailure("engine_finalized")
        if chunk.seq != self.stream.processed + 1:
            raise ModelFailure("input_sequence_invalid")
        if chunk.start != self.stream.cursor or chunk.end != chunk.start + len(chunk.text):
            raise ModelFailure("input_position_invalid")
        sources = list(self.segment(chunk))
        self.stream.chunks.append(chunk)
        self.stream.sources.extend(sources)
        self.stream.pending.extend(sources)
        self.source_index.update((s.id, s) for s in sources)
        self.stream.cursor = chunk.end
        self.stream.processed += 1
        self.context.emit("node.completed", {"node": "ingest", "seq": chunk.seq})

    async def append(self, chunk):
        self.ingest(chunk)
        await self.cycle()
        await self.minutes.tick()

    def prepare_extraction(self, batch):
        # 推理任务只读快照；事实 ID、消费位置及正式状态由 run 的唯一消费者更新。
        snapshot = copy(self)
        snapshot.source_index = dict(self.source_index)
        snapshot.state = dict(self.state)
        snapshot.state["stream"] = replace(self.stream, sources=list(self.stream.sources))
        return snapshot.extract(batch)

    async def run(self, chunks):
        """有界输入预取、无状态抽取并行、有状态核验与提交串行。"""
        iterator = aiter(chunks)
        incoming = None
        jobs = []
        ended = False
        settings = self.context.settings

        async def discard():
            for _, task in jobs:
                task.cancel()
            await asyncio.gather(*(task for _, task in jobs), return_exceptions=True)
            jobs.clear()

        try:
            while not ended or jobs or self.stream.pending:
                await self.minutes.tick()
                reserved = {s.id for batch, _ in jobs for s in batch}
                pending = [s for s in self.stream.pending if s.id not in reserved]
                while pending and len(jobs) < settings.extraction_window:
                    total = sum(self.context.budget.count(s.text) for s in pending)
                    if (
                        not ended
                        and total < settings.batch_tokens
                        and self.clock() - self.stream.last_check < settings.checkpoint_seconds
                    ):
                        break
                    batch, size = [], 0
                    for source in pending:
                        cost = self.context.budget.count(source.text)
                        if batch and (
                            size + cost > settings.batch_tokens
                            or len(batch) >= settings.batch_sources
                        ):
                            break
                        batch.append(source)
                        size += cost
                    if any(s.text.strip() for s in batch):
                        task = asyncio.create_task(self.prepare_extraction(batch))
                    else:
                        task = asyncio.create_task(asyncio.sleep(0))
                    jobs.append((batch, task))
                    peak = self.context.stats.get("extraction_inflight_peak", 0)
                    self.context.stats["extraction_inflight_peak"] = max(peak, len(jobs))
                    pending = pending[len(batch) :]
                if jobs and jobs[0][1].done():
                    batch, task = jobs.pop(0)
                    try:
                        prepared = task.result()
                    except ModelFailure as exc:
                        # 拆分会改变来源 ID，后续快照须重新准备。
                        await discard()
                        await self.process(batch, extraction_error=exc)
                    else:
                        await self.process(batch, prepared=prepared)
                    self.stream.last_check = self.clock()
                    continue
                if not ended and incoming is None and len(jobs) < settings.extraction_window:
                    incoming = asyncio.create_task(anext(iterator))
                waiting = ([incoming] if incoming else []) + ([jobs[0][1]] if jobs else [])
                if self.minutes.task and not self.minutes.task.done():
                    waiting.append(self.minutes.task)
                if not waiting:
                    continue
                timeout = None
                if pending and len(jobs) < settings.extraction_window:
                    timeout = max(
                        0.001, settings.checkpoint_seconds - (self.clock() - self.stream.last_check)
                    )
                if self.minutes.dirty and self.minutes.task is None and self.knowledge.facts:
                    draft_wait = max(0.001, settings.minutes_interval -
                                     (self.clock() - self.minutes.started))
                    timeout = min(timeout, draft_wait) if timeout is not None else draft_wait
                done, _ = await asyncio.wait(
                    waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
                if incoming is not None and incoming in done:
                    try:
                        chunk = incoming.result()
                    except StopAsyncIteration:
                        ended = True
                    else:
                        self.ingest(chunk)
                    incoming = None
            await self.finalize()
        finally:
            if incoming is not None:
                incoming.cancel()
                await asyncio.gather(incoming, return_exceptions=True)
            await discard()
            await self.minutes.close()
            if hasattr(iterator, "aclose"):
                await iterator.aclose()

    async def cycle(self, mode="normal"):
        settings = self.context.settings
        ready = self.context.budget.count(self.stream.buffer) >= settings.batch_tokens
        if (
            mode == "normal"
            and not ready
            and self.clock() - self.stream.last_check < settings.checkpoint_seconds
        ):
            return
        while self.stream.pending:
            batch, total = [], 0
            for source in self.stream.pending:
                cost = self.context.budget.count(source.text)
                if batch and (
                    total + cost > settings.batch_tokens or len(batch) >= settings.batch_sources
                ):
                    break
                batch.append(source)
                total += cost
            await self.process(batch)
            self.stream.last_check = self.clock()
            if (
                mode == "normal"
                and self.context.budget.count(self.stream.buffer) < settings.batch_tokens
            ):
                break

    def ground_fields(self, fact):
        """字段值逐字出现在已引用原文时，程序补全可验证的字段引用。"""
        update = {}
        if assignment_lacks_subject(fact):
            for sid in fact.source_ids:
                if sid not in self.source_index:
                    continue
                raw = self.source_index[sid].text
                searchable = re.sub(r'“[^”]*”|‘[^’]*’|"[^"]*"', lambda m: " " * len(m[0]), raw)
                anchors = list(
                    re.finditer(
                        rf"(?:仍由|由)?(?:{re.escape(fact.owner)}|我)(?:继续|仍然|仍)?负责(?:(?:这|该|此)一?项(?:落实|工作|任务)?)?(?=[，,。；;]|$)",
                        searchable,
                    )
                )
                if len(anchors) != 1:
                    continue
                sentences = re.findall(r"[^。！？；]+[。！？；]?", raw[: anchors[0].start()])
                context = next((s.strip() for s in reversed(sentences) if s.strip()), "")
                if context:
                    # 代词前的原句是依据，模型生成的 subject 不能替代其中的数字和条件。
                    update["text"] = context + fact.text
                    break
        for value_name, refs_name in (("owner", "owner_source_ids"), ("due", "due_source_ids")):
            value = getattr(fact, value_name)
            if value and not getattr(fact, refs_name):
                update[refs_name] = [
                    sid
                    for sid in fact.source_ids
                    if sid in self.source_index and value in self.source_index[sid].text
                ]
        return fact.model_copy(update=update)

    def validate_extraction(self, result, batch, context_ids=()):
        ids = {s.id for s in batch}
        allowed = ids | set(context_ids)
        used = set()
        for raw_fact in result.facts:
            refs = set(raw_fact.source_ids)
            if refs <= set(context_ids):
                # 模型重述已经处理的前文不是新增事实；不会借此满足本批覆盖。
                continue
            fact = self.ground_fields(raw_fact)
            unquoted_claim = re.sub(r'“[^”]*”|‘[^’]*’|"[^"]*"', "", fact.text)
            if (
                fact.status == "confirmed"
                and fact.kind != "action"
                and not re.search(r"如果|假如|若|一旦|只要|原来|原先|此前|曾经", unquoted_claim)
                and re.search(
                    r"(?:^|[，,。；;])[^，,。；;]{0,20}(?:仍待|尚待|尚未|仍未|还没有)(?:最后|最终|正式|明确)?(?:确认|确定|决定)",
                    unquoted_claim,
                )
            ):
                raise ModelFailure(
                    f"pending_claim_category_invalid: {fact.text}。仍待确认的具体事项应为未决问题或未确认信息；与已确定规则混在一起时必须拆成不同状态的条目。"
                )
            if fact.status == "confirmed" and re.fullmatch(
                r"(?:目前|当前|现在|今天|本次|本轮|会议|暂时|暂|先|还|仍|尚)*(?:不作|不做|未作出|未做出|尚未作出)(?:最终|正式)?决定[。！.!]?",
                fact.text.strip(),
            ):
                raise ModelFailure(
                    f"pending_decision_category_invalid: {fact.text}。暂未定案应连同所讨论的具体事项记为未决问题，不另造已确认约束。"
                )
            if assignment_lacks_subject(fact):
                raise ModelFailure(
                    f"assignment_subject_missing: {fact.text}。请从所引原文补明实际负责的对象、要求和数字，不能只写‘这项落实’或‘继续负责’。"
                )
            if fact.owner and (
                fact.status != "confirmed" or fact.kind not in {"action", "decision", "constraint"}
            ):
                raise ModelFailure(
                    f"owner_category_invalid: text={fact.text}, owner={fact.owner}, kind={fact.kind}。"
                    "原文明示‘由某人负责’、点名执行或本人承诺时，category 应选已分配行动，不能选已确认事实。"
                    "若仅为建议者、发言人或未分工，则清空 owner，不得为保留姓名把建议升为行动。"
                )
            refs = set(fact.source_ids)
            if not refs <= allowed or not refs & ids:
                raise ModelFailure(
                    f"证据必须属于本轮 sources 或 context_sources，且每条新事实必须至少引用一个本轮 ID。只有前文依据的旧事实不要再次输出。new_ids={sorted(ids)}; invalid_refs={sorted(refs - allowed)}; received={sorted(refs)}"
                )
            used |= refs & ids
            if fact.kind == "proposal" and fact.status != "proposed":
                raise ModelFailure("proposal_status_invalid")
            if fact.kind in {"question", "risk"} and fact.status != "unresolved":
                raise ModelFailure("unresolved_status_required")
            for value, evidence in (
                (fact.owner, fact.owner_source_ids),
                (fact.due, fact.due_source_ids),
            ):
                if (bool(value) != bool(evidence)) or not set(evidence) <= refs:
                    raise ModelFailure("field_evidence_invalid")
            if fact.owner:
                owners = re.split(r"[、，,/&]+", fact.owner)
                evidence_text = " ".join(
                    self.source_index[sid].text
                    + (
                        self.source_index[sid].speaker or ""
                        # 这里只核对姓名／第一人称能否指向发言人；是否真正分工由 fields 任务核验。
                        if re.search(r"我(?!们|方)", self.source_index[sid].text)
                        else ""
                    )
                    for sid in fact.owner_source_ids
                )
                if any(owner.strip() not in evidence_text for owner in owners):
                    raise ModelFailure(
                        f"owner_not_in_cited_source: owner={fact.owner}, text={fact.text}。发言人姓名不能作为分工依据；只有原文明示执行人或第一人称承诺才能填写负责人。"
                    )
        ignored = [item.source_id for item in result.ignored]
        if len(set(ignored)) != len(ignored) or set(ignored) & used:
            raise ModelFailure("source_disposition_conflict")
        if used | set(ignored) != ids:
            raise ModelFailure(
                f"source_coverage_incomplete:missing={sorted(ids - used - set(ignored))}"
            )
        for source in batch:
            unquoted = re.sub(r'“[^”]*”|‘[^’]*’|"[^"]*"', "", source.text)
            assignment_text = re.sub(
                r"(?:不是|并非|不再|不会|不能|不)(?:由我(?:来)?(?:负责|执行|收口|汇总)|交给我汇总)",
                "",
                unquoted,
            )
            assignments = [
                f
                for f in result.facts
                if source.id in f.source_ids and f.status == "confirmed" and f.owner
            ]
            if (
                source.speaker
                and not re.search(
                    r"只是建议|尚未批准|没有批准|未通过|没有通过|假如|如果|尚未决定|原来|原先|原本|曾经",
                    source.text,
                )
                and re.search(
                    r"(?:^|[。！？；]\s*)我(?:还|也)?(?:会(?!建议|认为|考虑)|将)|(?:^|[。！？；]\s*)我准备[^。！？]{0,40}[，,]会|(?<!不)(?:由我(?:来)?(?:负责|执行|收口|汇总)|交给我汇总)",
                    assignment_text,
                )
                and not any(f.owner == source.speaker for f in assignments)
            ):
                raise ModelFailure(
                    f"first_person_assignment_missing: {source.id} 明示本人将执行任务，需分别记录行动并填写负责人 {source.speaker}；不能改写成仅表达看法或待批准建议。"
                )
            if not assignments:
                continue
            # 仅检查无引用、无假设或撤销语气的直接分工句；不把历史/建议强制变为行动。
            for sentence in re.split(r"[。！？；]", unquoted):
                if re.search(
                    r"建议|尚未|未批准|没有批准|如果|假如|原来|原先|曾经|撤销|取消|作废|是否|能否",
                    sentence,
                ):
                    continue
                for name, deadline in re.findall(
                    r"(?:仍)?由([^，,。；;\s]{1,12})负责[，,]\s*(?:交付|提交|完成)期限(?:仍)?(?:为|是)([^，,。；;]+)",
                    sentence,
                ):
                    if not any(f.owner == name and deadline.strip() in f.due for f in assignments):
                        raise ModelFailure(
                            f"assignment_deadline_missing: {source.id} 明确由 {name} 负责，交付期限为 {deadline.strip()}。"
                            "必须在同一条已分配行动中保留 owner 与 due，不能将期限拆成无主日期或被活动日期替代。"
                        )
            duties = re.findall(
                r"(?:由|[，,、；;])([^，,、；;\s]{1,8}?)(?:担任|负责)?(主值班|备班)", source.text
            )
            missing = [
                name
                for name, role in duties
                if not any(name == f.owner and role in f.text for f in assignments)
            ]
            if missing:
                raise ModelFailure(
                    "值班分工覆盖不完整："
                    + "、".join(missing)
                    + "；主值班与备班必须分别记录负责人及其岗位。"
                )

    async def extract(self, batch):
        meaningful = [s for s in batch if s.text.strip()]
        context_sources = []
        for source in reversed(self.stream.sources):
            if source.end > meaningful[0].start:
                continue
            value = source.payload()
            if self.context.budget.count([value, *context_sources]) > 512:
                break
            context_sources.insert(0, value)
        payload = {
            "meeting_date": self.meeting_date,
            "sources": [s.payload() for s in meaningful],
            "context_sources": context_sources,
        }

        def validate_wire(wire):
            context_ids = {s["id"] for s in context_sources}
            mismatches = []
            for sid, record in wire.model_dump().items():
                for value in record["facts"]:
                    refs = set(value["source_ids"])
                    if not refs <= context_ids and sid not in refs:
                        message = f"source_fact_mismatch: {sid} 下的事实至少引用本片段；不能把相邻发言的 ID 互换。text={value['text']}"
                        if not self.context.settings.local_extraction_retry:
                            raise ExtractionFailure(message, [sid])
                        mismatches.append((sid, message))
            if mismatches:
                raise ExtractionFailure(
                    "\n".join(message for _, message in mismatches),
                    dict.fromkeys(sid for sid, _ in mismatches),
                )
            try:
                self.validate_extraction(unpack_extraction(wire), meaningful, list(context_ids))
            except ModelFailure as exc:
                bad = []
                records = wire.model_dump()
                ids = {s.id for s in meaningful}
                for source in meaningful:
                    record = records[source.id]
                    # 只对不涉及本批其他记录的错误缩小范围；跨记录错误整批处理。
                    refs = {sid for f in record["facts"] for sid in f["source_ids"]}
                    if refs & (ids - {source.id}):
                        raise exc
                    one = extraction_schema([source.id]).model_validate({source.id: record})
                    try:
                        self.validate_extraction(
                            unpack_extraction(one), [source], list(context_ids)
                        )
                    except ModelFailure:
                        bad.append(source.id)
                if bad:
                    raise ExtractionFailure(str(exc), bad) from exc
                raise

        async def repair(wire, error):
            ids = list(error.source_ids)
            self.context.stats["local_extraction_retries"] = (
                self.context.stats.get("local_extraction_retries", 0) + 1
            )
            correction = {
                **payload,
                "sources": [s for s in payload["sources"] if s["id"] in ids],
                "validation_error": str(error),
                "instruction": "仅重新输出指定 sources 的完整结果，修正引用和覆盖错误。",
            }

            def merged(fixed):
                return type(wire).model_validate({**wire.model_dump(), **fixed.model_dump()})

            fixed = await self.context.invoke(
                "extract",
                correction,
                extraction_schema(ids),
                lambda fixed: validate_wire(merged(fixed)),
                retry_validation=False,
            )
            return merged(fixed)

        wire = await self.stage(
            "extract",
            lambda: self.context.invoke(
                "extract",
                payload,
                extraction_schema([s.id for s in meaningful]),
                validate_wire,
                repair=repair if self.context.settings.local_extraction_retry else None,
            ),
        )
        return wire

    async def process(self, batch, depth=0, prepared=None, extraction_error=None):
        meaningful = [s for s in batch if s.text.strip()]
        if not meaningful:
            self.commit(batch, [], [], {s.id: "whitespace" for s in batch})
            return
        try:
            if extraction_error is not None:
                raise extraction_error
            wire = prepared if prepared is not None else await self.extract(batch)
            result = unpack_extraction(wire)
        except ModelFailure as exc:
            splittable = any(
                code in str(exc)
                for code in ("incomplete", "input_budget", "task_validation_failed")
            )
            if not splittable or depth >= 4:
                raise
            self.context.stats["extraction_splits"] = (
                self.context.stats.get("extraction_splits", 0) + 1
            )
            if len(batch) > 1:
                middle = len(batch) // 2
                await self.process(batch[:middle], depth + 1)
                await self.process(batch[middle:], depth + 1)
                return
            source = batch[0]
            # 多事项短发言也可能连续触发不同校验错误；优先按完整句子缩小任务。
            boundaries = [
                m.end()
                for m in re.finditer(r"[。！？；]", source.text)
                if source.text[m.end() :].strip()
            ]
            if boundaries:
                middle = min(boundaries, key=lambda pos: abs(pos - len(source.text) // 2))
            elif self.context.budget.count(source.text) >= 256:
                middle = len(source.text) // 2
            else:
                raise
            children = [
                replace(
                    source,
                    id=source.id + ":a",
                    text=source.text[:middle],
                    end=source.start + middle,
                ),
                replace(
                    source,
                    id=source.id + ":b",
                    text=source.text[middle:],
                    start=source.start + middle,
                ),
            ]
            pos = self.stream.pending.index(source)
            self.stream.pending[pos : pos + 1] = children
            pos = self.stream.sources.index(source)
            self.stream.sources[pos : pos + 1] = children
            self.source_index.pop(source.id)
            self.source_index.update((s.id, s) for s in children)
            for child in children:
                await self.process([child], depth + 1)
            return
        inputs = [f for f in result.facts if set(f.source_ids) & {s.id for s in meaningful}]
        new = [
            Fact(**self.ground_fields(f).model_dump(), id=f"f{self.next_fact_id + i:06d}")
            for i, f in enumerate(inputs)
        ]
        contextualized = {f.id for f, raw in zip(new, inputs) if f.text != raw.text}
        new = await self.stage("fields", lambda: self.audit_fields(new))
        # 暂存所有变更，归并成功后与消费位置一起提交。
        relations = await self.stage("reconcile", lambda: self.reconcile(new, contextualized))
        dispositions = {s.id: "extracted" for s in meaningful}
        dispositions.update((s.id, "whitespace") for s in batch if not s.text.strip())
        dispositions.update((s.source_id, "ignored: " + s.reason) for s in result.ignored)
        await asyncio.sleep(0)  # 在不可分割的提交前响应取消。
        self.commit(batch, new, relations, dispositions)

    def literal_owner_support(self, fact):
        """仅证明逐字对应的第一人称执行句，不从同段出现姓名推断分工。"""
        if not fact.owner or fact.status != "confirmed":
            return False
        clauses = [
            clause.replace(fact.owner, "我").strip()
            for clause in re.split(r"[，,。；;]", fact.text)
            if fact.owner in clause
        ]
        for sid in fact.owner_source_ids:
            source = self.source_index[sid]
            if source.speaker != fact.owner or re.search(
                r"建议|尚未批准|没有批准|未通过|没有通过|假如|如果|原来|原先|原本|曾经|不再|不是|并非|是否|能否|会不会|要不要|[？?]",
                source.text,
            ):
                continue
            raw = re.sub(r'“[^”]*”|‘[^’]*’|"[^"]*"', "", source.text)
            originals = {clause.strip() for clause in re.split(r"[，,。；;]", raw)}
            if any(
                clause in originals
                and not re.search(
                    r"(?:不|未|没|别|禁止|无需|不用)[^，,。；;]{0,8}(?:由我|交给我)", clause
                )
                and re.search(
                    r"^我(?:还|也)?(?:会(?!建议|认为|考虑)|将|负责)|由我(?:来)?(?:负责|执行|收口|汇总)|交给我汇总",
                    clause,
                )
                for clause in clauses
            ):
                return True
        return False

    async def audit_fields(self, facts):
        """独立核对字段归属；不能仅凭姓名或日期出现在引用中就确认分工。"""
        audited = {}
        proven_owners = {f.id for f in facts if self.literal_owner_support(f)}

        async def page(items):
            if not items:
                return
            ids = {sid for f in items for sid in f.owner_source_ids + f.due_source_ids}
            payload = {
                "claims": [
                    {
                        k: v
                        for k, v in f.model_dump().items()
                        if k
                        in {
                            "id",
                            "text",
                            "kind",
                            "owner",
                            "due",
                            "owner_source_ids",
                            "due_source_ids",
                        }
                    }
                    for f in items
                ],
                "sources": [self.source_index[sid].payload() for sid in sorted(ids)],
            }
            schema = field_support_schema([f.id for f in items])
            if len(items) > 6 or not self.context.fits("fields", payload, schema):
                if len(items) == 1:
                    self.context.budget.check(self.context.prompt("fields"), payload, schema)
                middle = len(items) // 2
                await page(items[:middle])
                await page(items[middle:])
                return
            result = await self.context.invoke("fields", payload, schema)
            for fact in items:
                verdict = getattr(result, fact.id)
                changes = {}
                if fact.owner and fact.id not in proven_owners and not verdict.owner_supported:
                    changes.update(owner="", owner_source_ids=[])
                if fact.due and not verdict.due_supported:
                    changes.update(due="", due_source_ids=[])
                if changes:
                    audited[fact.id] = fact.model_copy(update=changes)
                    self.context.emit(
                        "fields.corrected",
                        {
                            "fact_id": fact.id,
                            "fields": list(changes),
                            "reason": verdict.reason,
                        },
                    )

        await page([f for f in facts if (f.owner and f.id not in proven_owners) or f.due])
        return [audited.get(f.id, f) for f in facts]

    def evidence_view(self, fact):
        """召回读取实际新原文；抽取后的改写可能省略被撤销安排的引文。"""
        evidence = [
            self.source_index[sid].text
            for sid in fact.source_ids
            if self.source_index[sid].end > self.stream.consumed
        ]
        return fact.model_copy(update={"text": fact.text + "\n" + "\n".join(evidence)})

    def candidates(self, new):
        found = set()
        for f in new:
            for term in terms(f.subject + f.topic):
                found.update(self.fact_index.get(term, ()))
        views = [self.evidence_view(f) for f in new]
        return [
            f
            for f in self.knowledge.summaries
            if (f.id in found and any(related(f, n) for n in new))
            or any(explicit_reference(n, f) for n in views)
        ]

    def relation_payload(self, new, old):
        candidates = []
        for fact in old:
            ordered = sorted(fact.source_ids, key=lambda sid: self.source_index[sid].start)
            # 重复确认会不断合并证据；比对读取首次及最近依据，账本保留全部引用。
            selected = list(dict.fromkeys(ordered[:1] + ordered[-1:]))
            value = fact.model_dump()
            value["source_ids"] = selected
            for field in ("owner_source_ids", "due_source_ids"):
                value[field] = [sid for sid in value[field] if sid in selected]
            value["additional_source_count"] = len(ordered) - len(selected)
            candidates.append(value)
        refs = {sid for f in new for sid in f.source_ids}
        refs.update(sid for f in candidates for sid in f["source_ids"])
        spans = []
        for sid in sorted(refs):
            source = self.source_index[sid]
            for index, text in enumerate(re.findall(r".*?[。！？；\n]+|.+$", source.text, re.S)):
                if text.strip():
                    spans.append(
                        {
                            "id": f"{sid}#{index}",
                            "source_id": sid,
                            "text": text,
                            "speaker": source.speaker,
                            "timestamp": source.timestamp,
                        }
                    )
        return {
            "new": [f.model_dump() for f in new],
            "candidates": candidates,
            "sources": spans,
        }

    async def reconcile(self, new, contextualized=()):
        seen = list(self.knowledge.summaries)
        novel = []
        for fact in new:
            if not any(same_fact(fact, previous) for previous in seen):
                novel.append(fact)
                seen.append(fact)
        new = novel
        old = self.candidates(new) + new[:-1]

        async def page(news, olds):
            views = {n.id: self.evidence_view(n) for n in news}
            pairs = [
                (n, o)
                for n in news
                for o in olds
                if eligible_pair(
                    views[n.id],
                    o,
                    scope_quotes=not (
                        relation_signals(n.text) & {"supersedes", "retracts"}
                        and not revision_quotes(n)
                    ),
                )
                and not (
                    set(n.source_ids) <= set(o.source_ids)
                    and (
                        n.id in contextualized
                        or o.id in contextualized
                        or (
                            relation_signals(o.text) & {"supersedes", "retracts"}
                            and not relation_signals(n.text) & {"supersedes", "retracts"}
                        )
                    )
                )
            ]
            if not pairs:
                return []
            payload = self.relation_payload(news, olds)
            spans = {s["id"]: s for s in payload["sources"]}
            choices, kinds, selected = {}, {}, []
            for nf, of in pairs:
                ids = [
                    sid
                    for sid, span in spans.items()
                    if span["source_id"] in nf.source_ids and relation_signals(span["text"])
                ]
                if ids:
                    key = f"p{len(selected)}"
                    allowed = set().union(*(relation_signals(spans[s]["text"]) for s in ids))
                    if nf.status != "confirmed":
                        allowed &= {"conflicts"}
                    if of.status not in {"unresolved", "conflicted", "proposed"}:
                        allowed.discard("resolves")
                    if re.search(
                        r"(?:尚未|仍未|还未|还没|尚无|仍无|未能|没有|无法|不能)(?:有|得到|获得|最终|正式|明确)*(?:确认|确定|解决|答复|结论)",
                        nf.text,
                    ):
                        # 原文同句可能还包含别的“确认”；未决命题的重申不是答案。
                        allowed.discard("resolves")
                    if not allowed:
                        continue
                    choices[key] = ids
                    kinds[key] = ["none", *sorted(allowed)]
                    selected.append((nf, of))
            if not selected:
                return []
            if len(selected) > 6:
                if len(news) > 1:
                    middle = len(news) // 2
                    return await page(news[:middle], olds) + await page(news[middle:], olds)
                middle = len(olds) // 2
                return await page(news, olds[:middle]) + await page(news, olds[middle:])
            # 只携带实际待判断的事实对，不能把未入选候选及其原文反复装入请求。
            payload = self.relation_payload(
                list({f.id: f for f, _ in selected}.values()),
                list({f.id: f for _, f in selected}.values()),
            )
            payload["allowed_pairs"] = [[n.id, o.id] for n, o in selected]
            payload["pairs"] = {
                key: {
                    "new_id": nf.id,
                    "old_id": of.id,
                    "new_claim": fact_text(nf),
                    "old_claim": fact_text(of),
                    "new_status": nf.status,
                    "old_status": of.status,
                    "evidence_ids": choices[key],
                }
                for key, (nf, of) in zip(choices, selected)
            }
            schema = relation_schema(choices, kinds)
            if not self.context.fits("reconcile", payload, schema):
                if len(news) > 1:
                    middle = len(news) // 2
                    return await page(news[:middle], olds) + await page(news[middle:], olds)
                if len(olds) > 1:
                    middle = len(olds) // 2
                    return await page(news, olds[:middle]) + await page(news, olds[middle:])
                raise ModelFailure("relation_input_budget_exceeded")

            def validate(result):
                for key, value in result.model_dump().items():
                    nf, of = selected[int(key[1:])]
                    kind, evidence = value["kind"], value["evidence_id"]
                    if kind == "none":
                        continue
                    if evidence not in choices[key] or kind not in relation_signals(
                        spans[evidence]["text"]
                    ):
                        raise ModelFailure("relation_evidence_invalid")
                    if kind == "resolves" and of.status not in {
                        "unresolved",
                        "conflicted",
                        "proposed",
                    }:
                        raise ModelFailure("resolution_target_invalid")
                    if kind != "conflicts" and nf.status != "confirmed":
                        raise ModelFailure("unconfirmed_revision")

            try:
                result = await self.context.invoke("reconcile", payload, schema, validate)
            except ModelFailure as exc:
                if "incomplete" not in str(exc):
                    raise
                if len(selected) > 1:
                    if len(news) > 1:
                        middle = len(news) // 2
                        return await page(news[:middle], olds) + await page(news[middle:], olds)
                    middle = len(olds) // 2
                    return await page(news, olds[:middle]) + await page(news, olds[middle:])
                # 单个事实对只额外尝试一次；再次失败保留原文，不把失败解释为 none。
                retry = {
                    **payload,
                    "response_error": str(exc),
                    "instruction": "只输出符合 Schema 的完整 JSON，结束后立即停止，不重复空白或内容。",
                }
                result = await self.context.invoke("reconcile", retry, schema, validate)
            accepted = []
            for key, value in result.model_dump().items():
                if value["kind"] == "none":
                    continue
                nf, of = selected[int(key[1:])]
                accepted.append(
                    Relation(
                        new_id=nf.id,
                        old_id=of.id,
                        kind=value["kind"],
                        reason=value["reason"] or "原文支持的修订关系",
                        source_ids=list(dict.fromkeys(nf.source_ids + of.source_ids)),
                    )
                )
            return accepted

        return await page(new, old)

    def commit(self, batch, new, relations, dispositions):
        if (
            not batch
            or self.stream.pending[: len(batch)] != batch
            or batch[0].start != self.stream.consumed
        ):
            raise NoProgress("commit_boundary_invalid")
        facts = list(self.knowledge.facts)
        revisions = list(self.knowledge.revisions)
        # 精确重复只合并证据；数字、状态、负责人及期限变化均保留。
        remap = {}
        for fact in new:
            previous = next((f for f in facts if same_fact(f, fact)), None)
            if previous:
                merged = previous.model_copy(
                    update={
                        "source_ids": list(dict.fromkeys(previous.source_ids + fact.source_ids)),
                        "owner_source_ids": list(
                            dict.fromkeys(previous.owner_source_ids + fact.owner_source_ids)
                        ),
                        "due_source_ids": list(
                            dict.fromkeys(previous.due_source_ids + fact.due_source_ids)
                        ),
                        "revision": previous.revision + 1,
                    }
                )
                facts[facts.index(previous)] = merged
                revisions.append(
                    Revision(
                        fact_id=previous.id,
                        revision=merged.revision,
                        before=previous,
                        after=merged,
                        reason="exact_duplicate",
                        source_ids=fact.source_ids,
                    )
                )
                remap[fact.id] = previous.id
            else:
                facts.append(fact)
        by_id = {f.id: f for f in facts}
        targets = defaultdict(list)
        for relation in relations:
            new_id = remap.get(relation.new_id, relation.new_id)
            old_id = remap.get(relation.old_id, relation.old_id)
            if new_id != old_id:
                targets[old_id].append((relation, new_id))
        for old_id, changes in targets.items():
            # 同一次修订可拆成新数值、撤销说明等多条事实；多条后继不等于语义冲突。
            conflict = any(r.kind == "conflicts" for r, _ in changes)
            for relation, new_id in changes:
                updates = [
                    (
                        old_id,
                        "conflicted"
                        if conflict
                        else {
                            "supersedes": "superseded",
                            "retracts": "retracted",
                            "resolves": "resolved",
                        }[relation.kind],
                    )
                ]
                if conflict:
                    updates.append((new_id, "conflicted"))
                for target, status in updates:
                    before = by_id[target]
                    after = before.model_copy(
                        update={
                            "status": status,
                            "revision": before.revision + 1,
                            "related_ids": list(
                                dict.fromkeys(
                                    before.related_ids + [new_id if target == old_id else old_id]
                                )
                            ),
                        }
                    )
                    by_id[target] = after
                    revisions.append(
                        Revision(
                            fact_id=target,
                            revision=after.revision,
                            before=before,
                            after=after,
                            reason=relation.reason,
                            source_ids=relation.source_ids,
                        )
                    )
        self.next_fact_id += len(new)
        self.knowledge.facts = [by_id[f.id] for f in facts]
        self.knowledge.revisions = revisions
        for f in self.knowledge.facts:
            for term in terms(f.subject + f.topic):
                self.fact_index[term].add(f.id)
        self.stream.dispositions.update(dispositions)
        del self.stream.pending[: len(batch)]
        self.stream.consumed = batch[-1].end
        self.context.emit(
            "facts.updated",
            {
                "facts": [by_id.get(remap.get(f.id, f.id)).model_dump() for f in new],
                "revision_count": len(revisions),
            },
        )
        self.context.emit(
            "node.completed", {"node": "commit", "consumed_chars": self.stream.consumed}
        )

    def chapter_payload(self, topic, items, limit):
        return {
            "topic": topic,
            "selection_limit": min(6, limit),
            "items": [i.model_dump() for i in items],
        }

    async def write_chapter(self, topic, items, limit):
        allowed = {fid for item in items for fid in item.fact_ids}
        by_id = {f.id: f for f in self.knowledge.summaries}

        def materialize(ids):
            return Chapter(
                title=topic,
                items=[
                    SectionItem(
                        text=f"【{STATUS_LABELS[by_id[fid].status]}】{fact_text(by_id[fid])}",
                        fact_ids=[fid],
                    )
                    for fid in dict.fromkeys(ids)
                ],
            )

        if len(allowed) <= min(6, limit):
            return materialize([fid for item in items for fid in item.fact_ids])
        payload = self.chapter_payload(topic, items, limit)
        schema = chapter_selection_schema(limit)

        def validate(result):
            if not set(result.fact_ids) <= allowed:
                raise ModelFailure("chapter_reference_invalid")

        try:
            result = await self.context.invoke("chapter", payload, schema, validate)
            return materialize(result.fact_ids)
        except ModelFailure as exc:
            warning = "章节使用事实列表降级：" + str(exc)
            if warning not in self.knowledge.warnings:
                self.knowledge.warnings.append(warning)
            self.context.emit("node.degraded", {"node": "chapter", "reason": str(exc)})
            return materialize([fid for item in items for fid in item.fact_ids])

    async def bounded_map(self, operation, items):
        results = []
        width = (
            self.context.settings.model_concurrency
            if self.context.settings.parallel_chapters
            else 1
        )
        for offset in range(0, len(items), width):
            tasks = [
                asyncio.create_task(operation(item)) for item in items[offset : offset + width]
            ]
            try:
                results.extend(await asyncio.gather(*tasks))
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def select_items(self, topic, facts, limit):
        by_id = {f.id: f for f in facts}

        def item(fid):
            f = by_id[fid]
            return SectionItem(
                text=f"[{f.kind}/{f.status}] {f.topic}: {fact_text(f)}", fact_ids=[fid]
            )

        current = [item(f.id) for f in facts]
        schema = chapter_selection_schema(limit)
        while len(current) > limit:
            pages, page = [], []
            for candidate in current:
                if page and (
                    len(page) >= 24
                    or not self.context.fits(
                        "chapter", self.chapter_payload(topic, page + [candidate], limit), schema
                    )
                ):
                    pages.append(page)
                    page = []
                page.append(candidate)
            if page:
                pages.append(page)
            selected = []
            chapters = await self.bounded_map(
                lambda page: self.write_chapter(topic, page, limit), pages
            )
            for chapter in chapters:
                selected.extend(fid for value in chapter.items for fid in value.fact_ids)
            selected = list(dict.fromkeys(selected))
            if len(selected) >= len(current):
                return [item(fid) for fid in selected[:limit]]
            current = [item(fid) for fid in selected]
        return current

    async def compose(self, on_output=None, commit=True):
        active = self.knowledge.summaries
        body_candidates = [
            f
            for f in active
            if not re.fullmatch(
                r"(?:今天|本次|这次)?(?:散会|会议结束|会议到此结束|就到这里)|谢谢大家",
                f.text.strip("。！.! "),
            )
        ]
        by_id = {f.id: f for f in active}
        limit = self.context.settings.body_max_items
        # 修订后的现行安排先保留独立名额，避免最后确认被大量早期背景挤掉。
        successors = {
            fid for f in self.knowledge.facts if f.status in INACTIVE for fid in f.related_ids
        }
        revised = [f for f in body_candidates if f.id in successors]
        current = await self.select_items("修订后的现行安排", revised, max(1, limit // 3))
        chosen = {fid for item in current for fid in item.fact_ids}
        buckets = {name: [] for name in ("决定", "行动", "未决事项", "约束", "背景事实")}
        for f in body_candidates:
            if f.id in chosen:
                continue
            if f.status in {"unresolved", "conflicted", "proposed"}:
                bucket = "未决事项"
            else:
                bucket = {"decision": "决定", "action": "行动", "constraint": "约束"}.get(
                    f.kind, "背景事实"
                )
            buckets[bucket].append(f)
        buckets["行动"].sort(key=lambda f: (bool(f.owner), bool(f.due)), reverse=True)
        # 各工作类别轮流获得名额；背景只使用剩余名额，不和行动、决定争抢。
        quotas = dict.fromkeys(buckets, 0)
        remaining = limit - len(current)
        while remaining:
            progressed = False
            for name in ("决定", "行动", "未决事项", "约束"):
                if remaining and quotas[name] < len(buckets[name]):
                    quotas[name] += 1
                    remaining -= 1
                    progressed = True
            if not progressed:
                break
        quotas["背景事实"] = min(remaining, len(buckets["背景事实"]))

        async def select_bucket(name):
            return await self.select_items(name, buckets[name], quotas[name])

        selections = await self.bounded_map(
            select_bucket, [name for name in buckets if quotas[name]]
        )
        for selection in selections:
            current.extend(selection)
        groups = defaultdict(list)
        for item in current:
            for fid in item.fact_ids:
                fact = by_id[fid]
                groups[fact.topic].append(
                    SectionItem(
                        text=f"【{STATUS_LABELS[fact.status]}】{fact_text(fact)}", fact_ids=[fid]
                    )
                )
        sections = [Chapter(title=topic, items=items) for topic, items in groups.items()]
        narrative = None
        chunks = ["# " + escape(self.title or "会议纪要")]
        selected = [
            by_id[fid] for fid in dict.fromkeys(fid for item in current for fid in item.fact_ids)
        ]
        if selected:
            evidence = [
                {
                    "id": f.id,
                    "source_ids": list(dict.fromkeys(f.source_ids[:1] + f.source_ids[-2:])),
                }
                for f in selected
            ]
            refs = {sid for f in evidence for sid in f["source_ids"]}
            payload = {
                "facts": evidence,
                "sources": [s.payload() for s in self.stream.sources if s.id in refs],
            }
            allowed = {f.id for f in selected}
            minutes_schema = reader_minutes_schema([f.id for f in selected])

            def validate_minutes(result):
                paragraphs = [
                    result.overview,
                    *result.conclusions,
                    *result.discussion,
                    *result.actions,
                    *result.open_questions,
                ]
                for index, paragraph in enumerate(paragraphs):
                    for previous in paragraphs[:index]:
                        if (
                            min(len(paragraph.text), len(previous.text)) > 40
                            and SequenceMatcher(None, paragraph.text, previous.text).ratio() > 0.72
                        ):
                            raise ModelFailure(
                                "minutes_repeats_same_content:概述、结论和讨论不能重复同一组信息，删除重复段落，空类别返回空数组"
                            )
                if any(not set(p.fact_ids) <= allowed for p in paragraphs):
                    raise ModelFailure("minutes_reference_invalid")
                if any(
                    re.search(
                        r"f\d{6}|s\d+\.\d+|【(?:已确认|待确认)】|完整事项清单|原文索引", p.text
                    )
                    for p in paragraphs
                ):
                    raise ModelFailure("minutes_contains_internal_labels")

            narrative = await self.context.invoke(
                "minutes", payload, minutes_schema, validate_minutes,
                on_output=(lambda delta: on_output("minutes", delta)) if on_output else None,
            )
            narrative = await self.context.invoke(
                "minutes_review",
                {**payload, "draft": narrative.model_dump(mode="json")},
                minutes_schema,
                validate_minutes,
                on_output=(lambda delta: on_output("minutes_review", delta)) if on_output else None,
            )
            narrative = ReaderMinutes.model_validate(narrative.model_dump())
            chunks.append(escape(narrative.overview.text))
            for key, heading in [
                ("conclusions", "主要结论"),
                ("discussion", "讨论要点"),
                ("actions", "后续安排"),
                ("open_questions", "尚需明确"),
            ]:
                paragraphs = getattr(narrative, key)
                if paragraphs:
                    chunks.extend(
                        ["## " + heading, "\n\n".join(escape(p.text) for p in paragraphs)]
                    )
        else:
            chunks.append("本次记录未包含可整理的实质讨论内容。")
        draft = MinuteDraft(
            title=self.title or "会议纪要",
            markdown="\n\n".join(chunks),
            narrative=narrative,
            sections=sections,
            checklist=active,
            evidence_trace=[f.id for f in active],
            delivery_warnings=list(self.knowledge.warnings),
            coverage={
                "sources": len(self.stream.sources),
                "processed_sources": len(self.stream.dispositions),
                "active_facts": len(active),
                "checklist_facts": len(active),
                "consumed_chars": self.stream.consumed,
            },
        )
        if commit:
            self.knowledge.draft = draft
        return draft

    async def finalize(self):
        if self.finalized:
            return
        await self.cycle("flush")
        if self.stream.consumed != self.stream.cursor or len(self.stream.dispositions) != len(
            self.stream.sources
        ):
            raise NoProgress("source_coverage_incomplete")
        if self.knowledge.facts:
            await self.minutes.finish()
        self.context.emit("node.completed", {"node": "render"})
        self.finalized = True
