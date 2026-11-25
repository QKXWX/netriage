# Changelog

## v0.2.0

### 新增

- **历史存档与趋势**：每次 `/api/diagnose` 自动写入 SQLite（`storage.py`），页面新增「05 历史与趋势」——下行柱状图（虚线为签约带宽）、健康分/延迟/满载增量列表、相对上一次的涨跌，点行可回看当时的完整结果。
- **HTML 报告**：`POST /api/report` 与 `netriage diagnose <file> --html` 生成单文件报告（内联样式、无外链脚本），含套餐实测、检查表、根因证据与处置、报障工单，可直接发给运营商或存档。
- **测量 JSON 导入**：页面 02 新增「导入 JSON」，可载入 CLI 导出的载荷或 `tools/openwrt-collect.sh` 的采集结果，自动填套餐、实测、无线、日志字段。
- **OpenWrt 采集器**：`tools/openwrt-collect.sh`，只读抓取 `pppd` 日志与 `iwinfo` 信号（RSSI/SNR/协商速率/信道/频段），输出可直接导入的 JSON。
- **命令行主动探测**：`netriage probe` 直接向服务端打流（单流/四流下行、上行、空闲 RTT、抖动、丢包，四流下载期间同步测满载延迟与 bufferbloat，负载不足 0.8 秒时标注未采信），可 `--diagnose` 立即入库、`--out` 导出 payload。
- **定时复测**：`netriage watch --interval 600 --count 0` 无人值守循环，单轮失败不终止，支持 `--jsonl` 追加记录，配合 cron 可固定晚高峰证据。
- **Docker 部署**：`Dockerfile` + `docker-compose.yml`，历史落命名卷。

### 新增接口

- `GET /api/history`、`GET /api/history/<id>`、`POST /api/history/clear`
- `POST /api/report`
- `POST /api/diagnose?save=0` 可跳过存档（示例数据不污染趋势）

### 新增 CLI

- `netriage history --limit 20 [--json]`
- `netriage diagnose <file> --html report.html`
- `netriage serve --db PATH`
- `netriage probe --server URL --download 8 --upload 4 --diagnose`
- `netriage watch --server URL --interval 600 --jsonl watch.jsonl`

### 测试

- 24 → 47 项：存档增删查、重开保留、报告转义、历史接口、报告下载头、命令行探测（RTT/吞吐/丢包/bufferbloat）、watch 每轮入库、CLI 新子命令。

## v0.1.0

首个可用版本：真实测速端点、拨号日志/LQC 解析、八类根因加权诊断、报障工单、CLI，24 项 pytest，CI 覆盖 Python 3.9–3.13。
