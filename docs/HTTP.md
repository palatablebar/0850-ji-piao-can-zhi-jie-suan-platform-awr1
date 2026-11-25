# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 改期余额分配

- `POST /api/rebooking/plan`：无状态试算，传 `balance_cents`/`available_balance_cents` 与 `orders`。
- `POST /api/rebooking/orders`：提交单笔或批量改期单并原子预占余额；也可用 `/api/rebooking/allocate`、`/api/change-orders`、`/api/allocations` 等兼容路径。
- `GET /api/rebooking/workbench`：工作台数据，包含原票余额、每笔改期单抵扣结果和余额事件。
- `POST /api/rebooking/orders/{id}/confirm`：确认抵扣，确认后不受后续申请影响。
- `POST /api/rebooking/orders/{id}/cancel`（或 `/release`、`/refund`）：撤销未确认单并释放余额；重复请求幂等。
- `POST /api/rebooking/orders/{id}/retry`：重试暂缓单。
- `POST /api/rebooking/tickets/{ticket_id}/dispatch`：释放余额后，对该原票的全部暂缓单重新做最优整单分配。
- `POST /api/rebooking/demo/reset`：写入工作台演示数据。

金额字段使用整数分；日期支持 `YYYY-MM-DD`。今日优先可由 `today: true`、`is_today: true` 或今日/过去的 `issue_date` 表达。
