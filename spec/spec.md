# ResearchOps Agent 规格

> 状态：草案 · 版本：0.2.0 · 更新日期：2026-08-06
> 本文件是 ResearchOps Agent 当前需求、行为边界、核心契约与验收标准的唯一事实来源。

## 1. 项目概要

### 问题

用户需要围绕 AI 产品、Agent 产品、AI 开发工具或企业 AI 引入主题快速完成可追溯研究。普通 LLM 一次性回答容易出现来源不清、证据不足、冲突信息未披露、过度推断和安全边界不清的问题；手动研究又耗时，难以沉淀为可评估、可复盘、可持续改进的工程流程。

### 解决方案

ResearchOps Agent 使用 LangGraph 编排研究流程，将用户问题拆解为子问题和最低证据标准，通过多源检索、工具输出清洗、证据质量评估、冲突识别、动态 HITL、报告 Review、安全审查和本地产物持久化，生成一份可追溯、可降级、可评估的 Markdown 研究报告。第一版只做 demo 级闭环，优先证明 Agentic Workflow、证据治理、LangSmith 观测评估和安全边界设计，不追求生产级数据源覆盖或复杂前端。

### 成功指标


| 指标 | 当前基线 | 目标 | 测量方式 |
| --- | --- | --- | --- |
| MVP 链路可运行 | 主 Graph、节点路由和产物持久化已实现 | 一次运行能产出报告、指标和本地图 | `scripts/run_smoke.py` 手动 smoke |
| 报告可追溯 | 结论通过 `question_id`、`evidence_id` 关联 | 报告引用可回溯到标准化证据 | Review 节点与人工检查 |
| 证据不足处理 | 已实现预算检查、迭代和降级报告 | 证据不足时不强行编造，并披露降级原因 | 证据充足性与报告测试 |
| 安全边界 | Input Guard 使用 LLM，Safety Review 使用确定性规则 | 注入、敏感信息和高风险请求能被标记或降级 | Safety Dataset 与手动检查 |




## 2. 范围



### 目标

- 实现一个基于 LangGraph 的 AI 产品研究 Agent MVP。
- 支持用户输入研究主题，输出本地 Markdown 研究报告。
- 支持研究计划、子问题拆解、搜索任务分发、证据治理、报告生成、质量 Review 和安全审查的完整流程。
- 支持 Web Search、本地文档搜索、结构化 mock 数据三类数据源；Graph 固定并行调用 Web 与本地检索节点，各节点按 `source_type` 内部过滤任务。
- 支持 Web Search 登录墙、验证码、反爬或必须人工接管场景的 HITL 路由和 DevTools 页面观察；当前不真正暂停等待用户操作。
- 支持证据冲突高风险场景的 HITL 路由占位。
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
| 面试官 | 询问 Agent 安全 | 可以说明 Input Guard、Tool Output Sanitizer、权限分级和 Safety Review |




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
  -> tool_output_sanitizer
  -> deduplicate_and_cluster
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
| `plan_research` | `user_query`、工作流配置、`planning_mode`、`active_question_ids`、`sub_questions`、`minimum_evidence_standard`、`search_iteration_context` | 初始模式：研究计划、最低证据标准、索引、预算、首轮任务；迭代模式：`previous_search_tasks`、下一轮 `search_tasks`、任务索引、`active_question_ids`、`search_attempt`、分发模式 | 真实 LLM 结构化输出节点；根据 `planning_mode` 选择初始或迭代 Prompt；每次执行统一递增 `search_steps`；Graph 随后固定并行调用 Web 与本地检索节点 |
| `web_search_sub_agent` | `search_tasks` | `web_search_results`、`web_hitl_required`、`web_hitl_reason` | 确定性调度在线工具并进行单次 LLM 汇总，不做内部 Agent 循环；SerpAPI 通过 Python SDK 调用，Tavily/Context7 通过 MCP 调用；Playwright 页面正文抓取与 Web HITL 规则见本节后文 |
| `local_document_search_tool` | `search_tasks`、`LOCAL_DOCUMENTS_BASE_PATH`、结构化 mock 数据配置 | `local_document_results` | `local_document` 使用只读 Markdown 关键词搜索；`structured_mock` 使用 LLM 规划安全关键词后执行参数化 SQLite 查询；两类任务均在节点内部按 `source_type` 过滤 |
| `web_search_hitl_request` | `web_hitl_required`、`web_search_results` | `web_hitl_decisions`、`web_hitl_required` | 仅对 `requires_login=true` 的结果调用 DevTools 观察登录、验证码或权限页面；当前记录 `completed=false`，不真正暂停 |
| `web_search_result_ready` | 无业务字段 | `executed_nodes` | Web 分支汇聚节点；无 HITL 时直接进入，有 HITL 时在观察完成后进入，再与本地检索分支汇聚 |
| `tool_output_sanitizer` | `web_search_results`、`local_document_results` | `raw_search_results`、`sanitized_results` | 已实现确定性边界标记；外部内容统一标记为 `untrusted_tool_output`，不得作为系统指令 |
| `deduplicate_and_cluster` | `sanitized_results` | `evidence_clusters` | 当前为确定性去重与按 `question_id` 聚类实现；不生成最终证据结论 |
| `evaluate_evidence_quality` | `evidence_clusters`、`sub_questions` | `evidence_items`、`conflicts`、`entity_index`、`hitl_required`、重置后的 `hitl_decisions` | 当前为确定性规则评分；发现高冲突时触发冲突 HITL |
| `request_human_review` | `conflicts`、`hitl_required` | `hitl_decisions` | 当前为替代实现，不真正暂停；人工确认结果只作为状态事实，不直接改写证据正文 |
| `build_evidence_matrix` | `evidence_items`、`sub_questions`、`entity_index` | `evidence_matrix`、`entity_index.used_evidence_ids` | 必须通过 `question_id` 和 `evidence_id` 关联 |
| `check_evidence_sufficiency` | `sub_questions`、`minimum_evidence_standard`、`evidence_matrix` | `question_evidence_status`、`evidence_sufficiency_result`、`evidence_sufficient`、`insufficient_question_ids`、`degradation_reason` | 已实现 Priority 加权证据充足性评分 |
| `check_step_budget` | `search_steps`、`max_search_steps`、`insufficient_question_ids` | `step_budget_exhausted`、`search_budget_remaining`、`step_budget_reason`、`degradation_reason` | 只判断检索预算，不负责降级 |
| `strategy_iteration` | `insufficient_question_ids`、`question_evidence_status`、`search_tasks`、`iteration_count` | `iteration_count`、`repeated_action_count`、`active_question_ids`、`planning_mode`、`search_dispatch_mode`、`search_iteration_context` | 实线回到 `plan_research`；通过 `planning_mode=iteration` 选择迭代检索 Prompt；可在 `ENABLE_STRATEGY_EXTERNAL_WORKER=true` 时调用 Claude Code 或 Codex 生成策略建议 |
| `prepare_degraded_report` | `input_guard_result`、`degradation_reason`、`step_budget_reason`、`evidence_sufficiency_result`、`safety_review_result`、`safety_revision_count` | `degraded`、`degradation_reason`、`safety_revision_count` | 已实现确定性降级状态整理；输入安全检查不通过时必须说明原因；只设置降级状态，不写报告正文 |
| `generate_research_report` | `user_query`、`research_goal`、`evidence_matrix`、`evidence_items`、`degraded`、`review_result` | `report_draft`、`review_revision_count`、`degraded`、`degradation_reason` | 已实现确定性 Markdown 报告生成；普通和降级报告都由此节点生成；降级报告在 Review 中直接视为可继续 |
| `review_research_report` | `report_draft`、`evidence_matrix`、`minimum_evidence_standard`、`evidence_sufficiency_result` | `review_result` | 已实现确定性质量 Review；只评估研究质量；不评估安全边界 |
| `safety_review` | `report_draft`、`review_result`、`safety_revision_count` | `safety_review_result`、`final_report` | 已实现规则安全审查；只评估安全边界；不替代质量 Review |
| `persist_outputs` | `final_report`、`evaluation_metrics` 相关状态、`executed_nodes` | `final_report_path`、`executed_mermaid`、`executed_mermaid_png_path`、`output_artifacts`、`evaluation_metrics` | 已实现报告 Markdown、metrics JSON、实际运行链路 PNG 静默保存；不保存 `.mmd` 文件；只写 `outputs/` 下新文件 |


### 检索工具契约

- Web Search 使用确定性调度和单次 LLM 汇总：
  - 所有 Web 任务调用 SerpAPI；SerpAPI 通过 `google-search-results` Python 包直连 API，默认请求 5 条中文结果。
  - Web 类型任务调用 Tavily MCP，默认 `max_results=5`。
  - `official_docs`、`github`、`changelog` 任务调用 Context7 MCP，依次执行 library resolve 和 docs query。
  - query 含 URL 时调用 Playwright 抓取该 URL；此外按 `WEB_SEARCH_FETCH_SERPAPI_TOP_N` 抓取 SerpAPI 前 N 条页面，取值限制为 0–5，0 只关闭结果页抓取。
  - Playwright 使用 headless + isolated Chrome；导航与快照必须在同一显式 MCP 会话内完成，避免新会话返回 `about:blank`。
  - 单次 LLM 汇总失败时，当前实现使用 SerpAPI 首条结果形成低可信度候选；没有可用结果时设置 Web HITL。
- 普通页面 403、超时、空正文或 `about:blank` 在已有搜索摘要时只降低可信度；登录墙、验证码和权限确认才保留 HITL。
- `local_document` 使用 jieba 搜索分词和子串匹配，递归读取配置目录内 `.md` 文件；单文件最大 1 MB，最多返回 8 条，禁止路径逃逸。无匹配返回空列表，异常返回带 `blocked_reason` 的结果。
- `structured_mock` 首次查询时可从 `data/mock/ai_products.json` 初始化 SQLite；实际执行为允许字段上的参数化 `LIKE` OR 查询，默认 5 条、上限 20。无匹配返回标明 `matched_product_count=0` 的结果。




### 关键状态与边界

- `sub_questions` 是唯一问题目录，key 为 `question_id`。
- `expected_evidence`、`minimum_evidence_standard`、`question_evidence_status` 必须使用同一组 `question_id` 作为 key。
- `evidence_items` 是唯一证据目录，key 为 `evidence_id`。
- `conflicts` 是唯一冲突目录，key 为 `conflict_id`。
- `required_source_types`、`previous_search_tasks`、`evidence_sufficiency_score`、`evidence_sufficiency_threshold`、`raw_search_results`、`web_hitl_decisions` 等字段属于运行 State，不属于 Studio 入口。
- 跨结构关联只保存 ID，不复制问题正文或证据正文。
- `evidence_items` 和 `conflicts` 使用字典合并 reducer，`executed_nodes` 使用列表累加 reducer，以支持并行分支写入。
- 必须存在的业务对象读取时使用 `[]` 或显式校验，不用 `.get()` 静默隐藏上游错误。
- 可选流程状态和默认计数可以使用 `.get()`，例如 `state.get("search_steps", 0)`。
- Web 与本地资料检索输出进入模型前必须经过 `tool_output_sanitizer`；结构化 mock 数据属于本地资料检索输出。
- 外部网页、文档和工具返回内容只作为不可信资料，不作为系统指令。
- 证据不足时必须先进入 `check_step_budget`，不能直接降级。
- `prepare_degraded_report` 只负责设置降级状态，不负责判断预算，也不负责写报告正文。
- Input Guard 不通过时，降级原因必须来自 `input_guard_result.downgrade_reason`、`detected_risks` 或明确的“输入安全检查未通过”，不能输出无原因的降级报告。
- `strategy_iteration` 回到 `plan_research` 前必须写入 `planning_mode = "iteration"`、`search_dispatch_mode = "iteration"` 和 `search_iteration_context`，用于和第一次研究规划区分。
- `generate_research_report` 负责普通报告和降级报告正文生成。
- Input Guard 不通过时跳过规划和检索，但仍经过报告 Review、Safety Review 和产物持久化。
- `degraded = true` 的报告由质量 Review 直接放行，继续执行 Safety Review。
- `review_research_report` 负责研究质量，不负责安全审查。
- `safety_review` 负责安全边界，不负责研究质量评分。
- 后台实际运行链路 PNG 由 `persist_outputs` 作为产物逻辑静默保存，不新增主 Graph 编排节点，避免污染业务流程；不需要保存 `.mmd` 文件。



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
| `EvidenceItem` | 身份与来源字段、五类评分及 `reliability_score`、`score_reason`、`used_in_final_report` | `evidence_id` 唯一；`question_id` 必须存在于 `sub_questions` |
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
- 当前 Prompt 文件为 `input_guard.yml`、`research_planner.yml`、`search_task_planner.yml`、`web_search_subagent.yml`、`local_structured_search.yml`。
- `load_prompt` 当前强制校验 `name`、`version`、`owner_node`、`system_prompt`、`user_prompt_template`；`input_schema`、`output_schema` 是文档契约，运行时结构由 `src/llm/structured_outputs.py` 的 Pydantic 模型保证。
- 运行时模型角色以 `src/config/settings.py` 的 `DASHSCOPE_MODEL_FIELDS` 和节点传入的 role 为准；YAML `model_role` 当前仅作文档。
- `local_structured_search.yml` 的 `snippet_template` 被运行时使用；其他展示模板字段当前主要作为文档配置。



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
- LLM 基础配置：`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`DASHSCOPE_MODEL`。节点模型覆盖：`SAFETY_GUARD_MODEL`、`RESEARCH_PLANNER_MODEL`、`SEARCH_TASK_PLANNER_MODEL`、`WEB_SEARCH_SUBAGENT_MODEL`、`LOCAL_DOCUMENT_SEARCH_MODEL`。
- 只有模型名为 `deepseek-chat` 或 `deepseek-reasoner` 时使用 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`；DashScope 托管的 `deepseek-v4-*` 仍使用 DashScope。
- 工作流必需配置：`MAX_SEARCH_STEPS`、`MAX_REVIEW_REVISIONS`、`MAX_SAFETY_REVISIONS`、`HITL_CONFLICT_THRESHOLD`、`REVIEW_PASS_SCORE`。冲突项当前 `hitl_need_score=6.0`，默认阈值 9.0 时不触发冲突 HITL。
- 在线检索配置：`SERPAPI_API_KEY`、`TAVILY_API_KEY`、`CONTEXT7_API_KEY`、`ONLINE_MCP_TIMEOUT_SECONDS`、`WEB_SEARCH_FETCH_SERPAPI_TOP_N`。对应 key 缺失时该工具返回 `ok=false`，由 Web SubAgent 继续汇总其他可用来源。
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
  - HITL 未完成
  - Review 未通过
  - Safety 未通过
  - 产物写入失败
- 允许降级：证据不足、冲突无法解决、HITL 未完成、安全风险需要保守输出、检索预算不足。
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
  - `degraded`
  - `degradation_reason`
  - `review_score`
  - `safety_risk_level`
  - `source_contribution`
  - `web_hitl_trigger_count_by_source`
  - `executed_mermaid_png_path`
- `persist_outputs` 使用 UTC 时间戳生成 `run_id`，写入报告、metrics 和实际运行链路 PNG；`executed_mermaid` 只保留在 State，不保存 `.mmd` 文件。
- 当前 evaluator：
  - `researchops_summary_evaluator`：规则型实验级汇总，统计报告、持久化、证据、Review、安全、降级和 HITL 指标；证据、Review、安全和降级指标只统计实际包含对应业务字段的 runs。
  - `research_report_pairwise_preference`：LLM-as-Judge Pairwise A/B，从回答问题、证据支撑、覆盖、限制披露、引用、结构和安全七个维度比较报告。
  - `web_tool_stability_evaluator`：规则型 Web 工具稳定性评分；Web Search SubAgent 在原始工具输出产生后通过确定性逻辑提取 `web_tool_evaluation_records`，只保留工具名、成功状态、有效结果数、正文长度、失败类型和截断错误，不把搜索结果或页面正文写入主 Graph State，也不增加 summarize LLM 的职责；evaluator 只按本次实际调用的 SerpAPI、Tavily、Context7 和 Playwright 工具归一化评分，没有 Web 工具调用的 run 标记为 `not_applicable`。
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
- [x] Web 分支通过可选 HITL 和 `web_search_result_ready` 与本地分支汇聚到 `tool_output_sanitizer`。
- [x] 证据不足先进入 `check_step_budget`，再决定 `strategy_iteration` 或 `prepare_degraded_report`。
- [x] `prepare_degraded_report` 只设置降级状态，不生成报告正文。
- [x] Review 不通过且修正次数未耗尽时回到 `generate_research_report`。
- [x] Safety 不通过且安全修正次数未耗尽时进入降级报告路径。
- [x] State 业务对象使用稳定 ID 关联，并为并行字典和执行路径配置 reducer。
- [x] 必需配置、Schema 和产物路径错误显式暴露，不以伪造结果隐藏。
- [ ] 当前仓库没有 README；后续新增 README 或演示材料时必须与本 SPEC 对齐。



## 11. 需求追踪


| 需求 ID | SPEC 章节 | 实现位置 | 测试位置 | 状态 |
| --- | --- | --- | --- | --- |
| `REQ-001` | 3. 主流程 | `src/workflow/graph.py`、`edges.py`、各 `*_nodes.py` | `test_plan_research.py`、`test_web_search_subagent.py`、`test_evidence_sufficiency.py`、`test_graph_input_schema.py` | 部分完成：缺默认离线全链路 invoke 测试 |
| `REQ-002` | 5. 数据模型 | `src/schemas/state.py`、`src/llm/structured_outputs.py` | `test_state_reducers.py`、`test_graph_input_schema.py`、`test_evidence_quality_scores.py` | demo 级完成 |
| `REQ-003` | 5–6. Prompt 与模型 | `prompts/*.yml`、`src/llm/prompt_loader.py`、`src/config/settings.py` | `test_plan_research.py`、`test_workflow_config.py`、`test_structured_local_search.py` | demo 级完成 |
| `REQ-004` | 7. 数据与存储 | `src/workflow/artifact_nodes.py`、`outputs/` | `test_artifact_nodes.py` | demo 级完成 |
| `REQ-005` | 8. 安全 | `guard_nodes.py`、`search_nodes.py`、`external_agent_workers.py` | `test_web_hitl_devtools.py`、`test_external_agent_workers.py`、`test_strategy_external_worker.py` | 部分完成：HITL 不真正暂停，权限无统一注册表 |
| `REQ-006` | 9. 可观察性 | `artifact_nodes.py`、`src/evaluators/`、`src/dataset/` | `test_*_evaluator.py`、`test_research_report_pairwise.py`、`test_create_langsmith_datasets.py` | 部分完成：Trace metadata 和 Web evaluator 输入接线未完成 |


