# Holosun 经销商定位数据管线

本项目提供一整套自动化工具，用于遍历加利福尼亚州所有邮政编码，抓取 Holosun 经销商信息，完成数据规范化处理，并生成可交付的 CSV 以及配套的度量指标和审计工件。

## 环境准备
- Python 3.11 及以上版本（开发环境为 macOS）。
- `pip` 作为依赖管理工具；当前运行只需要 `requests`（HTTP）和 `pytest`（测试）。
- 建议使用 `virtualenv` 或 `pyenv` 创建隔离环境。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests pytest
```

## 仓库结构
- `docs/` —— 设计文档、上下文说明以及发布文档。
- `scripts/` —— 命令行工具（`fetch_ca_zip_codes.py`、`fetch_single_zip.py`、`orchestrate_zip_runs.py` 等）。
- `src/holosun_locator/` —— 可复用的导出与校验工具。
- `data/` —— 原始与处理后的数据（除占位文件外不纳入 git 版本控制）。
- `logs/` —— 运行日志，包括记录人工介入的 `manual_attention.log`。
- `tests/` —— pytest 测试套件。

## 交付流程（逐步说明）

### 1. 准备邮编参考数据（每次刷新必做）
```bash
python scripts/fetch_ca_zip_codes.py \
  --output data/processed/ca_zip_codes.csv
```
预期结果：
- 生成 `data/processed/ca_zip_codes.csv`（约 1,678 行，包含经纬度字段）。
- 生成 `data/processed/ca_zip_codes.metadata.json`，记录来源 URL、时间戳和行数。
- 控制台日志出现 `INFO Loaded <count> ZIP records`。

可选的事前冒烟测试：
```bash
python scripts/fetch_single_zip.py 90001 --verbose
```
将在 `data/raw/single_zip_runs/20251009T010203Z_90001/`（示例）下写入请求、响应与归一化摘要，便于验证。

### 2. 执行编排器（全量运行）
```bash
python scripts/orchestrate_zip_runs.py \
  --zip-csv data/processed/ca_zip_codes.csv \
  --flush-every 25 \
  --deliverable-name holosun_ca_dealers.csv
```

预期观察：
- 控制台持续输出阶段日志（`[stage:load_zip_table]`、`[stage:submit_locator_request]` 等）。
- 生成运行目录 `data/raw/orchestrator_runs/<run_id>/`，例如 `20251009T023000Z/`，包含：
  - `run_state.json`（运行时进度快照）与 `run_summary.json`（结束后写入）。
  - `normalized_dealers.json` / `normalized_dealers.csv`（完整标准化结果）。
  - `holosun_ca_dealers.csv`（精简交付 CSV，字段为 `dealer_name`、`address`、`phone`、`website`，已根据邮编区间/州名自动过滤为加州经销商）及 `<deliverable>.metrics.json`（度量信息）。交付物默认位于运行目录内，如需集中存放可复制到 `data/processed/`。
  - 若未使用 `--skip-raw`，`zip_runs/<zip>` 目录内保留每个邮编的原始请求/响应工件。
- 每次批量刷新会打印类似 `Metrics snapshot: total=185, unique=175, with_phone=140` 的统计行。

操作建议：
- 使用 `--prompt-on-block` 在遇到反爬拦截时人工确认是否重试。
- 调整 `--flush-every` 控制刷盘频率：数值越小，检查点越频繁。
- 调试时可配合 `--max-zips` 限定处理数量。

### 3. 恢复 / 回放场景

#### 场景 A —— 中断后恢复
```bash
python scripts/orchestrate_zip_runs.py \
  --zip-csv data/processed/ca_zip_codes.csv \
  --resume-state data/raw/orchestrator_runs/20251009T023000Z/run_state.json
```
默认 `--resume-policy skip` 会忽略已完成的邮编。控制台会提示 `INFO Skipping <N> ZIPs already completed in resume state`，即便没有待处理邮编，也会刷新最终交付物。

#### 场景 B —— 仅重放被拦截邮编
```bash
python scripts/orchestrate_zip_runs.py \
  --resume-state data/raw/orchestrator_runs/20251009T023000Z/run_state.json \
  --resume-policy blocked \
  --include-manual-log \
  --manual-log-run 20251009T023000Z
```
编排器会加载 `run_state.json` 中的 blocked 列表，并合并 `logs/manual_attention.log` 里对应 run 的记录。预期出现 `INFO Loaded <count> ZIPs from manual attention log`，随后仅处理该批邮编。

#### 场景 C —— 人工指定邮编并复用日志
```bash
python scripts/orchestrate_zip_runs.py \
  --zip 90001 --zip 94105 \
  --include-manual-log \
  --manual-log logs/custom_manual_attention.log \
  --deliverable-name rerun_holosun_ca_dealers.csv
```
适用于点检或专项验证。若指定的日志不存在或邮编不在参考表中，工具会给出警告并跳过。

### 4. 再次导出 / 校验（可选）
```bash
python scripts/export_normalized_dealers.py \
  --input data/raw/orchestrator_runs/20251009T023000Z/normalized_dealers.json \
  --output data/raw/orchestrator_runs/20251009T023000Z/normalized_dealers.csv \
  --metrics-json data/raw/orchestrator_runs/20251009T023000Z/normalized_dealers.metrics.json
```
用于按需重写 CSV 或独立产出指标。控制台会回显记录数与输出路径。

### 5. 运行结束自检
- 查看 `run_summary.json`，确认 `blocked_count` / `error_count` 是否为零。
- 检查 `logs/manual_attention.log`，评估是否仍需人工处理。
- 抽样核查 `holosun_ca_dealers.csv`（字段齐全、无异常换行，且全部为加州地址——邮编 90001-96162 或州名 CA）。
- 比较 `<deliverable>.metrics.json` 与历史版本，注意异常波动。
- 发布前务必执行 `docs/release-checklist.md` 中的完整核对表。

## 可观测性与输出
- 日志命名空间为 `holosun.*`，使用 `--verbose` 可以查看调试信息。
- 每次批量刷新都会重写交付 CSV 与指标，确保断点续跑不影响数据一致性。
- 被拦截邮编会在 `<run_dir>/blocked_zips/` 生成 JSON 工件，同时追加到 `logs/manual_attention.log`。
- `run_state.json` 永远指向最新快照，可安全用于再次恢复。

## 故障排查速查表
- **缺少邮编行**：确认 `data/processed/ca_zip_codes.csv` 是否存在，必要时重新生成。
- **遇到验证码/拦截**：启用 `--prompt-on-block`，查看手动日志，降低访问频率或更新会话。
- **交付 CSV 为空**：检查 `normalized_dealers.json` 是否含有数据，并确认 `--zip` / `--max-zips` 参数正确。
- **缺失指标文件**：确认 `--deliverable-name` 与 `--metrics-name` 指向可写路径，可使用导出脚本重新生成。

## 测试
```bash
python -m compileall scripts/orchestrate_zip_runs.py
pytest
```
当前测试覆盖导出工具与恢复逻辑，可根据新需求扩展。

## 合规与道德准则
- 遵守 Holosun 网站服务条款与使用政策。
- 保持请求速率合理（默认重试/退避策略已较保守，勿开启多线程并发除非获批）。
- 轮换 User-Agent 并尊重网站返回的 Cookie，禁止绕过明确的反爬机制。
- 每条数据保留 `source_zip`，并在 `data/raw/` 中保存可审计的原始文件。
- 当 `logs/manual_attention.log` 出现验证码或阻断提示时应暂停运行，待人工确认后再继续。
- 未经授权，禁止公开分发抓取的数据。

## 发布流程（速览）
1. 确保编排器跑完或使用恢复模式补齐全部邮编。
2. 检查 `run_state.json` / `run_summary.json` 中的阻断与错误计数。
3. 复核交付 CSV 与指标 JSON，保证格式与数值合理。
4. 更新文档（`docs/project-notes.md`、README、发布清单）。
5. 将运行目录与人工日志归档保存（可压缩至 `data/releases/<timestamp>.tar.gz`）。

完整检查表请见 `docs/release-checklist.md`。
