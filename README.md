# 机票残值结算台

航旅结算专员处理残值抵扣与税费分离的本地工作台。样例、规则和复核记录都在项目内，不依赖外部服务。

## 启动

```bash
docker compose up --build
```

打开 `http://127.0.0.1:${PORT:-18520}`。默认端口见 `docker-compose.yml`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

## 操作

1. 在左侧选阶段，查看采信、整理、计算、判定和对账结果。
2. 在“改期余额分配”面板提交或载入示例，查看每笔改期单的优先、足额抵扣、暂缓、确认、撤销释放和重试结果。
3. 选择 `dirty` 样例观察坏行和隔离原因。
4. 换 `clean` 样例执行批次，填写复核意见后签发。
5. 下载证据 JSON，核对来源摘要、指标和签发版本。

业务规则见 `docs/BUSINESS-RULES.md`，字段定义见 `docs/DATA-DICTIONARY.md`。
