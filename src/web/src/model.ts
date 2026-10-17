import type { Fact, Snapshot } from "./api";
export const kindNames: Record<string, string> = {
  decision: "决定",
  action: "行动项",
  question: "待确认问题",
  risk: "风险",
  constraint: "约束",
  proposal: "提议",
  fact: "其他信息",
};
export const factStatus: Record<string, string> = {
  confirmed: "会议表述明确",
  proposed: "提议",
  unresolved: "待确认",
  conflicted: "存在冲突",
  superseded: "已被取代",
  retracted: "已撤销",
  resolved: "问题已解决",
};
export const currentFacts = (facts: Fact[]) =>
  facts.filter(
    (f) => !["superseded", "retracted", "resolved"].includes(f.status),
  );
export const pendingFacts = (facts: Fact[]) =>
  facts.filter((f) => ["unresolved", "conflicted"].includes(f.status));
export const charCount = (text: string) => Array.from(text).length;
export function exportMarkdown(snapshot: Snapshot, evidence: boolean) {
  if (!snapshot.minutes) return "";
  const pending = pendingFacts(currentFacts(snapshot.facts)).length;
  const heading =
    snapshot.status === "completed" ? "最终纪要" : "阶段纪要（尚未完成收尾）";
  return (
    `# ${heading}\n\n${snapshot.minutes.markdown}\n\n---\n待确认事项：${pending} 项。\n${evidence ? "附录包含引用原文。" : "不包含原文依据。"}\n` +
    (evidence
      ? snapshot.sources
          .filter((s) =>
            snapshot.facts.some(
              (f) =>
                snapshot.minutes!.evidence_trace.includes(f.id) &&
                f.source_ids.includes(s.id),
            ),
          )
          .map((s) => `\n## 依据 ${s.id}\n\n${s.text}\n`)
          .join("")
      : "")
  );
}
