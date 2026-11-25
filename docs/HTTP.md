# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `POST /api/runs/{id}/amendments`：对已签发批次签发话单补正。提交 `request_key`（幂等键）、`base_version`（补正基准版本）、`reason`、`comment`、`adjustments`（`label` + `amount_cents` 列表）。基准版本落后于当前生效版本返回 `409 STALE_BASE`；同一 `request_key` 重试返回 `200` 且不新增版本，首次签发返回 `201`。
- `GET /api/runs/{id}/versions`：历次签发版本链（首发 `ISSUE`、补正 `AMENDMENT`，含基准版本与摘要）。
- `GET /api/runs/{id}/versions/{version}`：单个签发版本的元数据。
- `GET /api/runs/{id}/evidence?version={n}`：下载证据；不带 `version` 取当前生效版，带版本号取该版不可变快照（历史凭据与金额原样复查）。
- `GET /api/runs/{id}/evidence`：下载当前生效证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。
