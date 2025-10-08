# Project Walkthrough / 项目全流程说明

## 1. Vision & Scope / 项目愿景与范围

**English**  
The Holosun Dealer Locator initiative targets a reproducible, backend-only pipeline that enumerates every California ZIP code, harvests dealer records, normalizes the data, and ships an auditable CSV deliverable. The scope deliberately excludes UI layers so we can focus on automation, observability, and documentation rigor that withstands stakeholder review.

**中文**  
Holosun 经销商定位项目旨在构建一套可复现、偏后台的数据管线：遍历加州全部邮编，抓取经销商信息，完成标准化处理，并输出可审计的 CSV 成品。项目刻意不包含前端界面，以便集中资源在自动化流程、可观测性与文档规范上，为之后的利益相关方审查做好准备。

## 2. Technology Choices / 技术选型

**English**

- **Language: Python 3.11** — Rich ecosystem (requests, Playwright, pandas alternatives) and strong community tooling; async support makes future scaling feasible.
- **CLI-first architecture** — Simple onboarding, scriptable for CI/CD, and easy to demo in interviews.
- **File-based persistence (JSON/CSV)** — Lightweight for early-stage data projects, transparent for reviewers, and aligns with deliverable expectations.
- **Pytest** — Fast feedback, easy fixtures, integrates with GitHub Actions if needed.
- **Requests over browser automation (for production)** — Lower overhead; Playwright recon stays as a targeting tool when anti-automation needs visual inspection.

**中文**

- **语言：Python 3.11** —— 生态成熟（requests、Playwright、pandas 等），社区工具完善，异步能力让后续扩容具备可能。
- **命令行优先** —— 降低上手门槛，可直接集成进 CI/CD，演示时也简洁直观。
- **基于文件的持久化（JSON/CSV）** —— 轻量、透明，适合早期数据项目，也符合交付物要求。
- **Pytest 测试框架** —— 反馈快、夹具易写、后续能无缝对接 GitHub Actions。
- **生产阶段偏好 Requests 而非浏览器自动化** —— 运行成本低；Playwright 主要用于侦察和必要的界面排障。

## 3. Architecture and Modules / 架构与模块划分

**English**

1. **Reference Data Layer** — `scripts/fetch_ca_zip_codes.py` fetches ZIP + centroid data, cleans it, and produces metadata. It supplies deterministic coordinates so the orchestrator can mimic the site’s geocoded POST requests without calling third-party APIs at runtime.
2. **Recon Layer** — `scripts/capture_locator_traffic.py` (headless Playwright) captures baseline request/response shapes. This script is intentionally isolated to contain browser dependencies.
3. **Single ZIP Probe** — `scripts/fetch_single_zip.py` provides a smoke test against the production endpoint, performing anti-automation checks and producing normalized artifacts for a single ZIP.
4. **Orchestrator** — `scripts/orchestrate_zip_runs.py` sequences ZIP iteration, accumulates deduplicated dealers, writes raw artifacts, refreshes normalized JSON/CSV, emits the deliverable, calculates metrics, and persists `run_state.json` for resumption.
5. **Export Utilities** — `scripts/export_normalized_dealers.py` and `src/holosun_locator/exports.py` handle reuse scenarios (reformatting, validation, metrics).
6. **Documentation & Observability** — Markdown files in `docs/` track architecture decisions, change logs, release checklists, and this walkthrough for interview-ready storytelling.

**中文**

1. **参考数据层** —— `scripts/fetch_ca_zip_codes.py` 下载邮编与质心坐标，清洗并写出元数据，确保编排器可以仿照官网 POST 请求，无需在运行时调用第三方地理编码接口。
2. **侦察层** —— `scripts/capture_locator_traffic.py` 使用 Playwright 抓取最初的请求/响应结构，将浏览器依赖限定在该脚本内。
3. **单邮编探测** —— `scripts/fetch_single_zip.py` 面向生产端点的冒烟测试，内置反爬检测，并输出标准化结果以便快速复核。
4. **编排器** —— `scripts/orchestrate_zip_runs.py` 负责遍历邮编、汇总去重后的经销商数据、写入原始/标准化/交付文件、计算指标，并持久化 `run_state.json` 以支持断点续跑。
5. **导出工具** —— `scripts/export_normalized_dealers.py` 与 `src/holosun_locator/exports.py` 面向复用场景（重新格式化、校验、统计）。
6. **文档与可观测性** —— `docs/` 下的 Markdown 文件记录架构决策、变更日志、发布清单以及本 walkthrough，方便面试时完整讲述故事。

## 4. Workflow Narrative / 流程详解

**English**

1. **Kickoff (Day 0)** — Align on assignment, set non-goals (no public UI), and scaffold the repo.
2. **Recon & Data Sourcing** — Capture network traffic to understand payloads; build ZIP loader with deterministic centroids; document findings.
3. **Normalization Strategy** — Define canonical schema (address parsing, deduping hash) and write helper utilities.
4. **Batch Orchestration** — Implement stage-aware runner with retries, anti-automation logging, and incremental flushing.
5. **Resume & Deliverables** — Persist `run_state.json`, metrics, and CSV on every flush; add CLI switches for `--resume-state`, `--resume-policy`, and manual log replay.
6. **Documentation & Testing** — Maintain `docs/project-notes.md`, add README, release checklist, and targeted pytest coverage.

**中文**

1. **启动阶段（第 0 天）** —— 明确任务边界，确认不做 UI，搭建仓库骨架。
2. **侦察与数据来源** —— 抓取网络流量，解析请求载荷；实现带质心的邮编加载器；同步记录发现。
3. **标准化策略** —— 定义统一 schema（地址拆解、去重哈希），编写辅助工具。
4. **批量调度** —— 开发分阶段运行器，内置重试、反爬日志和增量刷盘。
5. **续跑与交付** —— 每次刷新时写入 `run_state.json`、指标和 CSV；提供 `--resume-state`、`--resume-policy`、手动日志回放等参数。
6. **文档与测试** —— 维护 `docs/project-notes.md`，补充 README、发布清单以及针对性的 pytest。

## 5. Key Concepts & Deep Dives / 核心概念与深入解析

**English**

- **DealerAccumulator** — Maintains deduplicated dealer records keyed by a SHA256 hash of normalized name/street/city/postal. Supports incremental updates, tracking first/last seen timestamps, and capturing source ZIP unions.
- **Run State Persistence** — Every flush writes `run_state.json` containing counts, blocked/error events, artifact paths, and retry policy. Resume flows rehydrate the accumulator from `normalized_dealers.json` to avoid reprocessing.
- **Manual Attention Log** — JSON lines file that records blocked ZIPs with payload and issue details; powers both human triage and automated replays.
- **Metrics Snapshot** — `compute_metrics` calculates coverage stats (unique dealers, contact completeness, source ZIP fan-out) to catch anomalies between runs.
- **Ethical Guardrails** — Throttle, user-agent rotation, and provenance logging are non-negotiable requirements embedded in design docs and CLI defaults.
- **Deliverable Guardrails** — Final CSV drops out-of-state locations by requiring `state == CA` or a postal code within the California 90001–96162 range, countering wide-radius searches that return Nevada/Montana mailing addresses.

**中文**

- **DealerAccumulator** —— 以标准化后的名称/街道/城市/邮编组合进行 SHA256 去重，支持增量更新、记录首末出现时间，并维护来源邮编集合。
- **运行状态持久化** —— 每次刷新写入 `run_state.json`，包含计数、阻断/错误事件、工件路径和重试策略；恢复流程会从 `normalized_dealers.json` 重建累加器，从而免去重复抓取。
- **人工关注日志** —— 以 JSON 行格式记录被拦截邮编及其上下文，既方便人工排查，也能驱动自动化回放。
- **指标快照** —— `compute_metrics` 输出覆盖度统计（唯一经销商、联系方式完整度、来源邮编展开程度），用于对比历史结果和捕捉异常。
- **合规约束** —— 限速、UA 轮换与溯源日志是不可妥协的要求，在设计文档和 CLI 默认值中均有体现。
- **交付物护栏** —— 最终 CSV 仅保留 `state == CA` 或邮编位于 90001–96162 区间的记录，以抵消大半径搜索带来的内华达/蒙大拿邮寄地址噪音。

## 6. Challenges & Resolutions / 关键挑战与应对

**English**

- **Anti-Automation Response Variability** — Solution: encapsulated detection in `detect_anti_automation`, wrote manual attention logging, and exposed `--prompt-on-block` for human decisions.
- **ZIP Geocoding without External APIs** — Solution: bundled centroid data fetched ahead of time; orchestrator reads from CSV to avoid runtime geocoding calls.
- **Resumable Long Runs** — Solution: flush cadence + `run_state.json` snapshots + accumulator hydration permitted clean resumes and partial deliverable refreshes.
- **Documentation Debt** — Solution: enforced documentation-before-code updates, added change logs, README (EN/CN), release checklist, and this detailed walkthrough.
- **Cross-Border Dealer Noise** — Solution: tighten deliverable export to enforce California-only rows using postal/state checks while retaining the full normalized dataset for audit trails.

**中文**

- **反爬拦截多变** —— 通过 `detect_anti_automation` 集中检测逻辑，写入人工关注日志，并提供 `--prompt-on-block` 让操作员即时决策。
- **无需外部 API 的邮编地理编码** —— 提前获取质心数据，编排器直接读取 CSV，避免运行时调用第三方。
- **长跑续航能力** —— 通过刷盘节奏、`run_state.json` 快照与累加器复原，实现平滑续跑并保持交付物同步刷新。
- **文档负债** —— 坚持“先文档再编码”，补充变更记录、双语 README、发布检查表和本 walkthrough
- **跨州噪音数据** —— 通过在交付导出阶段加入州码/邮编校验，仅保留加州经销商记录，同时保留完整标准化数据备查。

## 7. Interview Q&A Prompts / 面试问答提示

**English**

- _Why Python over Node/Go?_ — Emphasize rapid prototyping, abundant scraping libraries, and team familiarity.
- _How do you ensure data integrity if the run crashes mid-way?_ — Describe `run_state.json`, incremental flushing, and resume policies.
- _Biggest risk?_ — Anti-automation; mitigated by manual attention logging, prompts, and offline centroids reducing per-run variability.
- _How would you productionize?_ — Package CLI via Docker, schedule orchestrator with Airflow/Luigi, push metrics to monitoring stack, and secure secrets via Vault/KMS.
- _Future enhancements?_ — Integrate Playwright fallback, add geocoding validation, and expand pytest coverage for networking edge cases.

**中文**

- _为何选择 Python 而不是 Node/Go？_ —— 原型迭代快，抓取生态成熟，团队习惯也有优势。
- _如果运行中途崩溃如何保证数据完整性？_ —— 通过 `run_state.json`、增量刷盘和续跑策略，确保可恢复。
- _最大的风险是什么？_ —— 反爬机制；已经用人工关注日志、交互式提示与离线质心降低不确定性。
- _若要上生产环境怎么办？_ —— 将 CLI 封装为 Docker，使用 Airflow/Luigi 调度，指标上报监控系统，机密信息交由 Vault/KMS 管理。
- _后续优化方向？_ —— 增加 Playwright 兜底、做地理编码校验、补充网络边界条件的测试覆盖。

## 8. Demo Checklist for Interview / 面试演示清单

**English**

1. `find data -type f ! -name '.gitkeep' -delete` — Clean workspace.
2. `python scripts/fetch_ca_zip_codes.py --output data/processed/ca_zip_codes.csv` — Rebuild reference data.
3. `python scripts/fetch_single_zip.py 90001 --verbose` — Show smoke test output.
4. `python scripts/orchestrate_zip_runs.py --zip-csv data/processed/ca_zip_codes.csv --flush-every 25 --deliverable-name holosun_ca_dealers.csv` — Run core pipeline.
5. `python scripts/orchestrate_zip_runs.py --resume-state <run_dir>/run_state.json --resume-policy blocked --include-manual-log` — Demonstrate resume replay.
6. `python scripts/export_normalized_dealers.py --input <run_dir>/normalized_dealers.json --metrics-json <run_dir>/normalized_dealers.metrics.json` — Validate exports.
7. Walk through README / README-CN, release checklist, and this document to answer questions.

**中文**

1. `find data -type f ! -name '.gitkeep' -delete` —— 清理工作区。
2. `python scripts/fetch_ca_zip_codes.py --output data/processed/ca_zip_codes.csv` —— 重建参考数据。
3. `python scripts/fetch_single_zip.py 90001 --verbose` —— 演示冒烟测试输出。
4. `python scripts/orchestrate_zip_runs.py --zip-csv data/processed/ca_zip_codes.csv --flush-every 25 --deliverable-name holosun_ca_dealers.csv` —— 跑完整主流程。
5. `python scripts/orchestrate_zip_runs.py --resume-state <run_dir>/run_state.json --resume-policy blocked --include-manual-log` —— 展示断点续跑能力。
6. `python scripts/export_normalized_dealers.py --input <run_dir>/normalized_dealers.json --metrics-json <run_dir>/normalized_dealers.metrics.json` —— 复核导出结果。
7. 搭配 README / README-CN、发布检查表与本文

## 9. Closing Thoughts / 结语

**English**  
The project balances pragmatic engineering with storytelling: every decision is documented, automation is repeatable, and the pipeline is demonstrably resilient. This walkthrough distills the 0→1 journey so evaluators can appreciate both the technical depth and the operational polish.

**中文**  
本项目兼顾工程实用性与叙事：所有决策均有据可查，自动化流程可被复现，管线具备弹性。本 walkthrough 归纳了从 0 到 1 的全过程，帮助评审者理解技术深度与落地成熟度。
