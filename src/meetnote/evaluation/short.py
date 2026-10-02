"""短反例：可重复定位语义错误；真实模型检查不加入默认 pytest。"""

import json

from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ResponsesClient

# alternatives 表示同一检查允许的表达；不能用模型自己给自己评分。
CASES = {
    "amount": (
        ["采购预算原定十万元。", "刚才的预算数字说错了，现在正式改为二十万元，十万元作废。"],
        [["二十万", "20万"]],
    ),
    "question": (
        ["采购报价是否含税？这件事尚未确认。", "财务回复了，确认采购报价含税，这个问题已经解决。"],
        [["含税"]],
    ),
    "unit": (
        ["测试压差要求二十兆帕。", "更正：刚才单位口误，正确要求是二十千帕，不是二十兆帕。"],
        [["二十千帕", "20千帕", "20kPa"]],
    ),
    "owners": (
        ["小李负责回归测试，小王只负责发布。小陈是本次发言人，没有分配执行任务。"],
        [["小李"], ["小王"]],
    ),
    "proposal": (
        ["小赵建议周五上线。这只是建议，会议没有批准，上线日期尚未决定。"],
        [["未", "建议", "待"]],
    ),
    "date": (
        ["会议日期是2026年10月1日。周宁承诺在10月8日中午前提交原始报告，不是最终调查结论。"],
        [["周宁"], ["10月8", "十月八", "10-08"]],
    ),
    "negative": (
        ["发布只包括检索与导入，不包括自动回复。已决定取消自动回复的隐藏开关，不准随包发布。"],
        [["不", "取消"], ["自动回复"]],
    ),
    "condition": (
        [
            "只有连续五分钟错误率超过百分之一，才暂停扩量。回滚应用版本，不回滚客户已经成功写入的数据。"
        ],
        [["五分钟", "5分钟"], ["百分之一", "1%"], ["不回滚"]],
    ),
    "uncertain": (
        ["客户投诉单号听不清，可能是A17，也可能是A70。顾珊负责核实，现在不能确认任何一个号码。"],
        [["顾珊"], ["不", "待", "未"]],
    ),
    "conflict": (
        ["甲方认为合同价格含税。", "乙方认为合同价格不含税，双方暂未达成一致，留待核实。"],
        [["含税"], ["不含税"]],
    ),
    "tail": (["正式决定由陈宁在周四完成验收。\n\n"], [["陈宁"], ["周四"]]),
    "late": (
        [
            "我们先讨论是否需要增加验收，不作决定。",
            "讨论结束，正式决定增加验收，何静负责，十月十二日完成。",
        ],
        [["何静"], ["十月十二", "10月12", "10-12"]],
    ),
    "withdrawal": (["取消把自动摘要作为隐藏开关随包上线的提议。"], [["取消"], ["自动摘要"]]),
    "plain_assignment": (
        ["林洁在九月六日前向合作方发送升级说明。"],
        [["林洁"], ["九月六", "9月6"]],
    ),
    "duty": (["上线当天由余舟主值班，陈远备班。"], [["余舟"], ["陈远"]]),
    "plain_policy": (["体验客户使用原套餐，不单独收取试用费用。"], [["不"], ["费用"]]),
    "deferred": (["正式套餐是否调整价格留到下一次商业评审。"], [["下", "待", "未"]]),
    "mixed_state": (
        ["日志显示错误率为百分之一。建议下周把告警阈值调为百分之二，此建议尚未批准。"],
        [["百分之一", "1%"], ["百分之二", "2%"]],
    ),
    "speaker_schedule": (
        ["首次观察安排在十月十二日上午九点。"],
        [["十月十二", "10月12"], ["九点", "9点"]],
    ),
    "reaffirm_scope": (
        [
            "本轮不采购备用显示器。",
            "这也说明备用显示器不是当前必需采购项。若日后有校准显示需求，应由任务明确提出，而不是因为有人看到颜色不同就立刻加设备。今天不扩大这项范围。",
        ],
        [["备用显示器"], ["不采购", "不是", "不扩大"]],
    ),
    "deadline_scope": (
        ["客户投诉单号听不清。顾珊去核原工单，今天别猜。"],
        [["顾珊"], ["听不清", "未", "不确定"]],
    ),
    "explicit_today": (["顾珊今天完成单号核实。"], [["顾珊"], ["今天"]]),
    "first_person": (
        ["我准备培训材料时，会把操作错误画成流程图。", "我还会让学生实际走一遍接收流程。"],
        [["流程图"], ["接收流程"]],
    ),
    "quoted_revision": (
        [
            "样本量计划采集60份水样，由何静负责，交付期限为十月十八日。",
            "撤销原来“计划采集60份水样”的安排，最终确认采集48份水样，另设8份空白对照，对照不计入48份。仍由何静负责，交付期限仍为十月十八日。",
        ],
        [["48", "四十八"], ["8", "八"], ["何静"]],
    ),
    "withdrawn_draft": (
        [
            "先提一个草案，安全库存按四十五天覆盖，大家先讨论，不是最终批准。",
            "撤回刚开会四十五天的库存草案，按物料分层讨论，不搞一个天数覆盖所有物料。",
        ],
        [["撤回"]],
    ),
    "planned_verification": (
        [
            "换账号后导出文件可能回退到默认目录，尚待验证。",
            "明确一下，安排用两个测试账号分别导出，观察目录不可用时是否提示；测试尚未执行。",
        ],
        [["目录"], ["测试", "验证"]],
    ),
    "venue_cancel": (
        [
            "舞台建议设在北门草坪，场地审批尚未通过，这只是候选。",
            "北门草坪审批无法赶上，正式取消该候选，不再按那里设计。最终场地改为室内展厅。",
        ],
        [["室内展厅"]],
    ),
    "weather_reaffirm": (
        [
            "普通降雨不取消活动，严重天气由林洁组织评估。",
            "再次说明，严重天气影响交通时评估后决定，不是看到普通下雨就自动取消，也不是任何情况下都坚持举办。",
        ],
        [["林洁"], ["严重天气"]],
    ),
    "log_revision": (
        [
            "操作审计日志保留30天。",
            "调试日志保留14天。",
            "此前“操作审计日志保留30天”撤销，最终是操作审计日志保留60天，调试日志仍为14天。",
        ],
        [["60", "六十"], ["14", "十四"]],
    ),
    "implicit_task": (
        ["关键订单主键重复率必须为零，迟到率单独监控。林洁负责这项落实，交付到五月二日。"],
        [["重复率"], ["零", "0"], ["迟到率"], ["林洁"]],
    ),
    "field_batch": (
        [
            "评审改为五月九日，仍由林洁负责，交付期限仍为五月二日。上线当天由余舟主值班，陈远备班。韩晨去查台账，下午先别推测。"
        ],
        [["林洁"], ["五月二日"], ["余舟"], ["陈远"], ["韩晨"]],
    ),
    "clarification": (
        ["汇率使用预算版比较，实际汇率差异需保留来源。", "明确一下，预算假设不是未来汇率的保证。"],
        [["来源"], ["不是", "不"]],
    ),
    "unresolved_reaffirm": (
        [
            "试点企业具体名单待客户成功团队确认，目前还没有确定。",
            "正式确认本轮只做权限功能。试点企业具体名单仍没有确定，不能猜测。",
        ],
        [["名单"], ["客户成功"]],
    ),
    "conditional_executor": (
        ["回滚操作由我执行，但执行前必须得到周宁的确认，不能自己凭感觉切回去。"],
        [["顾岚"], ["周宁"], ["确认"]],
    ),
    "pending_schedule": (
        [
            "具体切换日期仍待最后确认；今天未同意跳过回退演练赶原计划。",
            "此前“切换原计划十月二十八日”撤销，最终确认切换日期为十一月二日，仍需先完成回退演练。",
        ],
        [["十一月二日"], ["回退演练"]],
    ),
    "routine_closing": (
        ["正式决定采购两台设备，林洁负责下单。今天散会。"],
        [["两台", "2台"], ["林洁"]],
    ),
}
SPEAKERS = {
    name: "顾岚"
    for name in (
        "withdrawal",
        "plain_assignment",
        "duty",
        "plain_policy",
        "deferred",
        "mixed_state",
        "speaker_schedule",
        "reaffirm_scope",
        "first_person",
        "conditional_executor",
    )
}


def verify(case, engine):
    active = engine.knowledge.summaries
    text = "\n".join(f.text + f.owner + f.due for f in active)
    checks = [any(option in text for option in alternatives) for alternatives in CASES[case][1]]
    errors = []
    if case == "pending_schedule" and any("仍待最后确认" in f.text for f in active):
        errors.append("正式日期已明确，但旧的待确认状态仍是现行事项")
    if (
        case == "routine_closing"
        and engine.knowledge.draft
        and any(
            "今天散会" in item.text
            for section in engine.knowledge.draft.sections
            for item in section.items
        )
    ):
        errors.append("例行散会话语占用了正文名额")
    if case == "conditional_executor" and not any(f.owner == "顾岚" for f in active):
        errors.append("明确第一人称执行人丢失，或将审批人当成执行人")
    if case == "unresolved_reaffirm" and not any(set(f.source_ids) == {"s1.0"} for f in active):
        errors.append("重申尚未确定被当成答案，旧未决事项错误关闭")
    if case == "venue_cancel" and any(set(f.source_ids) == {"s1.0"} for f in active):
        errors.append("已经取消的候选场地仍生效")
    if case == "weather_reaffirm" and not any(set(f.source_ids) == {"s1.0"} for f in active):
        errors.append("重申条件被当成取消，原分工丢失")
    if case == "log_revision":
        if any(set(f.source_ids) == {"s1.0"} for f in active):
            errors.append("被明确撤销的旧审计日志期限仍生效")
        if not any(set(f.source_ids) == {"s2.0"} for f in active):
            errors.append("未修改的调试日志被错误覆盖")
    if case == "implicit_task" and not any(
        f.owner == "林洁" and "重复率" in f.text for f in active
    ):
        errors.append("分工中的实际对象或要求丢失")
    if case == "planned_verification" and not any(set(f.source_ids) == {"s1.0"} for f in active):
        errors.append("核实计划被当成核实结果，错误关闭风险")
    if case == "field_batch":
        if not {"林洁", "余舟", "陈远", "韩晨"} <= {f.owner for f in active}:
            errors.append("第三人称点名分工被误删")
        if not any(f.owner == "林洁" and "五月二日" in f.due for f in active):
            errors.append("活动日期挤掉了明确交付期限")
        if any(f.owner == "韩晨" and f.due for f in active):
            errors.append("别推测的时间被当作查账期限")
    if case == "withdrawn_draft" and any(set(f.source_ids) == {"s1.0"} for f in active):
        errors.append("已撤回草案仍在现行清单中")
    if case == "clarification" and not any(set(f.source_ids) == {"s1.0"} for f in active):
        errors.append("补充说明错误覆盖了原有信息")
    if case == "deadline_scope" and any(f.owner == "顾珊" and f.due for f in active):
        errors.append("今天别猜被当作核实期限")
    if case in {"deadline_scope", "explicit_today"} and not any(f.owner == "顾珊" for f in active):
        errors.append("核实负责人的结构字段丢失")
    if case == "explicit_today" and not any(f.owner == "顾珊" and f.due == "今天" for f in active):
        errors.append("明确给出的今天完成期限被删除")
    if case == "first_person" and len([f for f in active if f.owner == "顾岚"]) < 2:
        errors.append("明确第一人称执行承诺未正确归属发言人")
    if case == "speaker_schedule" and any(f.owner for f in active):
        errors.append("仅报出时间的发言人被误写为负责人")
    if case == "reaffirm_scope" and any(f.status == "conflicted" for f in active):
        errors.append("重申不采购被误判为冲突")
    if case in {"withdrawal", "plain_policy"} and any(f.status != "confirmed" for f in active):
        errors.append("已经确定的取消或规则被降为提议／未决事项")
    if case == "plain_assignment" and not any(
        f.kind == "action" and f.status == "confirmed" and f.owner == "林洁" and f.due
        for f in active
    ):
        errors.append("直接分工的状态、负责人或期限不完整")
    if case == "duty" and not {"余舟", "陈远"} <= {
        f.owner for f in active if f.kind == "action" and f.status == "confirmed"
    }:
        errors.append("主值班与备班没有分开记录为已分配行动")
    if case == "deferred" and not any(
        f.kind == "question" and f.status == "unresolved" for f in active
    ):
        errors.append("推迟决定的问题被当成提议或已定事项")
    if case == "mixed_state" and not (
        any(f.status == "confirmed" and ("百分之一" in f.text or "1%" in f.text) for f in active)
        and any(f.kind == "proposal" and ("百分之二" in f.text or "2%" in f.text) for f in active)
    ):
        errors.append("同一发言内的事实与待批准建议没有分开")
    if case in {"amount", "unit", "late", "quoted_revision"} and any(
        set(f.source_ids) == {"s1.0"} for f in active
    ):
        errors.append("明确改口后旧事实仍处于现行状态")
    if case == "conflict" and not any(f.status == "conflicted" for f in active):
        errors.append("原文明示分歧，但账本未标记冲突")
    if not all(checks):
        errors.append("关键事实表达缺失")
    if case == "question" and any(f.kind == "question" or f.status == "unresolved" for f in active):
        errors.append("已回答的问题仍然未决")
    if case == "proposal" and any(
        f.kind in {"decision", "action"} and f.status == "confirmed" for f in active
    ):
        errors.append("建议被提升为决定或行动")
    if case == "proposal" and any(f.owner for f in active):
        errors.append("建议提出者被写成执行负责人")
    if case == "condition" and any(f.status == "proposed" for f in active):
        errors.append("执行规则被降为建议")
    if case == "uncertain":
        if any(f.status == "conflicted" for f in active):
            errors.append("互补的不确定信息和核实分工被判为冲突")
        if not any(f.kind == "action" and f.owner == "顾珊" for f in active):
            errors.append("核实分工缺失")
    if case == "owners":
        if any(f.owner == "小陈" for f in active):
            errors.append("把发言人当作负责人")
        if not {"小李", "小王"} <= {f.owner for f in active}:
            errors.append("行动负责人字段缺失")
    if case == "late" and any(f.status in {"proposed", "unresolved"} for f in active):
        errors.append("后续决定未关闭旧提议或未决事项")
    if case == "amount":
        for f in active:
            if ("十万元" in f.text.replace("二十万元", "") or "10万元" in f.text) and not any(
                w in f.text for w in ["作废", "改", "取消", "更正"]
            ):
                errors.append("旧金额仍为现行事实")
    if engine.stream.consumed != engine.stream.cursor:
        errors.append("原文未完整处理")
    if engine.knowledge.warnings:
        errors.append("出现降级")
    return errors


async def run_short(settings, names, emit=print):
    model = ResponsesClient(settings)
    failures = 0
    try:
        for name in names:
            diagnostics = []

            def observe(kind, data):
                if kind in {"model.validation_failed", "relation.rejected", "fields.corrected"} or (
                    kind == "model.completed" and data.get("task") in {"reconcile", "fields"}
                ):
                    diagnostics.append({"event": kind, **data})

            engine = MeetingEngine(Context(settings, model, observe), "短测 " + name, "2026-10-01")
            try:
                for index, text in enumerate(CASES[name][0]):
                    start = engine.stream.cursor
                    await engine.append(
                        TranscriptChunk(
                            chunk_id=f"u{index + 1}",
                            seq=index + 1,
                            text=text,
                            speaker=SPEAKERS.get(name),
                            start=start,
                            end=start + len(text),
                        )
                    )
                    # 明确跨窗口的反例，以小成本测试状态修订。
                    await engine.cycle("flush")
                await engine.finalize()
                errors = verify(name, engine)
            except Exception as exc:
                errors = [str(exc)]
            failures += bool(errors)
            emit(
                json.dumps(
                    {
                        "case": name,
                        "passed": not errors,
                        "errors": errors,
                        "stats": engine.context.stats,
                        "diagnostics": diagnostics,
                        "facts": [f.model_dump() for f in engine.knowledge.facts],
                        "minutes": engine.knowledge.draft.model_dump()
                        if engine.knowledge.draft
                        else None,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await model.close()
    return failures
