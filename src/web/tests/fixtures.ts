import type { Fact, Snapshot } from "../src/api";
const words = [
  ["陈屿", "这次先上线会议纪要和转写，自动任务分发暂不进入这次范围。"],
  ["林岚", "我负责转写页联调，原计划周五完成验收，下周一发布。"],
  ["陈屿", "时间改一下，周四完成验收，周五再发布，仍由林岚负责。"],
  ["林岚", "好的，按周四验收、周五发布执行。"],
  ["周宁", "导出的 Markdown 我来做，周四中午之前交付。"],
  ["陈屿", "数据保留多久还没定，先不要写成已确定方案。"],
  ["林岚", "离线部署可不可行，需要压测后再确认。"],
  ["周宁", "如果接入外部模型，原文是否出网需要单独确认。"],
  ["陈屿", "还需要补一次移动端布局检查，负责人和截止时间会后再定。"],
  ["陈屿", "结论就是，本次范围按刚才说的纪要和转写执行，自动任务分发不做。"],
];
const makeFact = (
  id: string,
  text: string,
  kind: string,
  refs: number[],
  owner = "",
  due = "",
  status = "confirmed",
): Fact => ({
  id,
  text,
  kind,
  source_ids: refs.map((i) => `s${i}`),
  owner,
  due,
  status,
  topic: "本次上线",
  subject: text,
  revision: 1,
  related_ids: [],
});
export function demoSnapshot(): Snapshot {
  const facts = [
    makeFact(
      "f1",
      "本次上线会议纪要和转写；自动任务分发不在范围内。",
      "decision",
      [1, 10],
    ),
    makeFact(
      "f2",
      "完成转写页联调、验收与发布。",
      "action",
      [3, 4],
      "林岚",
      "周四验收，周五发布",
    ),
    makeFact(
      "f3",
      "交付 Markdown 导出。",
      "action",
      [5],
      "周宁",
      "周四中午之前",
    ),
    makeFact("f4", "补一次移动端布局检查。", "action", [9]),
    makeFact(
      "f5",
      "产品数据保留时长尚未确定。",
      "question",
      [6],
      "",
      "",
      "unresolved",
    ),
    makeFact(
      "f6",
      "离线部署可行性需压测后确认。",
      "question",
      [7],
      "",
      "",
      "unresolved",
    ),
    makeFact(
      "f7",
      "如果接入外部模型，原文是否出网需确认。",
      "question",
      [8],
      "",
      "",
      "unresolved",
    ),
  ];
  const before = makeFact(
    "f2",
    "周五完成验收，下周一发布。",
    "action",
    [2],
    "林岚",
    "周五验收，下周一发布",
  );
  return {
    id: "demo",
    title: "栖叶协作产品周会（演示数据）",
    status: "running",
    accepted_count: 10,
    processed_count: 10,
    remaining_chars: 0,
    queue_size: 0,
    error: null,
    chunks: words.map(([speaker, text], i) => ({
      chunk_id: `c${i + 1}`,
      seq: i + 1,
      speaker,
      text,
    })),
    facts,
    sources: words.map(([, text], i) => ({
      id: `s${i + 1}`,
      chunk_id: `c${i + 1}`,
      text,
      disposition: "processed",
    })),
    revisions: [
      {
        fact_id: "f2",
        revision: 2,
        before,
        after: { ...facts[1], revision: 2 },
        reason: "片段 03 改口，片段 04 确认新安排。",
        source_ids: ["s2", "s3", "s4"],
      },
    ],
    stats: {},
    contract_version: 2,
    meeting_date: "",
    minutes: {
      title: "栖叶协作产品周会",
      markdown:
        "# 栖叶协作产品周会\n\n## 结论\n\n本次上线纪要与转写，不做自动任务分发。\n\n## 行动项\n\n林岚负责转写页联调，周四验收、周五发布。周宁负责 Markdown 导出，周四中午前交付。移动端检查未指定负责人，期限未明确。\n\n## 待确认\n\n产品保留策略、离线部署可行性和外部模型数据流向尚待确认。\n\n## 完整事项清单\n\n所有事项均保留原文依据。",
      narrative: {
        overview: {
          text: "上线范围为纪要与转写。验收调整为周四、发布为周五；产品保留策略与部署问题待确认。",
          fact_ids: ["f1", "f2", "f5", "f6", "f7"],
        },
      },
      evidence_trace: facts.map((f) => f.id),
      delivery_warnings: [],
    },
    warnings: [],
    event_cursor: 0,
  };
}
export function stressSnapshot(): Snapshot {
  const s = demoSnapshot();
  const subjects = [
    "输入边界与重试",
    "中文长句与标点",
    "外部模型数据边界",
    "移动端可读性",
    "分工与期限证据",
    "成本与限流预算",
    "部署压测方案",
    "发布回滚条件",
  ];
  s.chunks = Array.from({ length: 160 }, (_, i) => {
    const subject = subjects[i % subjects.length];
    const cycle = Math.floor(i / subjects.length) + 1;
    return {
      chunk_id: `stress-${i}`,
      seq: i + 1,
      speaker: i % 3 ? "林岚" : "陈屿",
      text: `第 ${cycle} 轮议题：${subject}。本轮讨论对象为第 ${i + 1} 组输入，候选预算为 ${200 + i * 7} 个字符，负载边界为 ${cycle * 12} 个并发请求。\n\n需分别记录前置条件、观测结果和未确认假设。如果压测尚未完成，不能将目标写成已达到的结论；若某个片段重试失败，则保留原文并明确影响范围。对比上一轮的 ${190 + i * 5} 个字符预算，本轮先验证边界连续性，避免因为重叠的关键词而合并两个不同事项。\n\n本轮的复核问题是：${subject}是否在设备宽度、输入法、重连与服务重启等条件下保持一致？测试负责人和完成时间仍需另行明确，不能从发言人推断责任归属。记录编号 ${i + 1} 仅用于这个布局测试，不是生产模型结果。`,
    };
  });
  s.sources = s.chunks.map((c, i) => ({
    id: `stress-source-${i}`,
    chunk_id: c.chunk_id,
    text: c.text,
    disposition: "processed",
  }));
  s.facts = Array.from({ length: 64 }, (_, i) =>
    makeFact(
      `stress-fact-${i}`,
      `${subjects[i % subjects.length]}：第 ${i + 1} 项的边界、例外及回退安排需要保持可核对；由服务端引用提供依据，不将目标当作结果。`,
      i % 3 ? "action" : "question",
      [],
      i % 4 ? "林岚" : "",
      i % 5 ? "待明确" : "",
      i % 3 ? "confirmed" : "unresolved",
    ),
  ).map((f, i) => ({
    ...f,
    source_ids: [s.sources[i].id, s.sources[i + 1].id],
  }));
  s.accepted_count = s.processed_count = s.chunks.length;
  return s;
}
