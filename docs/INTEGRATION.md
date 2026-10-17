# 4B 主模型联调

默认主模型使用 `http://192.168.0.222:10001/v1` 的 `spark25` 服务，对应 Spark-X2.5-4B-INT8；tokenizer 位于 `/workspace/model-bin/XHToken/Spark-X2.5-4B-INT8/tokenizer.json`。所有模型任务使用同一 Responses 客户端、模型名和并发额度。

## 启动

```bash
pnpm --dir src/web run build
poetry run meetnote serve --port 8010
```

`GET /api/v1/info` 返回主模型的 `provider`、`model` 及能力列表，包含 `fulltext_generate`。服务不再检查独立决策服务的就绪状态、精度或指纹。

## 验证

```bash
curl --fail-with-body http://192.168.0.222:10001/v1/models
poetry run meetnote check-short --case amount --case question
poetry run meetnote check-integration --url http://127.0.0.1:8010
poetry run meetnote review-quality --sample product_clean --details
```

`check-integration` 先运行短测，再通过 HTTP 回放完整会议，检查 SSE、输入消费、纪要和会话释放。`review-quality` 在推理结束后读取标注，输出人工复核材料。所有会议输入、事件和结果只驻留内存，结果仅输出终端；不生成会议文件。

全文接口的请求、响应和失败语义见 [README](../README.md#全文同步生成接口)。真实验证应检查非空产物及 `message`，不能以 HTTP 200 代替成功判断；关键词仅为占位，不视为模型能力验收。
