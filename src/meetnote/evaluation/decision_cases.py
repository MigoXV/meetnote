"""人工编写的短合成标注；expected 仅由评分器/显式模拟器读取。"""

from dataclasses import dataclass

from meetnote.domain.results import field_support_schema, relation_schema


@dataclass(frozen=True)
class Case:
    id: str
    task: str
    text: str
    claim: str
    field: str = ""
    value: str = ""
    expected: bool | str = False
    old: str = ""
    old_status: str = "confirmed"
    new_status: str = "confirmed"

    def payload(self):
        if self.task == "fields":
            claim = {
                "id": "f1",
                "text": self.claim,
                "kind": "action",
                "owner": "",
                "due": "",
                "owner_source_ids": [],
                "due_source_ids": [],
            }
            claim[self.field] = self.value
            claim[self.field + "_source_ids"] = ["s1.0"]
            return {
                "claims": [claim],
                "sources": [
                    {"id": "s1.0", "text": self.text, "speaker": "主持人", "timestamp": None}
                ],
            }, field_support_schema(["f1"])
        kinds = ["none", "supersedes", "retracts", "resolves", "conflicts"]
        pair = {
            "new_id": "f2",
            "old_id": "f1",
            "new_claim": self.claim,
            "old_claim": self.old,
            "new_status": self.new_status,
            "old_status": self.old_status,
            "evidence_ids": ["s2.0#0"],
        }
        sources = [
            {
                "id": "s1.0#0",
                "source_id": "s1.0",
                "text": self.old,
                "speaker": "主持人",
                "timestamp": None,
            },
            {
                "id": "s2.0#0",
                "source_id": "s2.0",
                "text": self.text,
                "speaker": "主持人",
                "timestamp": None,
            },
        ]
        return {
            "pairs": {"p0": pair},
            "allowed_pairs": [["f2", "f1"]],
            "new": [
                {"id": "f2", "text": self.claim, "status": self.new_status, "source_ids": ["s2.0"]}
            ],
            "candidates": [
                {"id": "f1", "text": self.old, "status": self.old_status, "source_ids": ["s1.0"]}
            ],
            "sources": sources,
        }, relation_schema({"p0": ["s2.0#0"]}, {"p0": kinds})


FIELDS = [
    (
        "owner_yes",
        "小李负责回归测试，小王负责发布。",
        "回归测试由小李负责。",
        "owner",
        "小李",
        True,
    ),
    (
        "owner_wrong",
        "小李负责回归测试，小王只负责发布。",
        "回归测试由小王负责。",
        "owner",
        "小王",
        False,
    ),
    (
        "speaker_only",
        "小陈主持讨论，回归测试尚未分配。",
        "回归测试由小陈负责。",
        "owner",
        "小陈",
        False,
    ),
    (
        "proposal_owner",
        "小赵建议增加压测，会议没有批准，也没有安排小赵执行。",
        "增加压测由小赵负责。",
        "owner",
        "小赵",
        False,
    ),
    ("backup_yes", "韩晨主值班，林洁备班。", "备班由林洁负责。", "owner", "林洁", True),
    ("backup_wrong", "韩晨主值班，林洁备班。", "主值班由林洁负责。", "owner", "林洁", False),
    (
        "reaffirm_owner",
        "评审改为五月九日，仍由林洁负责。",
        "评审由林洁负责。",
        "owner",
        "林洁",
        True,
    ),
    (
        "conditional_owner",
        "获批后由周宁负责发布，负责人已经确定，审批尚未通过。",
        "获批后发布由周宁负责。",
        "owner",
        "周宁",
        True,
    ),
    (
        "contact_only",
        "顾珊是联系窗口，执行核查的人还没确定。",
        "执行核查由顾珊负责。",
        "owner",
        "顾珊",
        False,
    ),
    (
        "negative_owner",
        "今天确认小李不负责发布，小王负责。",
        "发布由小李负责。",
        "owner",
        "小李",
        False,
    ),
    (
        "named_action",
        "周宁核对原始报告，何静检查样本标签。",
        "核对原始报告由周宁执行。",
        "owner",
        "周宁",
        True,
    ),
    (
        "quoted_owner",
        "旧稿写小李负责，但旧稿作废，现在改由小王执行。",
        "执行工作由小李负责。",
        "owner",
        "小李",
        False,
    ),
    ("due_yes", "顾珊今天完成工单核实。", "核实工单。", "due", "今天", True),
    ("due_discussion", "工单编号不清，顾珊去核实，今天别猜。", "核实工单。", "due", "今天", False),
    ("due_date", "周宁在十月八日前交付原始报告。", "交付原始报告。", "due", "十月八日", True),
    ("due_other", "小李周五完成测试，小王周六发布。", "完成测试。", "due", "周六", False),
    (
        "due_reaffirm",
        "评审改为五月九日，材料交付期限仍为五月二日。",
        "交付评审材料。",
        "due",
        "五月二日",
        True,
    ),
    (
        "due_event",
        "首次观察安排在十月十二日上午九点。",
        "开展首次观察。",
        "due",
        "十月十二日上午九点",
        True,
    ),
    (
        "due_history",
        "上周五的报告已经归档，新报告交付日期尚未确定。",
        "交付新报告。",
        "due",
        "上周五",
        False,
    ),
    ("due_duration", "测试持续三小时，然后停止。", "执行测试。", "due", "三小时", True),
    (
        "due_negated",
        "明确一下，不要求周五完成测试，日期另外通知。",
        "完成测试。",
        "due",
        "周五",
        False,
    ),
    ("due_confirmation", "今天确认了分工，完成日期还没定。", "完成任务。", "due", "今天", False),
    ("due_before", "请何静在周二下午之前完成标签复核。", "完成标签复核。", "due", "周二下午", True),
    ("due_proposal", "有人建议周五发布，但日期未批准。", "发布。", "due", "周五", False),
]
RELATIONS = [
    (
        "budget_revision",
        "预算十万元。",
        "采购预算正式改为二十万元，十万元作废。",
        "采购预算改为二十万元。",
        "supersedes",
        "confirmed",
        "confirmed",
    ),
    (
        "unit_revision",
        "压差要求二十兆帕。",
        "更正，压差要求是二十千帕，不是二十兆帕。",
        "压差要求改为二十千帕。",
        "supersedes",
        "confirmed",
        "confirmed",
    ),
    (
        "cancel_candidate",
        "舞台建议设在北门草坪。",
        "正式取消北门草坪候选，不再按那里设计。",
        "取消北门草坪候选。",
        "retracts",
        "proposed",
        "confirmed",
    ),
    (
        "cancel_draft",
        "安全库存建议覆盖四十五天。",
        "撤回四十五天库存草案。",
        "撤回四十五天库存草案。",
        "retracts",
        "proposed",
        "confirmed",
    ),
    (
        "question_answer",
        "采购报价是否含税？",
        "财务确认采购报价含税，这个问题已经解决。",
        "采购报价含税。",
        "resolves",
        "unresolved",
        "confirmed",
    ),
    (
        "problem_resolved",
        "导出失败原因尚未确认。",
        "已经查明导出失败原因是权限缺失，问题原因已确认。",
        "导出失败原因是权限缺失。",
        "resolves",
        "unresolved",
        "confirmed",
    ),
    (
        "explicit_conflict",
        "财务确认本轮预算为十万元。",
        "采购方坚持本轮预算为二十万元，与财务十万元口径冲突，会议尚未裁定。",
        "本轮预算为二十万元，与财务口径冲突。",
        "conflicts",
        "confirmed",
        "unresolved",
    ),
    (
        "scope_not_revision",
        "采购数量为十台。",
        "更正，采购预算改为二十万元，设备数量保持十台。",
        "采购预算改为二十万元。",
        "none",
        "confirmed",
        "confirmed",
    ),
    (
        "proposal_not_revision",
        "当前告警阈值为百分之一。",
        "建议把告警阈值改为百分之二，此建议尚未批准。",
        "建议阈值改为百分之二。",
        "none",
        "confirmed",
        "proposed",
    ),
    (
        "verification_not_answer",
        "导出目录是否回退尚未验证。",
        "安排两个账号验证导出目录，尚未执行，不能确认是否回退。",
        "安排验证导出目录。",
        "none",
        "unresolved",
        "confirmed",
    ),
    (
        "negated_cancellation",
        "本轮保留手动回复。",
        "明确说，不撤销手动回复，撤销的是自动回复。",
        "撤销自动回复。",
        "none",
        "confirmed",
        "confirmed",
    ),
    (
        "different_subject",
        "小李负责测试。",
        "更正，小王的发布安排取消，小李仍负责测试。",
        "取消小王发布安排。",
        "none",
        "confirmed",
        "confirmed",
    ),
]
CASES = [
    Case(i, "fields", text, claim, field, value, expected)
    for i, text, claim, field, value, expected in FIELDS
]
CASES += [
    Case(
        i,
        "reconcile",
        text,
        claim,
        expected=expected,
        old=old,
        old_status=old_status,
        new_status=new_status,
    )
    for i, old, text, claim, expected, old_status, new_status in RELATIONS
]
