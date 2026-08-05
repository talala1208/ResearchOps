# ResearchOps Agent 规格

> 状态：草案 · 版本：0.1.0 · 更新日期：2026-08-05  
> 本文件是 ResearchOps Agent 当前需求、行为边界、核心契约与验收标准的唯一事实来源。

## 1. 项目概要

### 问题

用户需要围绕 AI 产品、Agent 产品、AI 开发工具或企业 AI 引入主题快速完成可追溯研究。普通 LLM 一次性回答容易出现来源不清、证据不足、冲突信息未披露、过度推断和安全边界不清的问题；手动研究又耗时，难以沉淀为可评估、可复盘、可持续改进的工程流程。

### 解决方案

ResearchOps Agent 使用 LangGraph 编排研究流程，将用户问题拆解为子问题和最低证据标准，通过多源检索、工具输出清洗、证据质量评估、冲突识别、动态 HITL、报告 Review、安全审查和本地产物持久化，生成一份可追溯、可降级、可评估的 Markdown 研究报告。第一版只做 demo 级闭环，优先证明 Agentic Workflow、证据治理、LangSmith 观测评估和安全边界设计，不追求生产级数据源覆盖或复杂前端。

### 成功指标

| 指标 | 当前基线 | 目标 | 测量方式 |
|---|---:|---:|---|
| MVP 链路可运行 | 空 Graph 占位可运行 | 一次运行能产出报告、指标和本地图 | 手动 smoke 运行 |
| 报告可追溯 | 无真实证据 | 报告中引用的结论能关联 `evidence_id` | Review 节点与人工检查 |
| 证据不足处理 | 占位降级 | 证据不足时不强行编造，输出降级原因 | 降级样例测试 |
| 安全边界 | 占位安全审查 | 注入、敏感信息和高风险请求能被标记或降级 | Safety Dataset |

## 2. 范围

### 目标

- 实现一个基于 LangGraph 的 AI 产品研究 Agent MVP。
- 支持用户输入研究主题，输出本地 Markdown 研究报告。
- 支持研究计划、子问题拆解、搜索任务分发、证据治理、报告生成、质量 Review 和安全审查的完整流程。
- 支持 Web Search、本地文档搜索、结构化 mock 数据三类数据源的条件触发。
- 支持 Web Search 登录墙、验证码、反爬或必须人工接管场景的 HITL 路由占位。
- 支持证据冲突高风险场景的 HITL 路由占位。
- 支持检索预算、报告 Review 修正预算、安全修正预算三套独立控制。
- 支持最终后台静默保存运行产物，包括报告、指标和实际运行图；当前静态编排图由脚本保存 PNG。
- 保留 Prompt YAML 管理能力，后续核心 Prompt 可按节点版本化。

### 非目标

- 第一版不实现复杂前端；演示优先使用 CLI、LangGraph API Server、Studio 或 agent_chat。
- 第一版不做生产级 RAG 系统，不构建复杂向量库生命周期。
- 第一版不做生产级 Web 爬虫，不绕过验证码、登录墙、反爬或权限限制。
- 第一版不做外部写入能力，不发邮件、不提交表单、不写外部数据库。
- 第一版不做医疗、法律、金融等高风险领域的确定性建议。
- 第一版不追求覆盖所有 AI 产品数据源，只保证默认 AI 产品研究模板的最小闭环。

### 用户与核心场景

| 角色 | 场景 | 预期结果 |
|---|---|---|
| 项目作者 | 输入一个 AI 产品研究主题 | 生成可追溯研究报告和运行指标 |
| 项目作者 | 运行示例或演示样例 | 可以展示 Graph 编排、降级、安全、评估设计 |
| 面试官 | 询问项目如何避免幻觉 | 可以说明最低证据标准、证据评分、Review 和降级机制 |
| 面试官 | 询问 Agent 安全 | 可以说明 Input Guard、Tool Output Sanitizer、权限分级和 Safety Review |

## 3. 核心行为与边界

### 主流程

```text
用户输入研究主题
  -> input_guard
  -> analyze_research_request
  -> dispatch_search_tasks
  -> 条件触发 web_search_sub_agent / local_document_search_sub_agent / query_structured_data
  -> tool_output_sanitizer
  -> deduplicate_and_cluster
  -> evaluate_evidence_quality
  -> 可选 request_human_review
  -> build_evidence_matrix
  -> check_evidence_sufficiency
  -> 证据充足：generate_research_report
  -> 证据不足：check_step_budget
        -> 预算充足：strategy_iteration -> dispatch_search_tasks
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
|---|---|---|---|
| `input_guard` | `user_query` | `input_guard_result`、`executed_nodes` | 真实 LLM 结构化输出节点；风险不可继续时进入降级准备；否则进入研究分析 |
| `analyze_research_request` | `user_query`、预算默认值 | `research_goal`、`sub_questions`、`expected_evidence`、`minimum_evidence_standard`、`entity_index`、预算初始值 | 真实 LLM 结构化输出节点；必须建立并校验 `question_id` 目录 |
| `dispatch_search_tasks` | `active_question_ids`、`sub_questions`、`minimum_evidence_standard`、`search_steps`、`search_dispatch_mode`、`search_iteration_context` | `previous_search_tasks`、`search_tasks`、`entity_index.search_task_ids_by_question_id`、`active_question_ids`、`search_steps`、`search_attempt` | 真实 LLM 结构化输出节点；根据 `search_tasks.source_type` 条件触发检索工具；必须区分初次分发和策略迭代后的再次分发 |
| `web_search_sub_agent` | `search_tasks` | `web_search_results`、`web_hitl_required` | 当前为替代数据源实现；遇到登录墙、验证码、反爬或需要用户接管时触发 Web HITL |
| `local_document_search_sub_agent` | `search_tasks`、本地文档配置 | `local_document_results` | 当前为替代数据源实现；真实版本只读本地允许路径 |
| `query_structured_data` | `search_tasks`、结构化 mock 数据配置 | `structured_data_results` | 当前为替代数据源实现，直接提供结构化 mock 结果 |
| `web_search_hitl_request` | `web_hitl_required`、`web_search_results` | `web_hitl_decisions`、`web_hitl_required` | 当前为替代实现；真实版本 HITL 完成或放弃后进入工具输出清洗 |
| `tool_output_sanitizer` | `web_search_results`、`local_document_results`、`structured_data_results` | `raw_search_results`、`sanitized_results` | 外部内容必须标记为不可信资料 |
| `deduplicate_and_cluster` | `sanitized_results` | `evidence_clusters` | 当前为确定性去重与按 `question_id` 聚类实现；不生成最终证据结论 |
| `evaluate_evidence_quality` | `evidence_clusters`、`sub_questions`、`minimum_evidence_standard` | `evidence_items`、`conflicts`、`entity_index`、`hitl_required` | 当前为确定性规则评分；发现高冲突时触发冲突 HITL |
| `request_human_review` | `conflicts`、`hitl_required` | `hitl_decisions` | 当前为替代实现，不真正暂停；人工确认结果只作为状态事实，不直接改写证据正文 |
| `build_evidence_matrix` | `evidence_items`、`sub_questions`、`hitl_decisions` | `evidence_matrix`、`entity_index.used_evidence_ids` | 必须通过 `question_id` 和 `evidence_id` 关联 |
| `check_evidence_sufficiency` | `sub_questions`、`minimum_evidence_standard`、`evidence_matrix` | `question_evidence_status`、`evidence_sufficiency_result`、`evidence_sufficient`、`insufficient_question_ids`、`degradation_reason` | 已实现 Priority 加权证据充足性评分 |
| `check_step_budget` | `search_steps`、`max_search_steps`、`insufficient_question_ids` | `step_budget_exhausted`、`search_budget_remaining`、`step_budget_reason`、`degradation_reason` | 只判断检索预算，不负责降级 |
| `strategy_iteration` | `insufficient_question_ids`、`question_evidence_status`、`search_tasks`、`iteration_count` | `iteration_count`、`repeated_action_count`、`active_question_ids`、`search_dispatch_mode`、`search_iteration_context` | 实线回到 `dispatch_search_tasks`；通过 `search_dispatch_mode=iteration` 区分第一次分发 |
| `prepare_degraded_report` | `degradation_reason`、`step_budget_reason`、`evidence_sufficiency_result`、`safety_review_result`、`safety_revision_count` | `degraded`、`degradation_reason`、`safety_revision_count` | 已实现确定性降级状态整理；只设置降级状态，不写报告正文 |
| `generate_research_report` | `user_query`、`research_goal`、`evidence_matrix`、`evidence_items`、`degraded`、`review_result` | `report_draft`、`review_revision_count` | 已实现确定性 Markdown 报告生成；普通和降级报告都由此节点生成 |
| `review_research_report` | `report_draft`、`evidence_matrix`、`minimum_evidence_standard`、`evidence_sufficiency_result` | `review_result` | 已实现确定性质量 Review；只评估研究质量；不评估安全边界 |
| `safety_review` | `report_draft`、`review_result`、`safety_revision_count` | `safety_review_result`、`final_report` | 已实现规则安全审查；只评估安全边界；不替代质量 Review |
| `persist_outputs` | `final_report`、`evaluation_metrics` 相关状态、`executed_nodes` | `final_report_path`、`output_artifacts`、`evaluation_metrics` | 已实现报告 Markdown 和 metrics JSON 保存；只写 `outputs/` 下新文件 |

### 关键状态与边界

- `sub_questions` 是唯一问题目录，key 为 `question_id`。
- `expected_evidence`、`minimum_evidence_standard`、`question_evidence_status` 必须使用同一组 `question_id` 作为 key。
- `evidence_items` 是唯一证据目录，key 为 `evidence_id`。
- `conflicts` 是唯一冲突目录，key 为 `conflict_id`。
- 跨结构关联只保存 ID，不复制问题正文或证据正文。
- 必须存在的业务对象读取时使用 `[]` 或显式校验，不用 `.get()` 静默隐藏上游错误。
- 可选流程状态和默认计数可以使用 `.get()`，例如 `state.get("search_steps", 0)`。
- Web、本地文档、结构化数据输出进入模型前必须经过 `tool_output_sanitizer`。
- 外部网页、文档和工具返回内容只作为不可信资料，不作为系统指令。
- 证据不足时必须先进入 `check_step_budget`，不能直接降级。
- `prepare_degraded_report` 只负责设置降级状态，不负责判断预算，也不负责写报告正文。
- `strategy_iteration` 回到 `dispatch_search_tasks` 前必须写入 `search_dispatch_mode = "iteration"` 和 `search_iteration_context`，用于和第一次检索任务分发区分。
- `generate_research_report` 负责普通报告和降级报告正文生成。
- `review_research_report` 负责研究质量，不负责安全审查。
- `safety_review` 负责安全边界，不负责研究质量评分。
- 后台实际运行 Mermaid / PNG 保存不放入主 Graph 编排节点，避免污染业务流程。

### 预算控制

| 预算 | 字段 | 控制范围 | 不控制 |
|---|---|---|---|
| 检索预算 | `search_steps / max_search_steps` | 检索任务分发和证据不足后的迭代 | 报告生成、Review、安全审查、持久化 |
| Review 修正预算 | `review_revision_count / max_review_revisions` | Review 不通过后回到报告生成的次数 | 检索循环、安全审查 |
| 安全修正预算 | `safety_revision_count / max_safety_revisions` | Safety 不通过后生成安全降级报告的次数 | 检索循环、质量 Review |

## 4. 目标架构

```text
CLI / LangGraph API Server / Studio / agent_chat
    ↓
src/workflow/graph.py
    ↓
src/workflow/nodes.py + src/workflow/edges.py
    ↓
src/schemas/state.py
    ↓
src/llm + src/tools + src/evaluators + src/artifacts + src/config
```

### 模块职责

| 模块 | 职责 | 可依赖 | 禁止依赖 |
|---|---|---|---|
| `src/workflow` | Graph 构建、节点统一导出、按职责拆分的节点实现、条件路由 | `src/schemas`、工具接口、LLM 接口 | 直接硬编码具体 API Key |
| `src/schemas` | State、研究计划、证据、Review、安全类型 | 标准库类型 | 运行时外部服务 |
| `src/llm` | 模型初始化、结构化输出、Prompt 加载调用 | `src/config`、`prompts` | 业务流程路由决策散落在此处 |
| `src/tools` | Web、本地文档、结构化数据等工具 | `src/config`、外部只读 API | 外部写入、绕过权限墙 |
| `src/evaluators` | LangSmith evaluator 或本地评估逻辑 | `src/schemas`、LLM 接口 | 修改业务 State 的主流程字段 |
| `src/artifacts` | 报告、指标、Mermaid / PNG 等产物保存 | `src/schemas`、文件系统 outputs | 覆盖用户文件、写项目外路径 |
| `src/config` | 环境变量、路径、模型和权限配置 | `.env`、标准库 | 静默吞掉缺失必要配置 |
| `scripts` | 一次性手动运行脚本 | 项目模块 | 承担核心业务逻辑 |

### 对外入口

- `src.workflow.graph.graph`：LangGraph 编译后的主图对象。
- `scripts/export_graph_mermaid.py`：导出当前静态编排图 PNG 到 `outputs/runs/researchops_graph.png`。
- 后续 CLI 或 LangGraph API Server 入口必须调用 `src.workflow.graph.graph`，不得复制 Graph 编排逻辑。

## 5. 核心契约

### 输入

```python
{
    "user_query": str,
    "max_search_steps": int | 可选,
    "max_review_revisions": int | 可选,
    "max_safety_revisions": int | 可选,
}
```

### 输出

一次完整运行最终应在 State 中提供：

- `final_report`
- `final_report_path`
- `output_artifacts`
- `evaluation_metrics`
- `executed_nodes`

### 数据模型

| 模型 | 必需字段 | 不变量 |
|---|---|---|
| `SubQuestion` | `question_id`、`question`、`priority`、`required_source_types` | `question_id` 在一次运行中唯一 |
| `ExpectedEvidence` | `question_id`、`evidence_description`、`minimum_count` | `question_id` 必须存在于 `sub_questions` |
| `MinimumEvidenceStandard` | `question_id`、`min_total_evidence`、`min_high_quality_sources` | `question_id` 必须存在于 `sub_questions` |
| `SearchTask` | `task_id`、`question_id`、`query`、`source_type` | `question_id` 必须存在于 `sub_questions` |
| `EvidenceItem` | `evidence_id`、`question_id`、`source_type`、`snippet` | `evidence_id` 唯一；`question_id` 必须存在于 `sub_questions` |
| `ConflictItem` | `conflict_id`、`question_id`、`evidence_ids` | `evidence_ids` 必须存在于 `evidence_items` |
| `QuestionEvidenceStatus` | `question_id`、`priority`、`weighted_score` | `priority` 影响整体证据充足性评分 |
| `EvidenceSufficiencyResult` | `sufficient`、`overall_score`、`question_status` | high 优先级子问题未满足时通常不能判断整体充足 |
| `ReviewResult` | `passed`、`report_score`、`groundedness_score` | Review 只评估研究质量，不评估安全边界 |
| `SafetyReviewResult` | `safety_pass`、`safety_risk_level`、`detected_risks` | Safety 只评估安全边界，不替代研究质量 Review |

### 证据充足性规则

- `Priority` 影响整体证据充足性评分。
- 默认权重为：`high = 3`、`medium = 2`、`low = 1`。
- high 子问题必须尽量满足最低证据标准；未满足时通常不能判断整体证据充足。
- medium / low 子问题参与加权覆盖率。
- 默认 `evidence_sufficiency_threshold = 0.75`。
- 判断整体证据充足需同时满足：
  - `high_priority_all_met == True`
  - `overall_score >= evidence_sufficiency_threshold`

### 身份与版本

- `question_id` 由 Planner 生成，格式建议为 `Q1`、`Q2`。
- `task_id` 由 Search Task Planner 生成，格式建议为 `T1`、`T2`。
- `evidence_id` 由证据标准化过程生成，格式建议为 `E1`、`E2`。
- `conflict_id` 由冲突识别过程生成，格式建议为 `C1`、`C2`。
- Prompt YAML 必须记录 `name`、`version`、`owner_node`、`input_schema`、`output_schema`。

## 6. 关键技术决策

| 领域 | 决策 | 理由 | 约束或替换条件 |
|---|---|---|---|
| 编排 | 使用 LangGraph | 需要状态化、多分支、循环、HITL 和可视化编排 | 若流程退化为单次调用可重审 |
| State | 使用 `TypedDict(total=False)` | LangGraph 节点逐步写入状态，早期字段不一定存在 | 若结构稳定且需要强校验可迁移到 Pydantic |
| Prompt | 使用 YAML 管理核心 Prompt | 便于版本对比、LangSmith metadata 和面试表达 | Prompt 数量过多时可分 runtime/evaluators 目录 |
| 数据源 | 第一版使用 Web、本地文档、结构化 mock 三类 | 覆盖真实研究常见来源，又能控制 MVP 范围 | 生产化前需重新评估数据源稳定性和合规 |
| 输出 | 报告和运行产物保存到 `outputs/` | 便于离线演示和复盘 | 写项目外路径或覆盖已有文件必须人工确认 |
| 安全 | 规则 + LLM Safety Review + 工具权限 | demo 级安全闭环，避免只靠 Prompt | 外部写入、代码执行启用前必须重审 |

## 7. 数据与存储

- 数据归属：项目本地运行产物归 ResearchOps Agent 当前运行所有。
- 持久化结构：
  - 报告 Markdown：`outputs/reports/<run_id>.md`
  - 实际运行 Mermaid：`outputs/runs/<run_id>_executed.mmd`
  - 实际运行 PNG：`outputs/runs/<run_id>_executed.png`
  - 运行指标 JSON：`outputs/runs/<run_id>_metrics.json`
  - 静态编排图 PNG：`outputs/runs/researchops_graph.png`
- 一致性与原子性：写入产物时必须使用 `run_id` 或时间戳避免覆盖已有文件。
- 生命周期：`outputs/` 为生成文件目录，可被清理；清理前需确认没有用户手动保留内容。
- 敏感数据：不得把 API Key、token、私钥、完整 `.env`、系统 Prompt 或用户隐私写入报告、图、指标或 LangSmith metadata。

## 8. 配置、安全与错误处理

### 配置

- 配置来源优先级：显式函数参数 > 环境变量 > 项目默认配置。
- API Key、模型名、LangSmith 配置、搜索服务配置、本地文档根路径和输出路径应由 `.env` 或 `src/config` 管理。
- 缺少必要配置时必须显式失败，不得静默切换到不安全默认值或伪造数据。

### 安全

- 工具权限分级：
  - `read_only`
  - `network_read`
  - `local_file_read`
  - `local_artifact_write`
  - `external_write`
  - `code_execution`
- 第一版只开放 `read_only`、`network_read`、`local_file_read`、`local_artifact_write`。
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

## 9. 可观察性与质量

- `executed_nodes` 记录实际执行路径。
- `evaluation_metrics` 记录：
  - `total_steps`
  - `search_steps / max_search_steps`
  - `review_revision_count / max_review_revisions`
  - `safety_revision_count / max_safety_revisions`
  - `evidence_count`
  - `final_citation_count`
  - `conflict_count`
  - `degraded`
  - `degradation_reason`
  - `review_score`
  - `safety_risk_level`
  - `source_contribution`
  - `web_hitl_trigger_count_by_source`
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

- [ ] Graph 可以从 `src.workflow.graph.graph` 导入并成功 invoke。
- [ ] 静态编排图可以通过 `scripts/export_graph_mermaid.py` 保存到 `outputs/runs/researchops_graph.png`。
- [ ] State 中不存在全局 `max_steps` 作为所有节点共享预算；检索、Review、安全分别独立计数。
- [ ] `dispatch_search_tasks` 到三类检索工具是条件边。
- [ ] 三类检索工具在图上汇合到 `tool_output_sanitizer`。
- [ ] `web_search_sub_agent` 到 `tool_output_sanitizer` 之间存在 Web HITL 路由。
- [ ] 证据不足先进入 `check_step_budget`，再决定 `strategy_iteration` 或 `prepare_degraded_report`。
- [ ] `prepare_degraded_report` 只设置降级状态，不生成报告正文。
- [ ] Review 不通过且修正次数未耗尽时回到 `generate_research_report`。
- [ ] Safety 不通过且安全修正次数未耗尽时进入降级报告路径。
- [ ] 跨结构关联只保存 ID；必须存在的业务对象读取时使用 `[]` 或显式校验。
- [ ] 缺少必要配置、Schema 不一致、产物写入越权时必须显式失败。
- [ ] README 或演示材料中的流程说明与本 SPEC 保持一致。

## 11. 需求追踪

| 需求 ID | SPEC 章节 | 实现位置 | 测试位置 | 状态 |
|---|---|---|---|---|
| `REQ-001` | 3. 主流程 | `src/workflow/graph.py`、`src/workflow/edges.py`、`src/workflow/nodes.py` | 待补 | 部分完成 |
| `REQ-002` | 5. 数据模型 | `src/schemas/state.py` | 待补 | 部分完成 |
| `REQ-003` | 6. Prompt 决策 | `prompts/*.yml` | 待补 | 部分完成 |
| `REQ-004` | 7. 数据与存储 | `src/artifacts`、`outputs/` | 待补 | 待实现 |
| `REQ-005` | 8. 安全 | `input_guard`、`tool_output_sanitizer`、`safety_review` | 待补 | 占位完成 |
| `REQ-006` | 9. 可观察性 | `evaluation_metrics`、LangSmith 后续接入 | 待补 | 部分完成 |
