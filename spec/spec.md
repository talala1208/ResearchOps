# ResearchOps Agent 规格

> 状态：草案 · 版本：0.3.0 · 更新日期：2026-08-07
> 本文件是 ResearchOps Agent 当前需求、行为边界、核心契约与验收标准的唯一事实来源。

## 1. 项目概要

### 问题

用户需要围绕 AI 产品、Agent 产品、AI 开发工具或企业 AI 引入主题快速完成可追溯研究。普通 LLM 一次性回答容易出现来源不清、证据不足、冲突信息未披露、过度推断和安全边界不清的问题；手动研究又耗时，难以沉淀为可评估、可复盘、可持续改进的工程流程。

### 解决方案

ResearchOps Agent 使用 LangGraph 编排研究流程，将用户问题拆解为子问题和最低证据标准，通过多源检索、工具输出清洗、证据准入、证据质量评估、极简语义冲突识别、LLM 报告生成与质量 Review、安全审查和本地产物持久化，生成一份可追溯、可降级、可评估的 Markdown 研究报告。Web / 冲突 HITL 当前仅为观测占位（不暂停、不因未完成而降级），稳定后再补真实人机接管。第一版只做 demo 级闭环，优先证明 Agentic Workflow、证据治理、LangSmith 观测评估和安全边界设计，不追求生产级数据源覆盖或复杂前端。

### 成功指标


| 指标 | 当前基线 | 目标 | 测量方式 |
| --- | --- | --- | --- |
| MVP 链路可运行 | 主 Graph、节点路由和产物持久化已实现 | 一次运行能产出报告、指标和本地图 | `scripts/run_smoke.py` 手动 smoke |
| 报告可追溯 | 结论通过 `question_id`、`evidence_id` 关联 | 报告引用可回溯到标准化证据 | Review 节点与人工检查 |
| 证据不足处理 | 已实现预算检查、迭代和降级报告 | 证据不足时不强行编造，并披露降级原因 | 证据充足性与报告测试 |
| 安全边界 | Input Guard（LLM）+ 工具不可信边界 + Safety Review（规则）+ 分散权限 | 注入、敏感信息和高风险请求能被标记或降级；HITL 不计入安全闭环 | Safety Dataset 与手动检查 |




## 2. 范围



### 目标

- 实现一个基于 LangGraph 的 AI 产品研究 Agent MVP。
- 支持用户输入研究主题，输出本地 Markdown 研究报告。
- 支持研究计划、子问题拆解、搜索任务分发、证据治理、报告生成、质量 Review 和安全审查的完整流程。
- 支持 Web Search、本地文档搜索、结构化 mock 数据三类数据源；Graph 固定并行调用 Web 与本地检索节点，各节点按 `source_type` 内部过滤任务。
- 支持 Web Search 登录墙、验证码、反爬场景的 HITL **观测占位**路由和 DevTools 页面观察；不真正暂停、不因 `completed=false` 降级；真实人机接管为后续能力。
- 支持证据语义冲突触发的 HITL **观测占位**路由；人工确认结果只记入状态与 metrics，不改写证据、不驱动降级。
- 支持检索预算、报告 Review 修正预算、安全修正预算三套独立控制。
- 支持最终后台静默保存运行产物，包括报告、指标和实际运行图；当前静态编排图由脚本保存 PNG。
- 核心 Prompt 使用 YAML 管理并记录版本与 schema 契约。



### 非目标

- 第一版不实现复杂前端；演示优先使用 CLI、LangGraph API Server、Studio 或 agent_chat。
- 第一版不做生产级 RAG 系统，不构建复杂向量库生命周期。
- 第一版不做生产级 Web 爬虫，不绕过验证码、登录墙、反爬或权限限制。
- 第一版不做外部写入能力，不发邮件、不提交表单、不写外部数据库。
- 第一版不做医疗、法律、金融等高风险领域的确定性建议。
- 第一版不追求覆盖所有 AI 产品数据源，只保证默认 AI 产品研究模板的最小闭环。



### 用户与核心场景


| 角色 | 场景 | 预期结果 |
| --- | --- | --- |
| 项目作者 | 输入一个 AI 产品研究主题 | 生成可追溯研究报告和运行指标 |
| 项目作者 | 运行示例或演示样例 | 可以展示 Graph 编排、降级、安全、评估设计 |
| 面试官 | 询问项目如何避免幻觉 | 可以说明最低证据标准、证据评分、Review 和降级机制 |
| 面试官 | 询问 Agent 安全 | 可以说明 Input Guard、Tool 不可信边界、权限分级和 Safety Review；HITL 明确为观测占位 |




## 3. 核心行为与边界



### 主流程

```text
用户输入研究主题
  -> input_guard
        -> 不通过：prepare_degraded_report -> generate_research_report
        -> 通过：plan_research
  -> 固定并行触发 web_search_sub_agent / local_document_search_tool
  -> web_search_sub_agent
        -> 可选 web_search_hitl_request
        -> web_search_result_ready
  -> web_search_result_ready 与 local_document_search_tool 汇聚
  -> sanitize_and_cluster
  -> evaluate_evidence_quality
  -> 可选 request_human_review
  -> build_evidence_matrix
  -> check_evidence_sufficiency
  -> 证据充足：generate_research_report
  -> 证据不足：check_step_budget
        -> 预算充足：strategy_iteration -> plan_research
        -> 预算不足：prepare_degraded_report -> generate_research_report
  -> review_research_report
        -> Review 不通过且修正次数未耗尽：generate_research_report
        -> 否则：safety_review
  -> safety_review
        -> 安全不通过且修正次数未耗尽：prepare_degraded_report -> generate_research_report
        -> 否则：persist_outputs
```



### 节点输入输出契约


| 节点 | 主要读取字段 | 主要写入字段 | 路由 / 边界 |
| --- | --- | --- | --- |
| `input_guard` | `user_query` | `input_guard_result`、`executed_nodes` | 真实 LLM 结构化输出节点；风险不可继续时进入降级准备；否则进入研究分析 |
| `plan_research` | `user_query`、工作流配置、`planning_mode`、`active_question_ids`、`sub_questions`、`minimum_evidence_standard`、`search_iteration_context` | 初始模式：研究计划、最低证据标准、索引、预算、首轮任务；迭代模式：下一轮 `search_tasks`、任务索引、`active_question_ids`、`search_attempt`、分发模式 | 真实 LLM 结构化输出节点；根据 `planning_mode` 选择初始或迭代 Prompt；每次执行统一递增 `search_steps`；Graph 随后固定并行调用 Web 与本地检索节点 |
| `web_search_sub_agent` | `search_tasks` | `web_search_results`、`web_tool_evaluation_records`、`web_hitl_required`、`web_hitl_reason` | 对多个 Web `search_tasks` 做有界任务级并发，单任务内仍按固定顺序调度工具；Tavily / Context7 由代码物化且不进汇总 LLM；仅 SerpAPI 压缩结果进入汇总 LLM 打四维分并判断是否抓正文，代码写入综合 `score` 后 keep top N，再按 LLM 决策（硬分否决/兜底）可选 Playwright 写 `body`；合并时按 URL 去重；不做内部 Agent 循环；细节见本节后文 |
| `local_document_search_tool` | `search_tasks`、`LOCAL_DOCUMENTS_BASE_PATH`、结构化 mock 数据配置 | `local_document_results` | `local_document` 使用只读 Markdown 关键词搜索；`structured_mock` 使用 LLM 规划安全关键词后执行参数化 SQLite 查询；两类任务均在节点内部按 `source_type` 过滤 |
| `web_search_hitl_request` | `web_hitl_required`、`web_search_results` | `web_hitl_decisions`、`web_hitl_required` | **观测占位**：仅服务 Playwright 触发的登录/验证码/权限墙；只对 `requires_login=true` 的结果调用 DevTools 观察；记录 `completed=false`，不真正暂停，不因未完成而降级或阻断后续证据治理 |
| `web_search_result_ready` | 无业务字段 | `executed_nodes` | Web 分支汇聚节点；无 HITL 时直接进入，有 HITL 观测后进入，再与本地检索分支汇聚 |
| `sanitize_and_cluster` | `web_search_results`、`local_document_results` | `evidence_clusters`、可选 `discarded_candidate_count`，并清空两类上游结果 | 标记 `untrusted_tool_output`、按规范化 URL 去重、按 `question_id` 聚类；丢弃不可准入候选（见证据准入契约）；不再写入 `sanitized_results` |
| `evaluate_evidence_quality` | `evidence_clusters`、`sub_questions` | `evidence_items`、`conflicts`、`entity_index`、`hitl_required`、重置后的 `hitl_decisions`，并清空 `evidence_clusters` | 整表替换本轮 `evidence_items`/`conflicts`；按来源分桶计算 `reliability_score`；长文写入 `outputs/evidence/{evidence_id}.txt`；仅当同题证据命中极简语义冲突规则时建 `ConflictItem`；`hitl_triggered` 仅决定是否走冲突 HITL **观测**节点 |
| `request_human_review` | `conflicts`、`hitl_required` | `hitl_decisions` | **观测占位**：不真正暂停；只记录冲突观测事实，不改写证据正文，不触发降级 |
| `build_evidence_matrix` | `evidence_items`、`sub_questions`、`entity_index` | `evidence_matrix` | `evidence_matrix` 只存 `question_id`、证据 ID 列表与少量聚合字段；**不**在此把矩阵收录标为 `used_in_final_report` |
| `check_evidence_sufficiency` | `sub_questions`、`minimum_evidence_standard`、`evidence_matrix` | `evidence_sufficiency_result`、`degradation_reason` | 充足性只写入 `evidence_sufficiency_result`；`sufficient` / 分项状态 / `insufficient_question_ids` 均由此结构读取，不再另写顶层镜像字段 |
| `check_step_budget` | `search_steps`、`max_search_steps`、`evidence_sufficiency_result` | `step_budget_exhausted`、`search_budget_remaining`、`step_budget_reason`、`degradation_reason` | 只判断检索预算，不负责降级 |
| `strategy_iteration` | `evidence_sufficiency_result`、`search_tasks`、`iteration_count` | `iteration_count`、`repeated_action_count`、`active_question_ids`、`planning_mode`、`search_dispatch_mode`、`search_iteration_context` | 实线回到 `plan_research`；`search_iteration_context` 中上一轮任务只保留 `task_id` / `query` / `source_type` 摘要；可在 `ENABLE_STRATEGY_EXTERNAL_WORKER=true` 时调用外部 worker |
| `prepare_degraded_report` | `input_guard_result`、`degradation_reason`、`step_budget_reason`、`evidence_sufficiency_result`、`safety_review_result`、`safety_revision_count` | `degraded`、`degradation_reason`、`safety_revision_count` | 已实现确定性降级状态整理；输入安全检查不通过时必须说明原因；只设置降级状态，不写报告正文 |
| `generate_research_report` | `user_query`、`research_goal`、`evidence_matrix`、`evidence_items`、`sub_questions`、`degraded`、`review_result`、可选 `safety_review_result` | `report_draft`、`review_revision_count`、`entity_index.used_evidence_ids`、各证据 `used_in_final_report`、`degraded`、`degradation_reason` | LLM 结构化生成 Markdown；主张须引用合法 `evidence_id`；必须消费上一轮 `revision_suggestions`（若有）；降级/Safety 再入时带保守约束；非法引用 ID 剔除并写入 limitations |
| `review_research_report` | `report_draft`、`evidence_matrix`、`evidence_items`、`minimum_evidence_standard`、`evidence_sufficiency_result` | `review_result` | LLM 结构化质量 Review；输出评分与 `revision_suggestions`；不评估安全边界；`degraded=true` 时直接放行 |
| `safety_review` | `report_draft`、`review_result`、`safety_revision_count` | `safety_review_result`、`final_report`，并清空 `report_draft` | 已实现规则安全审查；通过后 State 只保留 `final_report` 作为报告正文 |
| `persist_outputs` | `final_report`、`evaluation_metrics` 相关状态、`executed_nodes` | `final_report_path`、`executed_mermaid`、`executed_mermaid_png_path`、`output_artifacts`、`evaluation_metrics` | 已实现报告 Markdown、metrics JSON、实际运行链路 PNG 静默保存；`executed` PNG 当前为线性示意（由 `executed_nodes` 列表生成，不还原 fan-out/fan-in）；不保存 `.mmd` 文件；只写 `outputs/` 下新文件 |


### 检索工具契约

- Web Search 使用确定性调度和单次 LLM 汇总：
  - `web_search_sub_agent` 对多个 Web `search_tasks` 使用有界线程池并发；默认并发度为 `WEB_SEARCH_TASK_CONCURRENCY=3`，最小为 1；合并结果时保持原任务顺序，任一任务抛错时节点整体失败。
  - 单任务内部仍串行调用工具，顺序不变；工具级限流为 Playwright=1、Tavily=1、Context7=2、SerpAPI=3，避免高成本 MCP / Chrome 会话风暴。
  - 所有 Web 任务调用 SerpAPI；请求条数由 `WEB_SEARCH_SERPAPI_NUM` 控制（默认 5，限制 1–10）。压缩保留 `title` / `url` / `snippet` / `published_at` 后进入汇总 LLM；LLM 输出四维分，并输出顶层 `needs_page_fetch` / `fetch_url` / `fetch_reason`（是否抓正文由 LLM 主决策：snippet 已够则 false；缺关键数字/条款/长文等则 true，且只选一条 keep 后候选 URL）。代码计算综合分 `score = source_confidence_score * 0.35 + freshness_score * 0.15 + relevance_score * 0.3 + answer_coverage_score * 0.2` 并写入结果，再按 `score` 保留 `WEB_SEARCH_SERPAPI_KEEP_TOP_N`（默认 3，限制 1–10）。
  - Web 类型任务调用 Tavily MCP；请求条数由 `WEB_SEARCH_TAVILY_MAX_RESULTS` 控制（默认 5，限制 1–10）。Tavily 不进入汇总 LLM；代码按 `title` / `score` / `url` / `snippet`(取 `content`) / `published_at` 保留并物化为候选。
  - `official_docs`、`github`、`changelog` 任务调用 Context7 MCP；官方文档不进入汇总 LLM、不做搜索相关性打分；代码保留 `docs_result` / `resolve_result`（及 `library_id`）并物化为候选，`snippet` 不承载文档正文；证据层对其采用权威加权分桶，并把 `docs_result` / `resolve_result` 写入 `EvidenceItem`。
  - query 含 URL 时，由 Playwright 抓取该 URL，代码物化为带 `body` 的候选，不进入汇总 LLM。
  - SerpAPI 结果页正文：keep top N 后由代码解析抓取目标——主决策为 LLM 的 `needs_page_fetch` + 合法 `fetch_url`（必须属于 keep 后 Serp 候选）；`WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE`（默认 0.7）仅作否决与兜底：目标条 `relevance_score` 未严格大于该阈值则否决且不改抓其他页；LLM 要求抓取但 `fetch_url` 缺失或不在候选中时，回退为 keep 后 `relevance_score` 最高且严格大于阈值的一条。成功后在该条写入 `body`（截断正文，上限 `WEB_SEARCH_PLAYWRIGHT_MAX_CHARS`，默认 3000），保留原 `snippet`，不二次进入汇总 LLM。
  - 最终由代码合并 Tavily、Context7、query-URL Playwright 与打分后的 SerpAPI 候选，并按规范化 URL/路径去重（同 URL 优先保留含 `body`、更高分、更完整正文的候选）；进入汇总 LLM 的 `tool_outputs` 仅含 SerpAPI 压缩结果；JSON 使用紧凑序列化（无 `indent`）。原始工具输出仍可用于评测记录提取。
  - Playwright 使用 headless + isolated Chrome；导航与快照必须在同一显式 MCP 会话内完成，避免新会话返回 `about:blank`。
  - 单次 LLM 汇总失败时，当前实现使用 SerpAPI 首条结果形成低可信度候选；没有可用结果时不触发 Web HITL。
- Web HITL 仅服务 Playwright：仅当 Playwright（query URL 抓取或 Serp 结果页抓取）确定性检测到登录墙、验证码或权限墙时，才将对应候选标为 `requires_login=true` 并设置 `web_hitl_required=true`；SerpAPI 汇总 LLM 不输出、不判断 HITL；Tavily、Context7 与“无搜索结果”均不得触发 Web HITL。普通页面 403、超时、空正文或 `about:blank` 只降低可信度，不进 Web HITL。
- `web_search_hitl_request` 为观测占位：仅对 `requires_login=true` 的结果调用 DevTools 观察；记录 `completed=false`，不真正暂停，不因未完成 HITL 降级。
- `local_document` 使用 jieba 搜索分词和子串匹配，递归读取配置目录内 `.md` 文件；单文件最大 1 MB，最多返回 8 条，禁止路径逃逸。无匹配返回空列表，异常返回带 `blocked_reason` 的结果。
- `structured_mock` 首次查询时可从 `data/mock/ai_products.json` 初始化 SQLite；实际执行为允许字段上的参数化 `LIKE` OR 查询，默认 5 条、上限 20。无匹配返回标明 `matched_product_count=0` 的结果。



### 证据准入契约

进入 `evidence_items` 并参与充足性计数前，候选必须通过准入过滤。满足任一条件的候选必须丢弃，不得抬高充足性：

- `requires_login` 为 `true`
- `blocked_reason` 非空
- `url_or_path` 以 `placeholder://` 开头（本地失败 / 无匹配等工具占位）
- 存在 `structured_payload.matched_product_count == 0`

丢弃数量可写入运行 State 的 `discarded_candidate_count` 供 metrics 观测；工具侧仍可保留失败占位便于调试。



### 极简语义冲突规则

- 仅对同一 `question_id` 下至少 2 条有效证据做两两检测。
- 检测文本取自 `title + snippet`（启发式，非完整 NLI）。
- **对立极性**：预置中英对立词对；同题两条分别命中对立两侧则建 `ConflictItem`。
- **同锚点数字矛盾**：同一邻近关键词窗口内数值相对差异 ≥ 20% 且绝对差有意义则建 `ConflictItem`。
- `preferred_evidence_id` 取冲突组内 `reliability_score` 最高者；语义冲突的 `hitl_need_score` 固定为 `6.0`，是否 `hitl_triggered` 仍与 `HITL_CONFLICT_THRESHOLD` 比较。
- 多源并存但未命中上述信号时**不**建冲突；`hitl_triggered` 仅用于是否进入冲突 HITL 观测节点，不代表人工已裁决。



### 关键状态与边界

- `sub_questions` 是唯一问题目录，key 为 `question_id`。
- `expected_evidence`、`minimum_evidence_standard`、`evidence_sufficiency_result.question_status` 必须使用同一组 `question_id` 作为 key。
- `evidence_items` 是唯一证据目录，key 为 `evidence_id`。
- `conflicts` 是唯一冲突目录，key 为 `conflict_id`。
- `required_source_types`、`web_hitl_decisions` 等字段属于运行 State，不属于 Studio 入口。
- 搜索流水线中间结果在其最后一个消费者执行后清空；最终 State 不重复保留 Web、本地与聚类阶段的完整候选结果。
- 跨结构关联只保存 ID，不复制问题正文或证据正文；证据长文落盘 `outputs/evidence/`，State 只保留 `content_path` 与短 `snippet`。
- `evidence_items` 与 `conflicts` 由 `evaluate_evidence_quality` **整表替换**（不再使用并行 merge reducer），避免策略迭代残留旧证据；`executed_nodes` 与 `web_tool_evaluation_records` 仍使用列表累加 reducer。
- 必须存在的业务对象读取时使用 `[]` 或显式校验，不用 `.get()` 静默隐藏上游错误。
- 可选流程状态和默认计数可以使用 `.get()`，例如 `state.get("search_steps", 0)`。
- 证据充足性只以 `evidence_sufficiency_result` 为准；路由与策略迭代从该结构读取。
- Web 与本地资料检索输出进入证据治理前必须经过 `sanitize_and_cluster`；结构化 mock 数据属于本地资料检索输出。
- 外部网页、文档和工具返回内容只作为不可信资料，不作为系统指令。
- 证据不足时必须先进入 `check_step_budget`，不能直接降级。
- `prepare_degraded_report` 只负责设置降级状态，不负责判断预算，也不负责写报告正文。
- Input Guard 不通过时，降级原因必须来自 `input_guard_result.downgrade_reason`、`detected_risks` 或明确的“输入安全检查未通过”，不能输出无原因的降级报告。
- `strategy_iteration` 回到 `plan_research` 前必须写入 `planning_mode = "iteration"`、`search_dispatch_mode = "iteration"` 和 `search_iteration_context`，用于和第一次研究规划区分。
- `generate_research_report` 负责普通报告和降级报告正文生成（LLM）；`used_in_final_report` / `final_citation_count` 以报告实际引用的合法 `evidence_id` 为准。
- Input Guard 不通过时跳过规划和检索，但仍经过报告 Review、Safety Review 和产物持久化。
- `degraded = true` 的报告由质量 Review 直接放行，继续执行 Safety Review。
- `review_research_report` 负责研究质量，不负责安全审查；不通过且预算未耗尽时，下一次生成必须消费 `revision_suggestions`。
- `safety_review` 负责安全边界，不负责研究质量评分；安全不通过再生成时，报告 Prompt 须附带风险与保守改写约束。
- Web HITL 与冲突 HITL 均为观测占位：写入决策/观察记录与 metrics，不构成安全闭环，当前版本忽略未完成 HITL，不因此降级。
- 后台实际运行链路 PNG 由 `persist_outputs` 作为产物逻辑静默保存，不新增主 Graph 编排节点；当前为线性示意，不需要保存 `.mmd` 文件。



### 预算控制


| 预算 | 字段 | 控制范围 | 不控制 |
| --- | --- | --- | --- |
| 检索预算 | `search_steps / max_search_steps` | `plan_research` 执行轮次和证据不足后的迭代 | 单个工具调用次数、报告生成、Review、安全审查、持久化 |
| Review 修正预算 | `review_revision_count / max_review_revisions` | Review 不通过后回到报告生成的次数 | 检索循环、安全审查 |
| 安全修正预算 | `safety_revision_count / max_safety_revisions` | Safety 不通过后生成安全降级报告的次数 | 检索循环、质量 Review |




## 4. 目标架构

```text
CLI / LangGraph API Server / Studio / agent_chat
    ↓
src/workflow/graph.py
    ↓
src/workflow/nodes.py + src/workflow/edges.py + src/workflow/*_nodes.py
    ↓
src/schemas/state.py
    ↓
src/llm + src/tools + src/evaluators + src/dataset + src/config
```



### 模块职责


| 模块 | 职责 | 可依赖 | 禁止依赖 |
| --- | --- | --- | --- |
| `src/config` | 环境变量、路径、模型和权限配置 | `.env`、标准库 | 静默吞掉缺失必要配置 |
| `src/dataset` | 创建和管理 LangSmith dataset、追加/拆分样例、运行 Pairwise A/B 实验 | `.env`、LangSmith SDK、项目模块 | 默认由脚本手动运行 |
| `src/evaluators` | LangSmith evaluator 或本地评估逻辑 | `src/schemas`、LLM 接口 | 修改业务 State 的主流程字段 |
| `src/llm` | 模型初始化、结构化输出、Prompt 加载调用 | `src/config`、`prompts` | 业务流程路由决策散落在此处 |
| `src/memory` | 实现模型多轮对话，memory checkpoint | - | 暂未实现 |
| `src/schemas` | State、研究计划、证据、Review、安全类型 | 标准库类型 | 运行时外部服务 |
| `src/tools` | Web、本地文档、结构化数据、Claude Code / Codex worker 封装等工具 | `src/config`、外部只读 API、本地受限 CLI | 外部写入、绕过权限墙 |
| `src/workflow` | Graph 构建、节点统一导出、条件路由，以及 planning/search/evidence/report/guard/artifact 节点实现 | `src/schemas`、工具接口、LLM 接口 | 直接硬编码具体 API Key |
| `scripts` | 静态图导出、LangGraph dev 启动和手动 E2E smoke | 项目模块 | 承载核心业务逻辑 |




### 对外入口

- `src.workflow.graph.graph`：LangGraph 编译后的主图对象。
- `scripts/export_graph_mermaid.py`：导出当前静态编排图 PNG 到 `outputs/runs/researchops_graph.png`。
- `scripts/run_smoke.py`：调用主 Graph 的真实 LLM 手动 smoke，不属于默认离线测试。
- 后续 CLI 或 LangGraph API Server 入口必须调用 `src.workflow.graph.graph`，不得复制 Graph 编排逻辑。
- LangGraph Studio 启动相关文件：`scripts/run_langgraph_dev.sh`、`langgraph.json`。



## 5. 核心契约



### 输入

```python
{
    "user_query": str,
}
```

`src.workflow.graph.graph` 的 `input_schema` 为 `ResearchInput`，仅暴露 `user_query`。预算和阈值从环境变量读取，并在首次 `plan_research` 时写入运行 State；Studio 输入不支持直接覆盖预算。



### 输出

一次完整运行最终应在 State 中提供：

- `final_report`
- `final_report_path`
- `output_artifacts`
- `evaluation_metrics`
- `executed_nodes`

`output_artifacts` 当前结构为：

```python
{
    "final_report": "outputs/reports/<run_id>.md",
    "metrics": "outputs/runs/<run_id>_metrics.json",
    "executed_mermaid_png": "outputs/runs/<run_id>_executed.png",
}
```



### 数据模型


| 模型 | 必需字段 | 不变量 |
| --- | --- | --- |
| `SubQuestion` | `question_id`、`question`、`priority`、`required_source_types` | `question_id` 在一次运行中唯一 |
| `ExpectedEvidence` | `question_id`、`evidence_description`、`minimum_count`、`required_source_types`、`required_authority_level` | `question_id` 必须存在于 `sub_questions` |
| `MinimumEvidenceStandard` | `question_id`、`min_total_evidence`、`min_high_quality_sources`、`must_include_source_types`、`allow_degraded_answer` | `question_id` 必须存在于 `sub_questions` |
| `SearchTask` | `task_id`、`question_id`、`query`、`source_type`、`search_provider`、`attempt` | `question_id` 必须存在于 `sub_questions` |
| `EvidenceItem` | 身份与来源字段、短 `snippet`、可选 `content_path`（指向 `outputs/evidence/{evidence_id}.txt`）、四维分（`relevance_score` / `answer_coverage_score` / `source_confidence_score` / `freshness_score`）、`reliability_score`、可选 `score_bucket` / `scored_by` / `library_id`、`score_reason`、`used_in_final_report` | `evidence_id` 唯一；`question_id` 必须存在于 `sub_questions`；长文不进 State；`reliability_score` 计算时权威权重取自 `source_type` 表，`source_confidence_score` 来自工具；不持久化 `authority_score` |
| `ConflictItem` | `conflict_id`、`question_id`、`evidence_ids`、`conflict_summary`、`preferred_evidence_id`、`hitl_need_score`、`hitl_triggered` | `evidence_ids` 必须存在于 `evidence_items` |
| `QuestionEvidenceStatus` | 问题与 Priority、证据数量、来源覆盖、最低标准、加权分和缺口原因 | `priority_weight` 参与整体加权评分 |
| `EvidenceSufficiencyResult` | `sufficient`、`overall_score`、`threshold`、`high_priority_all_met`、`question_status`、缺口与降级字段 | `question_status` 的 key 必须存在于 `sub_questions` |
| `StateEntityIndex` | 问题 ID、按问题关联的证据/冲突/任务 ID、最终使用证据 ID | 只保存 ID，不复制业务正文 |
| `ReviewResult` | `passed`、总分、来源覆盖、引用完整性、groundedness、边界、过度推断风险、修正建议 | Review 只评估研究质量 |
| `SafetyReviewResult` | `safety_pass`、风险等级、风险列表、降级标志与原因 | Input Guard 与 Safety Review 当前共用该 State 类型 |




### 证据充足性规则

- `Priority` 影响整体证据充足性评分。
- 默认权重为：`high = 3`、`medium = 2`、`low = 1`。
- high 子问题必须尽量满足最低证据标准；未满足时保留诊断和缺口说明，但不再单独覆盖整体加权分判断。
- medium / low 子问题参与加权覆盖率。
- `evidence_sufficiency_threshold = 0.75` 当前为代码常量，不通过环境变量配置。
- 判断整体证据充足以 `overall_score >= evidence_sufficiency_threshold` 为准。
- `high_priority_all_met` 作为诊断字段保留，用于报告中披露高优先级子问题缺口。



### 身份与版本

- `question_id` 由 Planner 生成，格式建议为 `Q1`、`Q2`。
- `task_id` 由 Search Task Planner 生成，格式建议为 `T1`、`T2`。
- `evidence_id` 由证据标准化过程生成，格式建议为 `E1`、`E2`。
- `conflict_id` 由冲突识别过程生成，格式建议为 `C1`、`C2`。
- Prompt YAML 必须记录 `name`、`version`、`owner_node`、`input_schema`、`output_schema`。
- 当前 Prompt 文件为 `input_guard.yml`、`research_planner.yml`、`search_task_planner.yml`、`web_search_subagent.yml`、`local_structured_search.yml`、`research_report.yml`、`research_report_review.yml`。
- `load_prompt` 当前强制校验 `name`、`version`、`owner_node`、`system_prompt`、`user_prompt_template`；`input_schema`、`output_schema` 是文档契约，运行时结构由 `src/llm/structured_outputs.py` 的 Pydantic 模型保证。
- 运行时模型角色以 `src/config/settings.py` 的 `DASHSCOPE_MODEL_FIELDS` 和节点传入的 role 为准；YAML `model_role` 当前仅作文档。
- `local_structured_search.yml` 的 `snippet_template` 被运行时使用；其他展示模板字段当前主要作为文档配置。
- 使用 DashScope / OpenAI 兼容接口的 `with_structured_output` 时，对应 Prompt 的 `messages` 必须包含 `json` 字样（大小写均可）；否则供应商会拒绝 `response_format=json_object` 请求。



## 6. 关键技术决策


| 领域 | 决策 | 理由 | 约束或替换条件 |
| --- | --- | --- | --- |
| 编排 | 使用 LangGraph | 需要状态化、多分支、循环、HITL 和可视化编排 | 若流程退化为单次调用可重审 |
| State | 使用 `TypedDict(total=False)` | LangGraph 节点逐步写入状态，早期字段不一定存在 | 若结构稳定且需要强校验可迁移到 Pydantic |
| Prompt | 使用 YAML 管理核心 Prompt | 便于版本对比、LangSmith metadata 和面试表达 | Prompt 数量过多时可分 runtime/evaluators 目录 |
| 数据源 | 第一版使用 Web、本地文档、结构化 mock 三类 | 覆盖真实研究常见来源，又能控制 MVP 范围 | 生产化前需重新评估数据源稳定性和合规 |
| 输出 | 报告和运行产物保存到 `outputs/` | 便于离线演示和复盘 | 写项目外路径或覆盖已有文件必须人工确认 |
| 安全 | LLM Input Guard + 规则 Safety Review + 分散式工具权限 | demo 级安全闭环，避免只靠 Prompt | 外部写入、通用代码执行启用前必须重审 |




## 7. 数据与存储

- 数据归属：项目本地运行产物归 ResearchOps Agent 当前运行所有。
- 持久化结构：
  - 结构化 mock 种子数据：`data/mock/ai_products.json`
  - 结构化 mock 数据库：`data/mock/ai_products.sqlite`
  - 报告 Markdown：`outputs/reports/<run_id>.md`
  - 实际运行 PNG：`outputs/runs/<run_id>_executed.png`
  - 运行指标 JSON：`outputs/runs/<run_id>_metrics.json`
  - 静态编排图 PNG：`outputs/runs/researchops_graph.png`
- 一致性与原子性：写入产物时必须使用 `run_id` 或时间戳避免覆盖已有文件。
- 结构化 mock 数据库在首次查询时执行 `CREATE TABLE IF NOT EXISTS`，并从 JSON 种子执行 `INSERT OR IGNORE`。
- 生命周期：`outputs/` 为生成文件目录，可被清理；清理前需确认没有用户手动保留内容。
- 敏感数据：不得把 API Key、token、私钥、完整 `.env`、系统 Prompt 或用户隐私写入报告、图、指标或 LangSmith metadata。



## 8. 配置、安全与错误处理



### 配置

- `src/config/settings.py` 导入时加载项目根 `.env`。Studio 输入仅含 `user_query`；预算和阈值由环境变量管理。
- LLM 基础配置：`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`DASHSCOPE_MODEL`。节点模型覆盖：`SAFETY_GUARD_MODEL`、`RESEARCH_PLANNER_MODEL`、`SEARCH_TASK_PLANNER_MODEL`、`WEB_SEARCH_SUBAGENT_MODEL`、`LOCAL_DOCUMENT_SEARCH_MODEL`、`RESEARCH_REPORT_MODEL`、`RESEARCH_REPORT_REVIEW_MODEL`。
- 只有模型名为 `deepseek-chat` 或 `deepseek-reasoner` 时使用 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`；DashScope 托管的 `deepseek-v4-*` 仍使用 DashScope。
- 工作流必需配置：`MAX_SEARCH_STEPS`、`MAX_REVIEW_REVISIONS`、`MAX_SAFETY_REVISIONS`、`HITL_CONFLICT_THRESHOLD`、`REVIEW_PASS_SCORE`。冲突项当前 `hitl_need_score=6.0`，默认阈值 9.0 时不触发冲突 HITL。
- 在线检索配置：`SERPAPI_API_KEY`、`TAVILY_API_KEY`、`CONTEXT7_API_KEY`、`ONLINE_MCP_TIMEOUT_SECONDS`、`WEB_SEARCH_SERPAPI_NUM`、`WEB_SEARCH_SERPAPI_KEEP_TOP_N`、`WEB_SEARCH_TAVILY_MAX_RESULTS`、`WEB_SEARCH_CONTEXT7_MAX_CHARS`、`WEB_SEARCH_PLAYWRIGHT_MAX_CHARS`、`WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE`、`WEB_SEARCH_TASK_CONCURRENCY`。对应 key 缺失时该工具返回 `ok=false`，由 Web SubAgent 继续汇总其他可用来源；`WEB_SEARCH_TASK_CONCURRENCY` 默认 3，控制同一节点内 Web 任务并发上限。
- Playwright MCP 通过 stdio 启动 `npx -y @playwright/mcp --headless --isolated --browser chrome`；Tavily 通过 `mcp-remote` stdio 代理；Context7 使用 streamable HTTP。
- DevTools 配置：`ENABLE_HITL_DEVTOOLS` 默认 true，`HITL_DEVTOOLS_HEADLESS` 默认 false；仅在 Web HITL 且结果需要登录时实际调用 `chrome-devtools-mcp@latest`。
- 本地 Markdown 根路径由 `LOCAL_DOCUMENTS_BASE_PATH` 管理，在首次本地检索时校验。
- 策略外部 worker 配置：`ENABLE_STRATEGY_EXTERNAL_WORKER`、`STRATEGY_EXTERNAL_WORKER`；真实调用还需开启对应的 `ENABLE_CLAUDE_CODE_WORKER` 或 `ENABLE_CODEX_WORKER`。命令、参数和超时由 `.env.example` 中 `CLAUDE_CODE_*`、`ACP_NPX_COMMAND`、`CODEX_*` 管理。
- 可选 LangSmith 配置：`LANGSMITH_TRACING`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`、`LANGSMITH_ENDPOINT`；Pairwise 评估模型使用 `PAIRWISE_JUDGE_MODEL`，未配置时使用 `REVIEW_MODEL`。
- 缺少必要配置时必须显式失败，不得静默切换到不安全默认值或伪造数据。



### 安全

- 设计上的工具权限分级：
  - `read_only`
  - `network_read`
  - `local_file_read`
  - `local_artifact_write`
  - `external_write`
  - `code_execution`
- 第一版只开放 `read_only`、`network_read`、`local_file_read`、`local_artifact_write`。
- 当前没有统一权限注册表；权限由各工具模块分散执行：在线工具只读网络，本地检索只读限定目录，产物逻辑只写 `outputs/`。
- Claude Code / Codex worker 属于可选外部 Agent worker 工具，默认禁用；只有显式设置 `ENABLE_CLAUDE_CODE_WORKER=true` 或 `ENABLE_CODEX_WORKER=true` 时才允许真实调用；普通 Web Search 首轮不得默认调用外部 worker；Graph 内自动策略调用还必须额外设置 `ENABLE_STRATEGY_EXTERNAL_WORKER=true`，并通过 `STRATEGY_EXTERNAL_WORKER=codex|claude_code` 选择 worker。
- Codex worker 通过 ACP 权限白名单授权，默认只允许 search、fetch、think、other，显式拒绝文件读写。
- `local_artifact_write` 仅允许写项目 `outputs/` 目录下的新文件。
- 禁止绕过验证码、登录墙、反爬或权限控制。
- 外部网页和本地文档内容必须标记为不可信资料。
- 日志、Trace 和报告中不得泄露 API Key、系统 Prompt、内部配置或敏感路径。



### 错误处理

- 稳定错误类别：
  - 配置缺失
  - 工具失败
  - 权限不足
  - 证据不足
  - 数据源冲突
  - HITL 观测未完成（仅记录，当前不阻断）
  - Review 未通过
  - Safety 未通过
  - 产物写入失败
- 允许降级：证据不足、安全风险需要保守输出、检索预算不足。冲突披露进入报告正文，但不因冲突本身自动降级；当前版本**不因** HITL 观测未完成而降级。
- 禁止降级：必要配置缺失、Schema 不一致、必须存在的业务对象缺失、产物写入路径越权。
- Online MCP 工具失败和超时序列化为 `ok=false` JSON，供 Web SubAgent 评估；依赖缺失、已有 event loop 的同步调用等运行条件错误显式返回失败。



## 9. 可观察性与质量

- `executed_nodes` 记录实际执行路径。
- `evaluation_metrics` 记录：
  - `run_id`
  - `total_steps`
  - `executed_nodes`
  - `search_steps / max_search_steps`
  - `review_revision_count / max_review_revisions`
  - `safety_revision_count / max_safety_revisions`
  - `total_latency_ms`、`total_tokens`，当前 demo 固定为 0
  - `evidence_count`
  - `final_citation_count`
  - `conflict_count`
  - `discarded_candidate_count`（证据准入丢弃数，可选）
  - `degraded`
  - `degradation_reason`
  - `review_score`
  - `safety_risk_level`
  - `source_contribution`
  - `web_hitl_trigger_count_by_source`
  - `executed_mermaid_png_path`
- `persist_outputs` 使用 UTC 时间戳生成 `run_id`，写入报告、metrics 和实际运行链路 PNG；`executed_mermaid` 只保留在 State，不保存 `.mmd` 文件；`final_citation_count` 取自报告实际引用的 `used_evidence_ids`。
- 当前 evaluator：
  - `researchops_summary_evaluator`：规则型实验级汇总，统计报告、持久化、证据、Review、安全、降级和 HITL 观测指标；证据、Review、安全和降级指标只统计实际包含对应业务字段的 runs。
  - `research_report_pairwise_preference`：LLM-as-Judge Pairwise A/B，从回答问题、证据支撑、覆盖、限制披露、引用、结构和安全七个维度比较报告。
  - `web_tool_stability_evaluator`：规则型 Web 工具稳定性评分；Web Search SubAgent 在原始工具输出产生后通过确定性逻辑提取 `web_tool_evaluation_records`，只保留工具名、成功状态、有效结果数、正文长度、失败类型和截断错误，并以列表累加 reducer 保留各轮紧凑记录；evaluator 按本次实际调用的 SerpAPI、Tavily、Context7 和 Playwright 工具归一化评分，没有 Web 工具调用的 run 标记为 `not_applicable`（输入已由主 Graph State 接线）。
  - `external_agent_worker_stability_evaluator`：规则型外部 worker 稳定性评分，读取迭代上下文中的 worker 结果；没有触发外部 worker 的 run 标记为 `not_applicable`。
- 当前 LangSmith dataset 定义：`researchops-input-guard-v1`、`researchops-standard-research-v1`、`researchops-url-web-search-v1`、`researchops-local-and-structured-v1`、`researchops-edge-cases-v1`。
- `src/dataset` 提供 dataset 创建、样例追加、split 管理和 Pairwise A/B 运行脚本；均为手动入口，不进入主 Graph。
- LangSmith Trace metadata 后续应记录：
  - `graph_version`
  - `prompt_version`
  - `model_name`
  - `source_types_used`
  - `evidence_count`
  - `conflict_count`
  - `hitl_triggered`
  - `degraded`
  - `review_score`
  - `safety_risk_level`
- 默认测试不得访问真实网络；联网测试必须单独标记。



## 10. 验收标准

- [x] Graph 可以从 `src.workflow.graph.graph` 导入，且 `scripts/run_smoke.py` 复用该入口。
- [ ] 真实 LLM 全链路 invoke 由手动 smoke 验证，不进入默认离线测试。
- [x] 静态编排图脚本和 `outputs/runs/researchops_graph.png` 已存在。
- [x] `persist_outputs` 保存报告、metrics 和实际运行链路 PNG，且不保存 `.mmd` 文件。
- [x] State 不存在全局 `max_steps`；检索、Review、安全分别独立计数。
- [x] `plan_research` 后固定并行调用 Web 与本地检索节点；结构化 mock 在本地节点内部处理。
- [x] Web 分支通过可选 HITL 和 `web_search_result_ready` 与本地分支汇聚到 `sanitize_and_cluster`。
- [x] 证据不足先进入 `check_step_budget`，再决定 `strategy_iteration` 或 `prepare_degraded_report`。
- [x] `prepare_degraded_report` 只设置降级状态，不生成报告正文。
- [x] Review 不通过且修正次数未耗尽时回到 `generate_research_report`。
- [x] Safety 不通过且安全修正次数未耗尽时进入降级报告路径。
- [x] State 业务对象使用稳定 ID 关联；`executed_nodes` 等并行路径使用累加 reducer；`evidence_items`/`conflicts` 整表替换。
- [x] 必需配置、Schema 和产物路径错误显式暴露，不以伪造结果隐藏。
- [x] 证据准入过滤失败/登录墙/无匹配占位；语义冲突启发式替代多源即冲突。
- [x] 报告与 Review 为 LLM 节点；生成消费 `revision_suggestions`；引用以合法 `evidence_id` 为准。
- [ ] 当前仓库没有 README；后续新增 README 或演示材料时必须与本 SPEC 对齐。



## 11. 需求追踪


| 需求 ID | SPEC 章节 | 实现位置 | 测试位置 | 状态 |
| --- | --- | --- | --- | --- |
| `REQ-001` | 3. 主流程 | `src/workflow/graph.py`、`edges.py`、各 `*_nodes.py` | `test_plan_research.py`、`test_web_search_subagent.py`、`test_evidence_sufficiency.py`、`test_graph_input_schema.py` | 部分完成：缺默认离线全链路 invoke 测试 |
| `REQ-002` | 5. 数据模型 | `src/schemas/state.py`、`src/llm/structured_outputs.py` | `test_state_reducers.py`、`test_graph_input_schema.py`、`test_evidence_quality_scores.py`、`test_evidence_conflict.py` | demo 级完成 |
| `REQ-003` | 5–6. Prompt 与模型 | `prompts/*.yml`、`src/llm/prompt_loader.py`、`src/config/settings.py` | `test_plan_research.py`、`test_workflow_config.py`、`test_structured_local_search.py`、`test_research_report_nodes.py` | demo 级完成 |
| `REQ-004` | 7. 数据与存储 | `src/workflow/artifact_nodes.py`、`outputs/` | `test_artifact_nodes.py` | demo 级完成 |
| `REQ-005` | 8. 安全 | `guard_nodes.py`、`search_nodes.py`、`external_agent_workers.py` | `test_web_hitl_devtools.py`、`test_external_agent_workers.py`、`test_strategy_external_worker.py` | 部分完成：HITL 为观测占位（非安全闭环），权限无统一注册表；真实 interrupt HITL 后续补齐 |
| `REQ-006` | 9. 可观察性 | `artifact_nodes.py`、`src/evaluators/`、`src/dataset/` | `test_*_evaluator.py`、`test_research_report_pairwise.py`、`test_create_langsmith_datasets.py` | 部分完成：Web evaluator 已接线；Trace metadata 与 latency/tokens 仍为后续 |


