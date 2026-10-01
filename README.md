# MeetNote

会议摘要 sidecar。接收定稿转写，运行十三节点摘要链路，通过 HTTP 和 SSE 返回摘要及纪要。所有会话仅保存在内存。

Web 页面是调试工作台：回放合成会议、手工提交文本、查看原文和摘要依据、观察节点与模型调用、收尾生成纪要。采用苍渊·白垣主题，默认苍渊。

## 安装与运行

开发环境需要 Python 3.10、Poetry、Node.js 22.12+（当前使用 Node.js 24）和 pnpm。

```bash
cd /workspace/sidecars/meetnote
poetry env use python3.10
poetry install
pnpm --dir src/web install
pnpm --dir src/web run build
poetry run meetnote serve
```

打开 `http://127.0.0.1:8010`，接口文档位于 `/docs`。后端统一托管 `src/web/dist`，这是标准启动方式；部署目录必须包含该构建产物，缺失时启动明确失败。源码检出目录是完整工作台的部署单位；`poetry build` 生成的 Python 包包含引擎、API 与 Prompt，不包含前端构建资源。

默认连接 `http://192.168.0.222:10001/v1/responses`，模型为 `spark25`。每次调用都发送 `store=false`，不依赖服务端历史。上游服务的运行日志由其自身管理。

无模型接线检查：

```bash
poetry run meetnote serve --mock
```

mock 输出有明确标记，只用于验证接线，不能衡量摘要质量。VS Code 的「MeetNote 服务」会先执行 `web: build`，所有调试入口读取 `.env`。

## 配置

可复制 `.env.example` 为 `.env`；配置优先级为显式构造参数、环境变量、`.env`、默认值。`--mock` 显式覆盖模型类型，主机及端口由 CLI 参数或对应环境变量设置。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MEETNOTE_PROVIDER` | `responses` | `responses` 或显式 `mock` |
| `MEETNOTE_BASE_URL` | `http://192.168.0.222:10001/v1` | Responses API 根地址 |
| `MEETNOTE_MODEL` | `spark25` | 模型 ID |
| `MEETNOTE_API_KEY` | `local` | 请求凭据，仅留在服务端 |
| `MEETNOTE_THINKING` | `false` | 目标服务的 `enable_thinking` |
| `MEETNOTE_MODEL_TIMEOUT` | `240` | 单次模型请求超时，秒 |
| `MEETNOTE_MODEL_CONCURRENCY` | `4` | 全进程模型并发上限 |
| `MEETNOTE_MAX_OUTPUT_TOKENS` | `4096` | 每次模型输出上限 |
| `MEETNOTE_MIN_CHARS` | `50` | 普通检查点最小字数 |
| `MEETNOTE_WINDOW_CHARS` | `700` | 每轮最大原文窗口 |
| `MEETNOTE_CHECKPOINT_SECONDS` | `9` | 普通检查点间隔，秒 |
| `MEETNOTE_MAX_SESSIONS` | `32` | 内存会话上限，含短期保留的终态 |
| `MEETNOTE_QUEUE_SIZE` | `256` | 每会话等待消费的片段上限 |
| `MEETNOTE_MAX_SESSION_CHARS` | `200000` | 每会话累计输入字数上限 |
| `MEETNOTE_IDLE_SECONDS` | `1800` | 运行会话无新输入的取消期限 |
| `MEETNOTE_RETENTION_SECONDS` | `300` | 终态会话保留时间 |
| `MEETNOTE_EVENT_COUNT` / `MEETNOTE_EVENT_BYTES` | `1024` / `4194304` | 每会话事件缓存双重上限 |
| `MEETNOTE_CORPUS_DIR` | `/workspace/model-bin/meetnote` | 只读合成语料目录 |
| `MEETNOTE_HOST` / `MEETNOTE_PORT` | `127.0.0.1` / `8010` | CLI 监听地址 |

首版按单进程 sidecar 运行，没有账号、数据库、跨进程会话共享或 ASR 中间结果修订。由宿主处理用户管理及最终结果保存。

## 接口与生命周期

| 方法及路径 | 行为 |
| --- | --- |
| `POST /api/v1/sessions` | 创建会话，JSON 为 `{"title":"产品周会"}` |
| `GET /api/v1/sessions` | 列出当前内存会话 |
| `GET /api/v1/sessions/{id}` | 取得原文、正式摘要、规划、纪要及处理状态 |
| `POST /api/v1/sessions/{id}/chunks` | 追加定稿文本，返回 202 和确认序号 |
| `POST /api/v1/sessions/{id}/finalize` | 停止接受新输入，排空队列并生成纪要 |
| `GET /api/v1/sessions/{id}/events` | SSE 事件流，支持 `Last-Event-ID` |
| `DELETE /api/v1/sessions/{id}` | 取消任务并释放会话 |
| `GET /api/v1/samples` | 列出合成转写版本 |
| `GET /api/v1/samples/{id}` | 取得一份转写，不返回参考标注 |
| `GET /api/v1/info` | 模型类型、节点列表及内存保留规则 |
| `GET /health/live` | 进程存活检查，不表示上游模型可用 |

追加请求示例：

```json
{
  "chunk_id": "segment-001",
  "text": "时间改一下，周四完成验收，周五发布。",
  "speaker": "陈宁",
  "timestamp": "2026-09-01T10:03:00"
}
```

只有 `text` 必填，每次最多 16000 字。可选 `chunk_id` 用于幂等提交：相同 ID、相同内容返回原序号；内容、说话人或时间冲突返回 409。没有 ID 时由服务生成。片段顺序以服务成功接收顺序为准，`timestamp` 是参考信息。已确认的重复片段在收尾后仍能取得原确认，不视作新输入。

生命周期为 `running → finalizing → completed`，另有 `failed`、`cancelled`。收尾与入队共用临界区，收尾后新输入返回 409；队列或会话达到容量时返回 429，累计文本超过上限返回 413。接受成功不等于摘要完成，需等待处理结果或终态事件。

摘要边界无法定位时原文保持不变；最终刷新无进展时会话进入 `failed`，已接受原文与已有摘要仍可在保留期内查询。空会话可以完成，但没有纪要。模型超时、失败和输出截断不会被当作成功摘要。Marker、Lens、规划和成文有显式规则降级，事件或纪要说明中会标明。

### SSE

SSE 使用默认 `message` 事件，JSON 中的 `type` 表达语义：

```text
id: 42
data: {"id":42,"type":"summary.committed","data":{"id":"summary_0001","summary":"…","chunk_ids":["segment-001"],"start":0,"end":24}}

```

主要类型：`session.snapshot`、`input.accepted`、`input.processed`、`node.started`、`node.completed`、`node.skipped`、`node.degraded`、`node.failed`、`scheduler.updated`、`model.started`、`model.completed`、`summary.committed`、`summary.rejected`、`plan.updated`、`minutes.completed`、`session.terminal`、`session.cancelled`。

第一次连接、游标过期或无效的未来游标会收到完整快照；历史事件在缓存范围内时从游标之后补发。终态连接返回最终快照后关闭。每 15 秒发送心跳。单条调试事件超过字节上限时只保留截断标记，业务结果仍通过会话快照查询。没有为每个订阅者建立无界队列。

服务只向标准输出记录操作元数据和错误类型，不自动保存原文、Prompt、模型响应或纪要；调试详情留在有界内存事件中。CLI `replay` 明确将结果输出到标准输出。浏览器不使用本地存储保存会话。

## 十三节点与代码组织

| 节点 | 职责 |
| --- | --- |
| `bucket_accumulator` | 追加原文及稳定位置 |
| `cycle_slice_builder` | 按字数及时间形成处理窗口 |
| `fallback_guard` | 标明窗口是否可处理 |
| `llm_signal_marker` | 提取主题、ASR 风险、缺失信息和提交信号 |
| `commit_debt_meter` | 累计信号压力，阈值为 3 |
| `work_candidate_builder` | 根据压力与已提交摘要选择候选任务 |
| `model_work_scheduler` | 摘要优先，空闲轮次处理 Lens 和结构提示 |
| `meeting_lens_builder` | 判断会议组织方向及不稳定部分 |
| `template_planner` | 提供轻量章节与证据提示 |
| `summary_worker` | 生成候选摘要与原文边界 |
| `bucket_enricher` | 核对边界、去重并正式提交摘要与证据 |
| `main_minutes_planner` | 结合当前证据与上一轮计划更新纪要规划 |
| `minutes_composer` | 收尾时根据完整已提交证据生成纪要 |

所有入口都调用同一 `MeetingEngine`，通过原生 `async/await` 顺序执行上述 13 个节点。统一执行器负责状态合并、事件和计时；每步成功立即保存，后续失败或取消时保留已完成的更新。会话消费者是引擎状态的唯一写入者，输入接收队列与模型调用分离。Stream、Cycle、Schedule、Knowledge 是四组职责明确的状态，节点返回类型明确的 `StatePatch`：省略字段表示保留，显式 `None` 表示清空，空更新表示跳过。事件日志位于引擎状态之外。

`domain` 定义契约；`engine` 承载业务节点及纯规则；`llm` 只处理 Responses 传输与结构校验；`sessions` 管理内存生命周期；`web` 适配 HTTP/SSE；`commands`、`corpus` 负责命令和只读语料。各模型任务的指令放在独立 Prompt 资源中，Schema 由 Pydantic 字段定义生成。

保留原型的债务、Lens 与规划反馈机制，同时修复旧实现中跨入口执行逻辑重复、收尾后仍接受文本、证据包含窗口之外片段、关键词相同导致不同事实被去重等问题。当前首版保守要求原文边界唯一，无法确认时不消费文本。

## 合成语料与回放

语料由子智能体编写，位于 `/workspace/model-bin/meetnote`，不提交到代码仓库。共 8 场会议、24 份转写：`clean`、`colloquial`、`asr_noisy`。口语版包含改口、重复、插话、半句和话题返回；噪声版覆盖同音、人名、术语、数字、漏词、否定词、重复、断句、单位及说话人误归属错误。人物与组织均为虚构。

`transcripts.jsonl` 每行是一份会议转写，包含元数据和有序 `segments`；`annotations.jsonl` 独立保存参考事实、决定、行动项、未决问题、证据及错误跨度，应用输入不读取它。`manifest.json` 提供数量、长度和 SHA-256，数据目录内另有中文说明。

```bash
poetry run meetnote validate-corpus
poetry run meetnote replay product_clean --mock
poetry run meetnote replay product_asr_noisy
```

页面按定稿片段逐段提交，1× 约为 12 字/秒，也可暂停、继续、加速或立即提交。回放结束会自动收尾。评估时按证据和关键事实对照参考标注，不要求摘要措辞一致；不可从噪声恢复的信息不能按理想版本强行补全。

## 检查

```bash
poetry run ruff check src/meetnote tests
poetry run pytest -q
pnpm --dir src/web run lint
pnpm --dir src/web run build
pnpm --dir src/web exec playwright install chromium
pnpm --dir src/web exec playwright test
poetry build
```

后端测试不依赖模型服务或外部语料。浏览器检查需要合成语料，会自动启动 8011 端口的 mock 服务，覆盖样本回放、手工输入、暂停继续、收尾、释放、双主题及窄屏。截图作为临时检查产物存放在 `/tmp`。

真实模型检查与常规测试分开进行。模型耗时、表达质量和上游容量会影响实时体验；一次结构化接口连通不代表整条业务链路通过，需同时核对原文消费、摘要、纪要和降级说明。

### 本次本地验收（2026-10-01）

- 全部 24 份样本通过 mock 引擎完整回放，原文全部消费并生成模拟纪要。
- `spark25` 完整回放 `product_clean`：9 条摘要，纪要 2198 字符，约 257 秒，无降级，剩余原文为 0。
- `spark25` 完整回放 `product_asr_noisy`：19 条摘要，纪要 2089 字符，约 245 秒，无降级，剩余原文为 0。
- 两次真实运行验证了端到端接线和最终交付，不代表对所有摘要事实的准确率评测；首版未设准确率或实时延迟 SLA。
