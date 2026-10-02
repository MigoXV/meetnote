# MeetNote

MeetNote 是仅存内存的会议纪要 sidecar。接收定稿转写，持续抽取带原文证据的事实、决定、行动和未决事项，收尾后交付简明正文与完整事项清单。HTTP、CLI 与评估入口共用 `MeetingEngine`。

## 安装与启动

需要 Python 3.10、Poetry、Node.js 22.12+ 和 pnpm。

```bash
poetry install
pnpm --dir src/web install
pnpm --dir src/web run build
poetry run meetnote serve
```

访问 `http://127.0.0.1:8010`，API 文档位于 `/docs`。后端托管 `src/web/dist`；缺少构建产物时明确启动失败。VS Code 服务调试会先构建前端。`poetry build` 的 Python 包包含引擎和 Prompt，不包含前端构建产物。

默认调用 `http://192.168.0.222:10001/v1/responses`，服务模型名为 `spark25`，使用流式 Responses、严格 JSON Schema、`store=false`、`enable_thinking=false`。不依赖服务端对话历史；`store=false` 不代表关闭前缀缓存。

```bash
poetry run meetnote serve --mock
```

mock 只验证接线，输出带“模拟”标记，不能用于证明事实准确率。工作台沿用苍渊·白垣双主题，浏览器不保存会话。

## 处理机制

1. 优先保留完整发言，超预算时按句子边界建立稳定子片段 ID，保留原文、说话人、时间与字符位置；超长片段按 tokenizer 预算切分。
2. 抽取输出以本批每个片段 ID 为必填键，逐段填写事实或忽略依据，再汇入账本。模型只选一次语义分类，程序展开类型和状态，避免生成“已确认的风险”等非法组合。负责人和期限有独立证据引用；执行决定和执行约束也可有明示负责人。未闭合内容可以进入未决状态。代词分工只在引用原文中有唯一且不在引号内的对应分工句时，补入其紧邻前句，逐字保留要求、数字与条件；不能用模型生成的简短主题替代任务内容。
3. 校验每段处理归属、证据范围和状态后，先由程序验证可逐字对应的第一人称执行分工；其余非空负责人和期限以每次最多 6 条的小任务独立核验是否确实属于该事项；不支持的字段清空并发布 `fields.corrected` 事件，原文及事实正文保留。核验请求失败不提交。全部通过后原子提交事实与处理位置。纯空白由程序处理；失败不越过原文，取消不提交候选结果。
4. 从内存索引召回同对象事实，相似度仅用于召回，不用于删除事实；按预算且每次最多六个候选对核对修订、撤销、问题关闭和冲突。程序建立原文证据句 ID，Schema 限定每对可选的证据和操作，模型不复制原句或事实 ID。每对新旧命题并排提供，带旧安排引文时限定匹配范围，避免相邻对象的相同数字互相覆盖。重复确认的候选事实仅提供首次与最近证据用于比对，完整引用仍在账本。关键词重合不能删除信息；只有精确命题及字段匹配，或共享证据的逐字同义标签扩展，才合并引用。修订保留历史，冲突不静默择一；没有明确修订／分歧原文依据时原事实并存。
5. 工作台展示当前事实与修订记录。收尾先为修订后的现行安排保留名额，决定、行动、未决事项和约束轮流分配正文名额；有负责人和期限的行动优先，背景事实只用剩余额度。每类按请求预算且每页最多 24 条分页、逐层保留类型选择重点，再按议题组织正文。正文最多 18 条要点；完整事项清单不受此限制。
6. 正文模型只选择事实 ID，不二次改写事实或状态；正文和完整事项清单都从账本渲染为 Markdown。成文失败可以降级为事实列表；抽取失败不能伪装为完整成功。

没有新增输入时不重复抽取，也不重复累计调度分数。旧十三节点、Marker、Lens、债务和复制文本锚点机制已移除。
结构或语义校验重试后仍失败时，有界拆小当前批次；短发言包含多个句子时也可沿句末标点拆分，保持原文与位置连续。核实计划不能当作核实结果来关闭风险。

每次模型请求有界，但会话原文和账本随输入增长。默认累计文本配额是内存保护，不代表无限长会议；不提供跨重启恢复、数据库或宿主检查点。

## 配置

复制 `.env.example` 为 `.env`。优先级：显式参数、环境变量、`.env`、默认值。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `MEETNOTE_PROVIDER` | `responses` | `responses` 或显式 `mock` |
| `MEETNOTE_BASE_URL` | `http://192.168.0.222:10001/v1` | 上游地址 |
| `MEETNOTE_MODEL` | `spark25` | 服务模型名 |
| `MEETNOTE_API_KEY` | `local` | 仅服务端持有 |
| `MEETNOTE_THINKING` | `false` | 上游思考开关 |
| `MEETNOTE_TEMPERATURE` | `0` | 默认减少采样变化，仍不保证上游逐次结果完全相同 |
| `MEETNOTE_TOKENIZER_PATH` | `/workspace/model-bin/XHToken/Spark-X2.5-4B-INT8/tokenizer.json` | 本地 tokenizer；不会下载或加载模型权重 |
| `MEETNOTE_BODY_MAX_ITEMS` | `18` | 简明正文要点上限，完整事项清单不截断 |
| `MEETNOTE_BATCH_TOKENS` | `768` | 原文批次目标 |
| `MEETNOTE_BATCH_SOURCES` | `12` | 单批片段上限，控制结构化输出开销 |
| `MEETNOTE_SOURCE_TOKENS` | `384` | 原文子片段上限 |
| `MEETNOTE_INPUT_TOKEN_BUDGET` | `8192` | 指令、输入与 Schema 的合计估算上限 |
| `MEETNOTE_PROTOCOL_TOKEN_RESERVE` | `1024` | 协议与模板余量 |
| `MEETNOTE_CONTEXT_TOKENS` | `128000` | 部署上下文限额，不从模型权重声明推断 |
| `MEETNOTE_MAX_OUTPUT_TOKENS` | `4096` | 输出上限；修订和章节任务额外限制为 2048 |
| `MEETNOTE_CHECKPOINT_SECONDS` | `9` | 低流量检查点间隔 |
| `MEETNOTE_MODEL_TIMEOUT` | `240` | 单次请求超时，秒 |
| `MEETNOTE_MODEL_CONCURRENCY` | `2` | 全进程模型并发上限 |
| `MEETNOTE_CACHE_ENTRIES` / `MEETNOTE_CACHE_BYTES` | `128` / `8388608` | 每会话内存结果缓存上限；设为零禁用 |
| `MEETNOTE_MAX_SESSIONS` | `32` | 会话容量 |
| `MEETNOTE_QUEUE_SIZE` | `256` | 每会话输入队列容量 |
| `MEETNOTE_MAX_SESSION_CHARS` | `200000` | 累计输入字符限额 |
| `MEETNOTE_IDLE_SECONDS` / `MEETNOTE_RETENTION_SECONDS` | `1800` / `300` | 空闲取消与终态保留时间 |
| `MEETNOTE_EVENT_COUNT` / `MEETNOTE_EVENT_BYTES` | `1024` / `4194304` | 每会话事件缓存上限 |
| `MEETNOTE_CORPUS_DIR` | `/workspace/model-bin/meetnote` | 只读语料目录；可指定 `data-bin/data` |
| `MEETNOTE_HOST` / `MEETNOTE_PORT` | `127.0.0.1` / `8010` | 服务监听地址 |

旧 `MIN_CHARS` 和 `WINDOW_CHARS` 配置已移除。更换服务模型时必须同步配置 tokenizer 与上下文限额，并重新运行短测。输入 token 数是本地估算，实际 usage 来自上游；两者不能混作同一指标。

网络故障重试一次，结构错误最多纠正一次，业务引用／覆盖校验最多纠正一次。抽取截断、业务校验纠正失败或超预算最多拆分四层（默认 12 段批次可退到单段），不能再安全拆分时明确失败。仅引用已处理前文的重述不会作为新事实，也不能满足本批覆盖。修订核对截断时进一步拆成更小的事实对；单对最多补试一次，仍失败则保留未消费原文并明确失败。缓存仅复用通过校验的任务结果，键覆盖模型配置、tokenizer 指纹、Prompt、Schema、完整输入与解码配置；不会缓存失败响应。

## API 与内存生命周期

会话路径保持 `/api/v1/sessions`，快照契约升级为 `contract_version=2`，需要同步升级客户端。

| 操作 | 接口 |
| --- | --- |
| 创建／列表 | `POST /api/v1/sessions`、`GET /api/v1/sessions` |
| 快照 | `GET /api/v1/sessions/{id}` |
| 追加定稿片段 | `POST /api/v1/sessions/{id}/chunks` |
| 收尾 | `POST /api/v1/sessions/{id}/finalize` |
| SSE | `GET /api/v1/sessions/{id}/events` |
| 取消并释放 | `DELETE /api/v1/sessions/{id}` |
| 语料列表／详情 | `GET /api/v1/samples`、`GET /api/v1/samples/{id}` |
| 阶段和模型配置／存活检查 | `GET /api/v1/info`、`GET /health/live` |

创建请求支持 `{"title":"评审会","meeting_date":"2026-10-01"}`。日期未知时留空，不使用运行当天日期推断会议年份。

追加请求示例：

```json
{"chunk_id":"u001","text":"陈宁周四完成验收。","speaker":"周宁","timestamp":"2026-10-01T10:03:00"}
```

只有 `text` 必填，每片段最多 16000 字。相同 ID、内容、说话人和时间的重复请求返回原确认；冲突返回 409。输入顺序以接收序号为准。每会话只有一个消费者修改引擎。

`running → finalizing → completed`，另有 `failed`、`cancelled`。收尾后拒绝新输入，已接收的幂等重试仍返回原确认。队列／会话满返回 429，累计文本超限返回 413。接收成功不等于处理成功，需观察进度及终态。失败保留已接受原文和已提交事实至保留期结束；删除、过期或服务重启后不可恢复。

快照提供 `facts`、`revisions`、`sources`、`minutes`、`stats`、处理位置及事件游标。移除了旧 `summaries`、`lens`、`plan`、`plan_revision`、`debt` 字段。`processed_count` 表示消费者接收的片段数，实际证据处理状态见 `sources[].disposition` 和 `remaining_chars`。

SSE 保留 `Last-Event-ID` 补发、游标过期快照恢复和终态关闭。新增／使用 `facts.updated`、`fields.corrected`、`model.request`、`model.reused`、`model.validation_failed`、`minutes.completed` 及节点／会话生命周期事件。修订事实具有稳定 ID 和递增 revision。

`model.request` 提供 response ID、实际 usage、缓存 token、请求耗时和失败／截断详情。缺失 usage 用 null 表达并计入 `usage_unknown`。逻辑任务数、HTTP 请求数和结果缓存命中分别记录。日志仅记录操作元数据；调试输入输出保留在有界内存事件中，不自动写会议文件。

适配器会提前终止连续空白或长片段重复三次的异常输出，保留最多 2000 字符的诊断尾部。异常响应不作为有效结果；抽取节点按失败重试规则拆分，不能从未完成响应中截取 JSON 伪装成功。

## 分层测试与验收

日常开发先运行不联网测试：

```bash
poetry run ruff check src/meetnote tests
poetry run pytest -q
pnpm --dir src/web run lint
pnpm --dir src/web run build
pnpm --dir src/web exec playwright test
poetry build
```

行为测试使用脚本模型、假 Responses 传输和可注入时钟，覆盖推进、修订、引用、故障、取消、幂等、背压、收尾和请求预算；不需要真实模型或长会议。浏览器测试使用 mock 服务，检查回放、手工输入、暂停继续、收尾、释放及双主题窄屏。

显式短测调用实际模型，每次只重跑受影响案例：

```bash
poetry run meetnote check-short --case amount --case question
poetry run meetnote check-short
poetry run meetnote check-fragment --case business_budget_20k --segments 12
poetry run meetnote check-fragment --case business_budget_20k --start-segment 109 --segments 12
```

`check-fragment` 只运行最多 20 条发言，可从故障附近开始，单独标记为局部检查，不计作整场验收。

36 个案例覆盖金额、单位、负责人、提议、期限、否定、条件、不确定性、冲突、空白尾部、后续决定、取消方案、直接分工、主备值班、收费规则、延期问题、混合语气、发言人与负责人区分、采购范围重申、明确与无依据的期限、第一人称行动、显式引用旧安排的修订、撤回草案、补充说明不覆盖旧事实、混合分工与双日期、核实计划不关闭风险、日志对象区分、分工代词还原、天气条件重申、候选场地取消、未决状态重申、有审批条件的第一人称执行分工、待确认日期转正式日期和例行散会正文过滤。自动检查只是必要条件，仍需审阅事实和纪要语义。

只有短测通过且功能稳定后才运行正式长会：

```bash
poetry run meetnote evaluate-formal --case business_budget_20k --case ops05
poetry run meetnote replay business_release_10k --directory data-bin/data
```

`data-bin/data` 支持 manifest 指向的 15 篇正式语料，读取时校验 hash 和片段数。应用只读原文与日期，不读 gold；评估器在模型运行结束后才读取 gold 并输出逐项对照。旧 `transcripts.jsonl` 语料格式继续支持。

所有评估结果仅输出标准输出，不生成会议记录文件。长会结果分别报告原文处理覆盖、事项覆盖、事实准确性、降级、调用、token 和耗时；“完成”不等于 428 条事实验收全部通过。验收要求所有关键检查通过且无虚构负责人／期限、错误修订或静默丢失，不以降低门槛替代修复。
