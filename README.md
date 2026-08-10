# ResearchOps Agent

**基于 [LangGraph](https://github.com/langchain-ai/langgraph) 的证据驱动研究 Agent：把研究问题拆成子问题，并行检索 Web 与本地资料，完成证据准入、质量评分、冲突识别、充足性检查、报告 Review 与安全审查，最终产出带可追溯引用的 Markdown 报告。**

项目侧重展示 Agent 工程能力，而不是简单的「搜索 + LLM」封装：

- **有状态 Graph**：并行分支、汇聚、条件路由、预算循环与降级路径
- **证据治理**：稳定证据 ID、准入过滤、质量评分、冲突识别与引用回溯
- **质量闭环**：报告生成、质量 Review、安全 Review 使用独立修正预算
- **多源检索**：Web、本地 Markdown、本地向量 RAG、结构化 mock 数据
- **可观察与可评估**：LangSmith Trace / Dataset / Experiment，配合证据利用率、降级、引用与工具分发等自定义指标

## 工作流

可执行 Graph 入口：`src.workflow.graph.graph`。行为契约见 `[spec/spec.md](spec/spec.md)`。

### Graph 设计图

静态编排图（`scripts/export_graph_mermaid.py` 导出），包含并行检索、策略迭代回环、报告 Review / 安全修正，以及两条 HITL 分支：

researchops_graph

### 实际执行拓扑

每次真实运行会保存 `outputs/runs/<run_id>_executed.png`：实线为本次走过的路径，虚线为未执行分支；并行检索区域标注 fan-out / fan-in。

**标准研究路径**：Web 与本地并行检索后进入证据治理与报告闭环；策略迭代、冲突 HITL、Web HITL 在本趟未触发：

sample_executed_topology

**危险输入降级路径**：`input_guard` 拦截后直达 `prepare_degraded_report`，跳过检索与证据链：

executed_degraded_input_guard

## 安装

环境要求：macOS / Linux，Python 3.11–3.13，[uv](https://docs.astral.sh/uv/)，以及可用的 DashScope / OpenAI 兼容模型密钥。真实 Web 研究需要 SerpAPI 或 you.com Search。

```bash
uv sync
cp .env.example .env
```

最小配置示例：

```dotenv
DASHSCOPE_API_KEY=你的模型密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3.7-plus

WEB_SEARCH_PROVIDER=serp
SERPAPI_API_KEY=你的搜索密钥

# LangSmith 观测（可选，但推荐开启）
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的 LangSmith 密钥
LANGSMITH_PROJECT=research-agent
```

使用 you.com Search 时，将 `WEB_SEARCH_PROVIDER` 改为 `ydc` 并配置 `YDC_API_KEY`。不要提交 `.env`。

可选本地能力：

- `LOCAL_DOCUMENTS_BASE_PATH`：受限只读 Markdown 根目录
- `LOCAL_RAG_PERSIST_PATH`：预构建 parquet 向量库（默认 `data/resources/union.parquet`）
- `data/mock/ai_products.json`：结构化 mock 数据

## 使用

### 端到端运行

```bash
uv run python scripts/run_smoke.py \
  "比较 LangSmith、AgentOps 与 Arize Phoenix 在 Agent 观测和评估方面的定位差异"
```

检索、Review 与安全修正预算由 `.env` 中的 `MAX_SEARCH_STEPS`、`MAX_REVIEW_REVISIONS`、`MAX_SAFETY_REVISIONS` 控制。

运行结束后查看：

```text
outputs/reports/<run_id>.md
outputs/runs/<run_id>_metrics.json
outputs/runs/<run_id>_executed.png
```

报告样例：`[examples/sample_report.md](examples/sample_report.md)`。

### LangGraph Studio

```bash
./scripts/run_langgraph_dev.sh
```

Studio 输入只接受：

```json
{
  "user_query": "你的研究问题"
}
```

预算与阈值只从环境变量读取，不能通过 Studio 输入覆盖。

## LangSmith 监测与优化

ResearchOps 把运行链路接到 LangSmith，用来做 Trace 复盘、Dataset 回归评测和指标驱动优化。正式评测样例覆盖五类场景：常规公开资料研究、指定 URL 研究、本地与 Web 结合、复杂多对象比较、危险输入直接降级。

评测关注的自定义指标包括：


| 指标                            | 作用                   |
| ----------------------------- | -------------------- |
| `evidence_utilization_rate`   | 报告是否真正消费了检索到的证据      |
| `is_degraded`                 | 是否进入降级路径（危险输入、证据不足等） |
| `source_citation_count`       | 最终报告对各来源的引用分布        |
| `source_dispatch_valid_count` | Web / 本地工具分发与有效结果情况  |
| Latency / Token Usage         | 时延与成本，用于定位瓶颈与优化预算    |


样例与 Dataset 维护入口：`src/dataset/append_questions_to_dataset.py`。Evaluator 实现位于 `src/evaluators/`。

### 正式样例实验截图

**常规公开资料研究**（`formal_general_research`）

formal_general_research

**指定 URL 研究**（`formal_url_research`）

formal_url_research

**本地资料 + Web 结合**（`formal_local_web_research`）

formal_local_web_research

**复杂多对象比较**（`formal_complex_research`）

formal_complex_research

**危险输入直接降级**（`formal_dangerous_direct_degradation`）

危险请求在 Input Guard 阶段被拦截，`is_degraded=1.0`，不进入正常检索与引用路径：

formal_dangerous_direct_degradation

### 如何用这些指标做优化

1. **证据质量**：`evidence_utilization_rate` 偏低时，优先检查证据准入、报告证据供给预算，以及 Review 是否要求了无法落地的补搜。
2. **工具成本**：从 `source_dispatch_valid_count` 看 Tavily / Playwright / Context7 / 主搜索的调用是否过密，再调 `WEB_SEARCH_`* 与任务并发。
3. **时延与 Token**：复杂研究与 URL 抓取通常最贵；可下调检索条数、正文截断长度、精选长文条数，或收紧 `MAX_SEARCH_STEPS`。
4. **安全与降级**：危险样例应稳定落到 `is_degraded=1`；若误伤常规研究，回看 Input Guard Prompt 与阈值。
5. **回归对比**：同一 Dataset 在 Prompt / 模型 / 预算变更后重跑 Experiment，对比利用率、降级率、延迟和 Token 变化。

