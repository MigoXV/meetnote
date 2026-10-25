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

默认调用 `http://192.168.0.222:10001/v1/responses`，使用 Spark-X2.5-4B-INT8，服务模型名为 `spark25`，使用流式 Responses、严格 JSON Schema、`store=false`、`enable_thinking=false`。不依赖服务端对话历史；`store=false` 不代表关闭前缀缓存。

真实运行强制使用独立 BGE-M3 gRPC 向量服务 `127.0.0.1:50054`。引擎项目位于 `/workspace/opusi/bgem3-emb`，启动示例：

```bash
cd /workspace/opusi/bgem3-emb
poetry run bgem3-emb serve --model-dir /workspace/model-bin/BAAI/bge-m3 --device cuda:3 --dtype float16
```

MeetNote 仅安装 gRPC 客户端，不加载向量权重、Torch 或 ONNX Runtime。跨容器运行时配置 `MEETNOTE_EMBEDDING_TARGET` 为可达地址。`/health/ready` 检查向量引擎是否就绪，`/health/live` 只检查业务进程存活。不提供关闭向量或纯大模型降级路径；旧的 `MEETNOTE_EMBEDDING_ENABLED` 配置已移除，设置它不会关闭向量。mock 使用固定向量替身走相同流程，不访问真实向量服务，也不代表语义准确率。

```bash
poetry run meetnote serve --mock
```

mock 只验证接线，输出带“模拟”标记，不能用于证明事实准确率。工作台沿用苍渊·白垣双主题，浏览器不保存会话。

## 处理机制

1. 优先保留完整发言，超预算时按句子边界建立稳定子片段 ID，保留原文、说话人、时间与字符位置；超长片段按 tokenizer 预算切分。
2. 抽取输出以本批每个片段 ID 为必填键，逐段填写事实或忽略依据，再汇入账本。模型只选一次语义分类，程序展开类型和状态，避免生成“已确认的风险”等非法组合。负责人和期限有独立证据引用；执行决定和执行约束也可有明示负责人。未闭合内容可以进入未决状态。代词分工只在引用原文中有唯一且不在引号内的对应分工句时，补入其紧邻前句，逐字保留要求、数字与条件；不能用模型生成的简短主题替代任务内容。
3. 校验每段处理归属、证据范围和状态后，先由程序验证可逐字对应的第一人称执行分工；其余非空负责人和期限以每次最多 6 条的小任务独立核验是否确实属于该事项；不支持的字段清空并发布 `fields.corrected` 事件，原文及事实正文保留。核验请求失败不提交。全部通过后原子提交事实与处理位置。纯空白由程序处理；失败不越过原文，取消不提交候选结果。
4. 从内存索引召回同对象事实，相似度仅用于召回，不用于删除事实；按预算且每次最多六个候选对核对修订、撤销、问题关闭和冲突。程序建立原文证据句 ID，Schema 限定每对可选的证据和操作，模型不复制原句或事实 ID。每对新旧命题并排提供，带旧安排引文时限定匹配范围，避免相邻对象的相同数字互相覆盖。重复确认的候选事实仅提供首次与最近证据用于比对，完整引用仍在账本。关键词重合不能删除信息；只有精确命题及字段匹配，或共享证据的逐字同义标签扩展，才合并引用。修订保留历史，冲突不静默择一；没有明确修订／分歧原文依据时原事实并存。
5. 工作台展示当前事实与修订记录。全部现行事实与原文能放入请求预算，且已为审校草稿预留完整输出预算时，直接纳入纪要输入，不先按固定条数筛掉后半场。超过预算时，为修订后的现行安排保留名额，决定、行动、未决事项和约束轮流分配候选；有负责人和期限的行动优先，背景事实使用剩余额度。每类每页最多 24 条，逐层选择，默认最多保留 18 条；完整事项清单不受此限制。`minutes.coverage.body_facts` 给出实际成文候选数量。
6. 纪要根据对应原始发言成文，篇幅随实质议题调整，没有整篇固定字数。会中只更新草稿，消费完全部输入后独立审校一次。生成与审校均以 Schema 限定可引用的事实 ID，校验引用、重复段落和内部标签；完整事项清单保留在结果结构中。章节筛选失败可回退事实列表，正式成文或最终审校失败则明确报错，不发布完成状态。

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
| `MEETNOTE_BODY_MAX_ITEMS` | `18` | 完整证据超出请求预算时的候选上限；能安全容纳时直接纳入全部现行事实 |
| `MEETNOTE_BATCH_TOKENS` | `768` | 原文批次目标 |
| `MEETNOTE_BATCH_SOURCES` | `12` | 单批片段上限，控制结构化输出开销 |
| `MEETNOTE_SOURCE_TOKENS` | `384` | 原文子片段上限 |
| `MEETNOTE_INPUT_TOKEN_BUDGET` | `8192` | 指令、输入与 Schema 的合计估算上限 |
| `MEETNOTE_PROTOCOL_TOKEN_RESERVE` | `1024` | 协议与模板余量 |
| `MEETNOTE_CONTEXT_TOKENS` | `128000` | 部署上下文限额，不从模型权重声明推断 |
| `MEETNOTE_MAX_OUTPUT_TOKENS` | `4096` | 所有模型任务的输出 token 上限 |
| `MEETNOTE_CHECKPOINT_SECONDS` | `9` | 低流量检查点间隔 |
| `MEETNOTE_MODEL_TIMEOUT` | `240` | 单次请求超时，秒 |
| `MEETNOTE_MODEL_CONCURRENCY` | `2` | 共享主模型客户端的请求并发上限 |
| `MEETNOTE_EXTRACTION_WINDOW` | `1` | 每会话抽取窗口，1～4；执行中和等待提交的批次共同占用窗口 |
| `MEETNOTE_LOCAL_EXTRACTION_RETRY` | `false` | 仅纠正能够明确定位到原文记录的抽取错误；合并后仍完整校验 |
| `MEETNOTE_PARALLEL_CHAPTERS` | `false` | 并行独立类别及同轮分页筛选；纪要生成与复核仍串行 |
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

## 调用策略与速度对照

在线消费者和完整会议评测共用 `MeetingEngine.run`：等待推理时继续有界接收输入，仅无状态抽取读取原文快照并行执行；字段核验、关系归并、事实编号及正式提交按原文顺序进行。窗口满时停止继续取输入。失败拆分导致来源边界改变时，后续预取取消并重新准备；取消、关闭和最终刷新会等待子任务清理，不能跳过未处理原文。兼容的 `append/cycle/finalize` 接口继续提供串行处理路径。

三个优化开关暂时保留保守默认值。显式启用示例：

```bash
MEETNOTE_EXTRACTION_WINDOW=2 MEETNOTE_LOCAL_EXTRACTION_RETRY=true MEETNOTE_PARALLEL_CHAPTERS=true poetry run meetnote serve
```

局部纠正只保留已通过记录，对指定原文重新抽取，合并后再次完整校验；跨记录依赖不能安全定位时仍整批纠正。该策略不删除校验、不减少原文，也不改变抽取字段。

主模型服务就绪后运行调用策略对照：

```bash
poetry run meetnote compare-strategy --case business_release_10k --rounds 3
```

该命令交替运行串行基线与窗口 2、局部纠正、并行章节组合，各三次；两边都执行完整抽取与最终成文，不复用跨运行抽取结果，不包含模型加载时间。失败或有剩余原文的运行不能用于加速结论。人工标注仍只在推理结束后读取，会议输入、事件与结果只在内存及标准输出中，不写文件。

`stats.by_task` 提供请求数、输入输出 token、错误次数、累计请求和排队耗时、最多 256 条首个输出增量延迟；另有抽取窗口峰值、局部纠正与拆分计数。并发时请求累计耗时可能超过墙钟时间，端到端速度只能使用完整运行的 `elapsed_seconds`。上游排队和前缀缓存仍会影响测量，应同时查看每轮数据和质量，不把单次最好成绩当稳定收益。正式切换默认配置要求完整对照的耗时中位数至少下降 20%，且关键覆盖、负责人期限及最终纪要质量不退步。

## 全文同步生成接口

`POST /v1/fulltext/generate` 一次提交完整转写，等待全部所选能力执行后返回；不需要创建或结束会话。默认使用同一个 Spark-X2.5-4B-INT8 主模型。

全文纪要仍由 `MeetingEngine.run` 抽取、核验和消费全部原文，再执行最终成文与审校。该入口不生成应用不会读取的中间纪要；会话入口仍按原规则持续更新纪要。最终刷新失败会明确返回失败提示，不能用旧稿替代。

摘要和计划的审校分页以每页 Pydantic Schema 的字符预算为准，并为校验纠正包预留输入空间。接近预算上限却未完成末句的结果会进入纠正重试，仍失败时明确报错；不会截掉已经生成的文字来满足长度约束。

```bash
curl --fail-with-body http://127.0.0.1:8010/v1/fulltext/generate \
  -H 'Content-Type: application/json' \
  -d '{"id":"meeting-001","text":"这里替换为完整会议转写正文，至少 80 字符。","pipeline":["minutes"]}'
```

成功响应：

```json
{"id":"meeting-001","minutes":{"markdown":"# 会议纪要\n……"}}
```

| 请求字段 | 说明 |
| --- | --- |
| `id` | 必填业务关联 ID，1～128 字符；首字符为英文字母或数字，其余允许字母、数字、点、下划线、冒号和连字符；不用于幂等去重 |
| `text` | 必填完整转写，去除首尾空白后最多 200,000 字符 |
| `pipeline` | 可选有序能力列表，不能重复或为空；默认 `["abstract","minutes","keywords","actions"]` |

| 能力 | 响应字段 |
| --- | --- |
| `polish` | `polished_text`，润色全文；需要显式选择 |
| `abstract` | `final_abstract`，独立生成会议摘要 |
| `minutes` | `minutes.markdown`，复用 `MeetingEngine.run()` 的完整证据处理、成文和审校流程 |
| `keywords` | `keywords`，当前固定为 `null`，不调用模型 |
| `actions` | `next_actions.markdown`，独立生成工作计划，区分建议与已定分工 |

未选择的产物字段不返回。能力按列表顺序执行，每项都读取原始转写，前一项产物不会自动成为后一项输入。摘要、工作计划和润色在引擎内按 token 预算切分；摘要与工作计划再逐层汇总，润色按原顺序拼接。每段都处理，不使用截断正文的方式规避预算；无法安全完成时返回明确失败提示。该流程复用当前主模型，不复制参考仓库的模型路由或业务图。

少于 80 字符、有效字符少于 60、中文少于 20 字或有效字符占比低于 0.35 的材料不调用模型，返回 HTTP 200，提示写入 `message` 和所选产物字段。单项能力失败不阻止后续能力，返回 HTTP 200 和对应安全提示；有失败时须检查 `message`，不能仅凭 HTTP 200 判定成功。无内部地址、密钥和异常堆栈返回。请求格式不合法返回 400，正文超限返回 413，服务繁忙或排队超时返回 429，请求级异常返回 500。

全文请求默认同时执行 2 场、最多等待 4 场，排队超时为 600 秒；配置为 `MEETNOTE_FULLTEXT_MAX_ACTIVE`、`MEETNOTE_FULLTEXT_MAX_WAITING`、`MEETNOTE_FULLTEXT_QUEUE_TIMEOUT`。所有任务共享主模型并发额度。客户端读取超时需覆盖排队与所有能力的总耗时。断连、请求取消和服务关闭会取消并清理任务；完成后立即释放全文请求状态，不出现在会话列表中，也不持久化会议内容。健康检查同时支持 `/health` 和 `/health/live`。

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

## 主模型联调与效果复核

抽取、字段核验、关系判断、证据核验、正文生成和审校统一调用 `spark25` 主模型。应用不再提供独立决策模型客户端、后端选择或回退配置；旧环境变量不会启用其他模型。联调命令见 [联调说明](docs/INTEGRATION.md)。

`evaluate-decisions` 检查主模型的字段及关系核验；`review-quality --sample product_clean --details` 在内存中运行完整会议并输出人工复核材料。参考标注仅在模型处理结束后读取，不进入模型输入。

### 面向阅读的纪要

会中由 MeetingEngine 从已提交证据快照调用主模型组织正文，输出会议概述、主要结论、讨论要点、后续安排和实质未决问题；最终消费全部原文后，以原文为依据独立审校。跨章节高度重复的段落会触发校验重试。正文不展示事实编号、状态标签和证据附录；结构化 `minutes.narrative` 保留段落与事实引用，`checklist`、`evidence_trace` 继续保留完整依据。引用校验、生成或最终审校失败不会发布完成状态。正文与证据仍仅存在会话内存中。

### 会中流式纪要

`MeetingEngine.run()` 在提交证据后调度正文任务，每会话最多一个，状态提交仍由唯一消费者执行。首批有效内容立即整理，后续默认至少间隔 10 秒启动一次；通过 `MEETNOTE_MINUTES_INTERVAL` 调整。正文任务只读取已提交证据，生成与审校沿用结构化引用约束、有界重试及模型并发额度，不阻塞输入接口。

SSE 新增 `minutes.started`、`minutes.delta`、`minutes.updated` 和 `minutes.aborted`。预览事件携带 `round`、`sequence`、`covered_chars` 和 `blocks`，只包含已解码的正文文本；正式提交事件携带完整 `minutes`。快照增加 `minutes_preview`，用于补发游标过期或断线恢复；该字段及所有会议数据仅存内存。`info.capabilities` 声明 `rolling_minutes` 和 `minutes_preview`，契约版本仍为 2。

未完成的 JSON 转义、字段名和引用不进入预览正文。已有草稿的段落在替代 text 字段完整前保持显示，避免每轮先退回短前缀再逐字增长。会中草稿仍可修订；最终审校不将重写中的片段替换到预览。结束时处理全部尾部证据，复用已覆盖最终消费位置的草稿完成一次审校；如需最后一次生成，则合并审校后提交。成功后发布 `minutes.completed`，空会议不补造纪要。

### hz01 仓库部署与流式复查

`deploy/systemd/meetnote-e2e-summary-repository.conf` 将既有摘要 unit 的工作目录和启动入口切到本仓库。原 unit 的预算、超时及并发配置继续生效。将 `deploy/summary-model.env` 复制到 `/etc/meetnote-e2e-summary-repository.env` 并执行 `restorecon`，再安装覆盖文件、执行 `systemctl daemon-reload`，确认会话已处理完毕后重启摘要服务。模型名称和 tokenizer 必须与当前 `/health/ready` 返回的实际模型一致。BGE-M3 使用原 `bgem3-emb.service` 的 NPU 部署，LLM 仍使用现有实例与显存配置。

`poetry run python scripts/replay_meeting_stream.py --meeting-id 266 --speed 6` 从 18095 只读取得确认逐字稿，复用 Python 应用的 `SummarySession`，按原时间间隔的六分之一输入真实摘要服务。可用 `--speed 1` 做原速复查。适配器的运行事件、产物和事实快照仅保留在内存，终端报告逐轮消费位置、正文长度、议题覆盖和请求计数，结束后删除验证会话。脚本不新建业务会议、不覆盖原会议产物、不保存转写或模型响应文件；凭据从指定配置文件读取，不能写入命令参数。

## 向量能力与验证

协议副本位于 `src/meetnote/embeddings/proto/embedding.proto`，服务端真源位于引擎项目；两端通过 `scripts/run_grpcio_tools.sh` 生成 Python 和类型声明。接口只编码文本，返回有序、归一化的 1024 维向量和显式截断信息；不把模型分类、检索门槛或业务 Prompt 放入推理引擎。

MeetNote 强制使用向量的两个环节：

- 纪要事实核对：每批新事实必须先完成向量编码，对含修订/撤销/确认信号的新事实统一召回历史候选；同批事实也经过相同的向量排名和门槛。已移除词项索引召回及未经过向量召回的候选路径。最多为每条新事实召回 6 个候选，相似度默认下限 0.70。向量分数只让候选进入证据核对；原文范围、允许操作、单次最多六对和主模型核验仍然生效。相似不能直接触发删除、合并或修订。该阈值是可配置的初始召回门槛，不是“事实置信度”。
- 全文摘要及工作计划：先按原流程处理全部原文，再对生成句子召回原始转写窗口；以原文位置和引用 ID 提交主模型审校。审校输出也使用产物的 Pydantic 长度约束。相同原文窗口去重，预算不足时按页打包审校，并预先分配含页间换行的总输出字符预算，不能截掉原文假装完成。证据相似度本身不证明句子为真。

向量缓存在每个会议的 Context 中，仅存内存，默认最多 2048 条且不超过 16MiB 的文本/向量有效载荷。跨会议不共享内容缓存；单次 RPC 最多 64 条文本。整个会议保持同一个模型标识，服务中途换权重会明确失败，避免混用向量空间。RPC 故障不静默切回词项模式；有状态处理在提交前失败，消费位置不前移。全文能力延续独立失败及安全提示约定。统计增加 `embedding_requests/texts/elapsed_ms/truncated`，会话事件增加 `embedding.completed` 和 `embedding.recalled`。

主要配置是 `.env.example` 中的 `MEETNOTE_EMBEDDING_*`。`MAX_TOKENS` 默认 512，兼容旧 ONNX 的 token 上限；业务客户端明确允许截断并累计截断数，原文仍完整进入抽取流程。调大服务和客户端上限需同时验证显存及召回质量。当前没有恢复旧项目依赖参考标注的会议分类器，也没有复制旧的向量阈值决策链。

```bash
poetry run pytest -q
poetry run meetnote check-short --case owners --case amount --case question
```

已移除 `compare-embeddings` 命令及其无向量基线实现。真实服务验证使用下方完整 HTTP 回放，会议内容和产物仅存内存。

候选排名前先排除当前及后续事实，避免占用历史召回名额；语义查询只编码事实对象与命题正文，完整来源发言留给后续证据核验，避免同段其他事项干扰检索。真实 HTTP 长会议回放可运行：

```bash
poetry run python scripts/check_fulltext.py --sample business_release_10k --url http://127.0.0.1:8013
```

该脚本读取正式语料原文，通过默认能力列表执行完整流程，输出产物长度、成功状态和向量 RPC/批次数差值；不保存会议正文或产物。比对期间应使用独立实例，避免其他请求污染计数。

## V2 文档优先工作台

V2 的定位是“文档优先、证据可追溯的会议摘要验证与复核工作台”。生产源码仍位于 `src/web`，沿用 React、TypeScript、Vite 和 pnpm；没有引入新的依赖、浏览器持久化或后端旁路。

[Figma V2 桌面工作流](https://www.figma.com/design/1TAZwMSZ4UwczV1hxSgnxV?node-id=12-331) 与 [窄屏工作流](https://www.figma.com/design/1TAZwMSZ4UwczV1hxSgnxV?node-id=13-946) 位于独立 V2 页面，原“01 · 当前工作台”保留。文件内分为基础样式、组件、桌面工作流、窄屏工作流、交互与能力边界。Figma 的 10 段会议文本与 7 条当前事项均明确标记为演示数据，不是模型实测；其他压力及错误状态单独标注。

| 旧入口与问题 | V2 入口与组织 | 能力分类 |
| --- | --- | --- |
| 当前会话与常驻样本配置挤在侧栏 | 216px 可折叠临时会话导航；未建会话单一启动区；开始后从“输入来源”打开样本、版本和速度 | 现有能力重排 |
| 事实与阶段摘要共用半宽空面板 | 实时概览约 40/60 原文/结果，各栏一处主要滚动；阶段摘要与按类型分组的事项分开 | 重排；布局为前端补充 |
| 最终纪要固定在半宽面板 | 最大 880px 的连续文档，按需查看依据 | 现有能力重排 |
| 修订只显示前后正文 | 前后值、负责人、期限、原因、原安排与修订依据并排，窄屏改纵向 | 现有能力重排 |
| 追加定稿文本挤占常驻高度 | 紧凑“追加转写”入口，可展开编辑器 | 前端补充 |
| 七节点和长 JSON 常驻 | 底部状态条；按需打开七节点最近事件与折叠排查信息 | 现有能力重排 |
| 证据只跳第一个片段 | 多引用、缺失引用说明、完整片段和前一片段上下文；关闭后恢复原事项焦点与阅读位置 | 前端补充 |
| 窄屏先堆配置再堆双栏 | 概览 / 原文 / 纪要任务切换，导航与设置按需打开，独立证据面板 | 前端补充 |
| 缺少带走纪要的出口 | 复制文本与下载 Markdown，可选择附上引用原文，标注阶段/最终与待确认项 | 已实现的前端补充 |

关键真实行为与字段约束：

- “新建并回放”仍新建临时会话，回放全部片段后仍自动收尾；现有暂停/继续只控制样本提交，不取消已接收内容的处理。读取样本、创建会话与回放/自动收尾失败分别说明。
- `minutes_preview.blocks` 展示生成中预览；运行中已有 `minutes` 标为阶段纪要，只有 `completed` 才称最终纪要。接口没有正式纪要覆盖片段版本，不声称运行中的旧稿覆盖了最新输入。终态会话不能继续追加。
- 当前事项排除 `superseded`、`retracted`、`resolved`，不等于“未完成任务”。`confirmed` 显示“会议表述明确”，不等于客观事实或人工复核。负责人、期限缺失分别显示“未指定”“未明确”。
- 原文只显示真实 `seq`、`speaker` 和可空 `timestamp`。引用显示服务端原文证据文本及来源标识，不在页面伪造精确字符高亮。修订和运行事件无统一发生时间时显示修订号或事件序号。
- 每次追加按 Unicode 字符计数，16000 上限与 Python/Pydantic 一致，表情不按 UTF-16 双倍计数。空白不提交，超限不截断，失败保留文本；相同内容的重试复用 `chunk_id`。Ctrl/Command + Enter 尊重 `isComposing`，Enter 默认换行。
- 原文仅在跟随最新时随追加滚动；向上阅读后显示“有新片段，回到最新”。证据通过单独面板打开，避免改变原阅读位置。刷新不会重新挂载输入区。
- 结束后保留时间读取服务配置，默认 5 分钟；服务重启立即释放是另一条件。不提供伪倒计时。404 后保留当前页面内存中的原文与结果，但不宣称可以恢复会话。释放会话前显式提醒保存。

需要接口或产品确认、尚未接通的能力：人工复核与编辑记录、区分算法重分析/人工编辑的修订来源、事件与修订发生时间、准确过期倒计时、正式纪要覆盖版本、安全重试协议、PDF/Word、云分享或永久归档。本次没有将这些能力画成已可用的生产按钮。Figma 中复制、下载和提交只是路径演示；真实浏览器分别调用剪贴板、Blob 下载和现有追加接口。

### 本次验证及限制

```bash
pnpm --dir src/web run build
pnpm --dir src/web run lint
pnpm --dir src/web run format:check
pnpm --dir src/web exec playwright test tests/redesign.spec.ts tests/workspace.spec.ts
poetry run pytest tests/test_api.py tests/test_sessions.py -q
```

浏览器行为测试中的演示数据只存在于 `tests/fixtures.ts`，通过明确的测试网络桩提供；不作为生产结果或真实模型评测。压力数据包含 160 个多段落片段、超过 4 万字、64 条事项和多引用，分多个议题与条件边界，不使用单句刷屏。

检查的视口为 1440×900、1366×768、1024×768、390×844 与 320×844。检查包括窄屏重排、长中英文名称、正文放大、键盘分隔、证据返回、焦点、下载、超限、输入法合成事件保护、失败保留、幂等重试、跟随最新及会话过期。普通文字与控件边界分别按实际表面检查对比；选中背景上的小字采用主文字色，避免浅色强调值被误作重要文字或图标色。移动 44×44 是项目目标；[WCAG 2.2 的 2.5.8 AA 基线为 24×24 并含例外](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)。不将有限自动检查称为完整合规认证。

真实 mock 消费者测试验证创建、追加、回放、暂停/继续、自动收尾与释放接线。现有 mock 在长语料成文时可能触发 `ModelFailure`；界面会明确显示失败并保留阶段纪要和已接收原文，测试不将这种状态认定为成功生成最终纪要。短文本的完成路径另有断言。本次未改模型或引擎以掩盖该限制，未运行真实模型完整会议准确率评测。没有真人可用性测试；系统级中文输入法候选窗口与各操作系统原生浏览器缩放仍需人工复核。
