# 携转话单计费台

通信计费稽核员处理重叠合并与分钟取整的本地工作台。样例、规则和复核记录都在项目内，不依赖外部服务。

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
2. 选择 `dirty` 样例观察坏行和隔离原因。
3. 换 `clean` 样例执行批次，填写复核意见后签发。
4. 下载证据 JSON，核对来源摘要、指标和签发版本。
5. 已签发批次可在“话单补正”中按条目提交更正金额：补正签发后当期对账立即使用新金额，原版本凭据仍可在“签发版本”中原样复查；基准版本落后会被拒绝，需基于当前版重新复核，同一补正重复提交不会新增版本。

业务规则见 `docs/BUSINESS-RULES.md`，字段定义见 `docs/DATA-DICTIONARY.md`。
