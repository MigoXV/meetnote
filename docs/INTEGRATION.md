# BF16 双模型联调

目标是验证真实会议流程可运行，不是精度或速度对照。主模型继续使用现有 spark25 Responses 服务；字段、关系和证据选择由 qwen-decider BF16 完成，禁止回退。

## 启动

在 `/workspace/opusi/qwen-decider` 的现有 Poetry 环境启动决策服务，等待 ready：

```bash
CUDA_VISIBLE_DEVICES=0 VLLM_WORKER_MULTIPROC_METHOD=spawn HF_HUB_OFFLINE=1 \
poetry run qwen-decider serve --model-path model-bin/decider-v3-bf16 --precision bf16 --port 10003
```

在 MeetNote 仓库构建页面并启动独立实例：

```bash
pnpm --dir src/web run build
MEETNOTE_PROVIDER=responses MEETNOTE_DECISION_BACKEND=laya \
MEETNOTE_LAYA_URL=http://127.0.0.1:10003 MEETNOTE_LAYA_FALLBACK=false \
poetry run meetnote serve --port 8012
```

`/api/v1/info` 返回决策后端、回退开关和启动时读取的模型信息；启动时要求决策服务已就绪且为 BF16 vLLM。每次决策仍检查响应指纹，防止请求期间模型被切换。历史配置名 `laya` 保留，不表示运行的是另一份模型。

## 验证

```bash
MEETNOTE_PROVIDER=responses MEETNOTE_DECISION_BACKEND=laya \
MEETNOTE_LAYA_URL=http://127.0.0.1:10003 MEETNOTE_LAYA_FALLBACK=false \
poetry run meetnote check-integration --url http://127.0.0.1:8012

pnpm --dir src/web exec playwright test --config playwright.integration.config.ts
```

第一条命令先验证六类短流程，全部通过后，使用 HTTP 创建会话、追加 `product_clean` 的 24 条发言、订阅 SSE、finalize、检查快照并释放会话。第二条从真实页面回放同一会议。页面测试连接独立实例，不自动启动 mock；不要与其他会话测试同时运行。

验收要求：两个模型均实际调用，零回退、零错误、零降级警告；全部发言完成消费，剩余原文为零；纪要和事实非空、引用有效；SSE 与页面正常展示结果，释放后服务仍存活。

会议输入、事件及结果只保留在内存。终端仅输出汇总，禁止将完整事件或会议结果重定向到文件。真实页面测试关闭 screenshot、video、trace 和失败时的页面快照。

## 接口边界

- 每道题 2–10 个选项；证据选择中的“无依据”占一个选项，最多 9 条候选证据。
- 关系判断只携带当前新旧事实相关原文，保持证据 ID，不读取同批其他事实的上下文。
- 模型完整输入上限 2048 token；超限返回明确错误，不截断原文或候选。
- 不确定、无依据、服务错误与模型指纹变化均显式暴露。跑通不依赖静默回退或强制选择。
- 参考标注不进入模型输入；本轮不运行 evaluate-decisions、compare-decisions 或 INT8 测试。

## 本次执行记录

2026-10-02：六类真实短流程通过，负责人、期限、建议未批准、金额修订、问题解决与撤销均经过原有 MeetingEngine。

首次完整 HTTP 回放实际完成：24 段全部处理、剩余原文为零，主模型 6 次请求、决策模型 29 次请求，无错误、回退或降级。验收脚本最初误按 `event:` 行统计事件，导致 `missing_sse_events`；已按项目原有 `data` JSON 协议修正，并补充回归测试。未修改服务端 SSE 协议。

修正后完整 HTTP 复验通过：SSE 正常收到输入处理、事实更新、纪要完成和会话结束事件；24 段全部消费、无剩余原文、最终无错误或降级、零回退。主模型 13 次请求，决策模型 32 次请求。过程中出现抽取校验失败和原引擎重试，最终恢复；这不是无重试运行，也不据此宣称精度或速度改善。

离线相关测试共 123 项通过，Ruff 和真实页面测试配置的 Prettier 检查通过，前端构建通过。

真实 Playwright 页面验收通过（1 项）：选择 product_clean 完整回放，最终纪要可见，24 段均处理完成、剩余原文为零、无最终错误或降级警告、零回退、无前端运行异常；会话释放后列表为空。页面运行中的抽取校验重试仍由原引擎处理。测试没有记录会议截图、录像或 trace。

PyTorch 2.9.1、vLLM 0.14.1、Transformers 4.57.6 保持原版本。此次结论仅为指定场景联调通过，不代表全部语料质量验收或性能提升。
