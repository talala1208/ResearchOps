# ResearchOps Agent

基于 LangGraph 的证据驱动研究 Agent：把研究问题拆成子问题，并行检索 Web 与本地资料，完成证据准入、质量评分、冲突识别、充足性检查、报告 Review 与安全审查，最终产出带可追溯引用的 Markdown 报告。

## 解决什么问题

普通 LLM 一次性回答容易来源不清、证据不足却继续编造、冲突未披露、安全边界模糊。

ResearchOps 把研究流程做成有状态 Graph：拆分子问题 → 多源并行检索 → 证据准入与评分 → 充足性判断 → 报告生成 / Review / 安全审查 → 产出带可追溯引用的 Markdown 报告；证据不够或请求危险时主动降级，而不是硬写完。

## 工作流一览

Graph 入口：`src.workflow.graph.graph`。

![researchops_graph](docs/assets/graph/researchops_graph.png)

实际运行路径样例（标准输入及危险降级）：

![executed_degraded_input_guard](docs/assets/graph/sampel_executed_graph.png)

## 设计亮点（摘要）

- **LLM 只做语义判断，代码做治理**：调度、限流、证据清洗、分桶打分、预算与降级由确定性代码负责。
- **按工具特性分流**：Tavily / Context7 高相关片段直接作证据；Serp/ydc 经 LLM 重排，抓正文有 relevance 硬分否决。
- **结构化输出护栏**：YAML Prompt + JSON 示例 → coerce → Pydantic → 业务校验；规划失败可带错重试一次。
- **工具失败尽量软、契约错误要硬**：单工具 `ok=false` 不中断整图；节点契约破坏直接抛错，不用降级掩盖。
- **有界循环**：检索 / Review / Safety 三套预算独立；不足则降级，禁止无限补搜；控制预算的同时保证报告可信度。

详见 `[docs/项目说明.md](docs/项目说明.md)`。

## 核心能力


| 维度       | 内容                                                                                                   |
| -------- | ---------------------------------------------------------------------------------------------------- |
| Graph 编排 | 并行 fan-out / fan-in、条件路由、策略迭代回环、三套独立预算、降级路径                                                          |
| 多源检索     | Web：SerpAPI / you.com 主检索 + Tavily / Context7 辅助 + Playwright 抓正文；本地：Markdown 关键词、轻量 RAG、LLM 驱动结构化搜索 |
| 证据治理     | 稳定证据 ID、`score_bucket` 分桶打分、信任边界、冲突识别、引用白名单                                                          |
| 质量与安全    | 充足性与 Review 职责分离；Input Guard + 规则 Safety Review                                                      |
| 人机协作     | Web HITL（登录墙观测、不暂停）；冲突 HITL（真实 `interrupt` / `resume`）                                               |
| 可观测评测    | LangSmith Trace / Dataset / Experiment；证据利用率、降级率、来源分发等自定义指标                                          |


## LangSmith 正式样例评测

在 LangSmith Experiment 上跑固定评测集，关注 `evidence_utilization_rate`、`is_degraded`、`source_citation_count`、`source_dispatch_valid_count` 以及 Latency / Token。覆盖五类场景：

**常规公开资料研究**（`formal_general_research`）

![formal_general_research](docs/assets/langsmith/formal_general_research.png)

**指定 URL 研究**（`formal_url_research`）

![formal_url_research](docs/assets/langsmith/formal_url_research.png)

**本地资料 + Web 结合**（`formal_local_web_research`）

![formal_local_web_research](docs/assets/langsmith/formal_local_web_research.png)

**复杂多对象比较**（`formal_complex_research`）

![formal_complex_research](docs/assets/langsmith/formal_complex_research.png)

**危险输入直接降级**（`formal_dangerous_direct_degradation`）

Input Guard 拦截后 `is_degraded=1.0`，不进入正常检索与引用路径：

![formal_dangerous_direct_degradation](docs/assets/langsmith/formal_dangerous_direct_degradation.png)

## 快速开始

环境：Python 3.11–3.13，[uv](https://docs.astral.sh/uv/)，DashScope / OpenAI 兼容模型密钥；真实 Web 研究需 SerpAPI 或 you.com Search。

```bash
uv sync
cp .env.example .env
# 至少配置 DASHSCOPE_API_KEY、WEB_SEARCH_PROVIDER 与对应搜索密钥
# 可选：TAVILY_API_KEY、CONTEXT7_API_KEY、LANGSMITH_*
```



### CLI smoke

```bash
uv run python scripts/run_smoke.py \
  "比较 LangSmith、AgentOps 与 Arize Phoenix 在 Agent 观测和评估方面的定位差异"
```



### LangGraph Studio

用 Studio 可视化跑图、单步查看节点与状态：

```bash
./scripts/run_langgraph_dev.sh
```

启动后按终端提示打开 Studio。输入只接受：

```json
{
  "user_query": "你的研究问题"
}
```

预算与阈值只从 `.env` 读取，不能通过 Studio 输入覆盖。开启 `LANGSMITH_TRACING=true` 后，同一次运行也可在 LangSmith 中查看 Trace。

产物示例：

```text
outputs/reports/<run_id>.md
outputs/runs/<run_id>_metrics.json
outputs/runs/<run_id>_executed.png
```

报告样例（真实运行）：


| 文件                                                                                                     | 内容                                 | 为何选入                                  |
| ------------------------------------------------------------------------------------------------------ | ---------------------------------- | ------------------------------------- |
| `[examples/sample_report_agent_observability.md](examples/sample_report_agent_observability.md)`       | LangSmith / AgentOps / Phoenix 对比  | 最贴项目主题；证据含官方文档 + 本地 RAG / 本地文档，能看多源引用 |
| `[examples/sample_report_agent_metrics.md](examples/sample_report_agent_metrics.md)`                   | Agent 观测指标框架（工具失败 / 中断 / 人工接管）     | Review 分最高（0.92）；结构紧凑，边界披露清楚          |
| `[examples/sample_report_workflow_orchestration.md](examples/sample_report_workflow_orchestration.md)` | LangGraph / Temporal / Prefect 对比  | 复杂多对象比较；适用边界与选型建议完整                   |
| `[examples/sample_report_web_search_apis.md](examples/sample_report_web_search_apis.md)`               | SerpAPI / you.com / Tavily 对比      | 直接对应本项目 Web 检索栈，便于对照工具设计              |
| `[examples/sample_report_langgraph_url.md](examples/sample_report_langgraph_url.md)`                   | 指定 URL：LangGraph 持久化 / HITL / 状态编排 | URL 研究路径样例；短小完整，适合快速翻阅                |




## 文档与目录


| 路径                             | 说明                     |
| ------------------------------ | ---------------------- |
| `[docs/项目说明.md](docs/项目说明.md)` | 详细设计、安装配置、LangSmith 评测 |
| `src/workflow/`                | Graph、路由与节点            |
| `src/tools/`                   | Web / 本地检索与 MCP 工具     |
| `prompts/`                     | 版本化 YAML Prompt        |
| `src/evaluators/`              | LangSmith 自定义评测指标      |
| `examples/`                    | 报告样例与评测输入              |


