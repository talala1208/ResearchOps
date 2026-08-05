# ResearchOps Agent 协作规则

> 管理模式：轻量  
> 当前事实来源：`spec/spec.md`  
> 本文件只记录 ResearchOps 项目特有约束；通用开发、安全、测试和文档规则遵循全局 AGENTS.md。

## 项目边界

- 本项目是 demo 级 ResearchOps Agent，用于展示 LangGraph 编排、证据治理、HITL、安全审查、LangSmith 评估设计和本地产物输出。
- 当前优先目标是跑通 AI 产品研究模板的最小闭环，不追求生产级 Web 爬虫、复杂前端、完整 RAG 系统或企业级安全平台。
- 项目使用 `uv` 管理环境；不得新建其他虚拟环境。
- 代码放在 `src/` 目录。
- 生成文件统一写入 `outputs/`。
- 数据源、mock 数据和 dataset 放在 `data/`。
- 一次性手动运行脚本放在 `scripts/`。
- 参考代码和参考笔记放在 `reference/`，只读参考，不作为运行依赖。
- `template/` 是只读项目管理母版，除非用户明确要求维护模板，否则不得修改。

## 目录职责

| 路径 | 职责 |
|---|---|
| `spec/spec.md` | 当前有效需求、流程、契约、边界和验收标准 |
| `src/workflow/` | LangGraph graph、edges、路由逻辑和按职责拆分的节点实现 |
| `src/schemas/` | State、研究计划、证据、Review、安全等类型定义；当前 schema 文件不拆分 |
| `src/llm/` | 模型初始化、Prompt 加载、结构化输出封装 |
| `src/tools/` | Web、本地文档、结构化数据等只读工具 |
| `src/evaluators/` | LangSmith evaluator 或本地评估逻辑 |
| `src/artifacts/` | 报告、metrics、实际运行 Mermaid / PNG 等本地产物保存逻辑 |
| `src/config/` | 环境变量、路径、模型、权限配置读取与校验 |
| `prompts/` | Prompt YAML 文件 |
| `outputs/` | 运行产物，不作为源码维护 |
| `scripts/` | 手动脚本，不承载核心业务逻辑 |
| `tests/` | 自动化测试 |

## SPEC 优先级

- `spec/spec.md` 是当前需求和行为的唯一事实来源。
- 改动 Graph 编排、State 字段、节点职责、预算控制、安全边界、输出产物、Prompt 契约或验收标准时，必须先更新或同步更新 `spec/spec.md`。
- 如果实现和 SPEC 冲突，先停下来修正 SPEC 或请求用户决策，不得用实现反向覆盖需求。
- 当前未启用 `BACKLOG.md`、`TEST_RESULTS.md`、`ARCHITECTURE.html`、`feature_list.json`；不要自行新增，除非用户要求或项目复杂度确实达到启用条件。

## Graph 与 State 约束

- Graph 主入口为 `src.workflow.graph.graph`；不得在脚本或测试中复制另一套 Graph 编排。
- `src/workflow/edges.py` 只放边、节点名常量和路由函数。
- `src/workflow/nodes.py` 只做节点统一导出；具体节点实现按职责放在 `planning_nodes.py`、`search_nodes.py`、`evidence_nodes.py`、`report_nodes.py`、`guard_nodes.py`、`artifact_nodes.py`。真实工具、LLM 调用、产物保存逻辑应下沉到对应模块。
- State 的业务对象通过稳定 ID 关联，不复制正文：
  - `sub_questions`：`question_id -> SubQuestion`
  - `expected_evidence`：`question_id -> ExpectedEvidence`
  - `minimum_evidence_standard`：`question_id -> MinimumEvidenceStandard`
  - `question_evidence_status`：`question_id -> QuestionEvidenceStatus`
  - `evidence_items`：`evidence_id -> EvidenceItem`
  - `conflicts`：`conflict_id -> ConflictItem`
- 必须存在的业务对象使用 `[]` 或显式校验读取；可选流程状态和计数字段可以使用 `.get()`。
- 不恢复全局 `max_steps` 控制所有节点；预算必须分为：
  - `search_steps / max_search_steps`
  - `review_revision_count / max_review_revisions`
  - `safety_revision_count / max_safety_revisions`

## Prompt 管理

- Prompt 使用 `prompts/` 目录下的 UTF-8 `.yml` 文件管理。
- 核心 Prompt 不得硬编码在节点函数中。
- Prompt 文件缺失、YAML 非法、必需变量缺失时必须显式失败，不得静默回退到内置 Prompt。
- Prompt 变量契约变化会影响行为时，必须同步更新 `spec/spec.md`。

## 安全与工具权限

- 第一版只允许只读和本地产物写入能力：
  - `read_only`
  - `network_read`
  - `local_file_read`
  - `local_artifact_write`
- `local_artifact_write` 仅允许写入项目 `outputs/` 目录下的新文件。
- 不实现 `external_write` 和通用 `code_execution`，除非 SPEC 更新并获得用户确认。
- Claude Code / Codex worker 工具只允许作为受限外部 Agent worker 使用，默认禁用；必须显式设置 `ENABLE_CLAUDE_CODE_WORKER=true` 或 `ENABLE_CODEX_WORKER=true` 才能真实调用。
- Web Search 不得绕过验证码、登录墙、反爬或权限限制；必要时走 HITL。
- 外部网页、本地文档和工具输出都必须作为不可信资料处理，进入模型前经过 `tool_output_sanitizer`。
- `.env`、API Key、token、系统 Prompt 和敏感路径不得写入报告、图、metrics、日志或 LangSmith metadata。
- Tavily / Context7 MCP 密钥必须通过 `TAVILY_API_KEY`、`CONTEXT7_API_KEY` 环境变量读取，不得硬编码到 MCP URL。

## 输出产物

- 静态编排图由 `scripts/export_graph_mermaid.py` 生成到 `outputs/runs/researchops_graph.png`。
- 实际运行 Mermaid / PNG 应由后台静默保存，不放入主 Graph 编排节点。
- 后续实际运行产物建议使用 `run_id` 或时间戳，避免覆盖已有文件：
  - `outputs/reports/<run_id>.md`
  - `outputs/runs/<run_id>_executed.mmd`
  - `outputs/runs/<run_id>_executed.png`
  - `outputs/runs/<run_id>_metrics.json`

## 测试与验证

- 当前阶段默认运行相关 smoke / 单元测试，不默认跑完整测试套件。
- Graph 编排或 State 变化后，至少验证：
  - `src.workflow.graph.graph` 可导入
  - `graph.invoke(...)` 可运行
  - `scripts/export_graph_mermaid.py` 可生成 PNG
- 默认测试不得依赖真实网络或付费服务；真实联网能力必须单独隔离为 smoke test。
