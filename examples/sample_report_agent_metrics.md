> 真实运行样例 · Agent 观测指标框架 · `run_id=20260809T064007143030Z_e9c02f42`
>
> 公开资料可能随时间变化。

# Agent 产品选型说明：超越模型回答质量的关键观测指标框架

## 1. 核心主张：为何仅看“回答质量”不足以评估 Agent

Agent 与单次 LLM 调用的本质区别在于其多步推理、工具使用与自主决策能力。这导致两个关键风险：第一，**失败具有隐蔽性**，Agent 可能在中间步骤选错工具或陷入循环，但最终仍生成看似合理的回答 [E4][E16]；第二，**成本与风险不可控**，Agent 自主决定调用次数与路径，若缺乏过程监控，极易出现 Token 消耗失控或任务静默失败 [E4][E16]。因此，产品负责人在选型时，必须将“工具调用失败”、“任务中断”和“人工接管”作为与模型回答质量同等重要的独立观测维度。

LLM 评估验证 Agent “能否工作”，而可观测性验证 Agent “是否在生产中稳定工作” [E10]。以下框架基于公开资料，为这三个维度提供可落地的指标定义。

## 2. 关键观测指标框架

### 2.1 工具调用失败率 (Tool Call Failure Rate)

该指标衡量 Agent 执行外部动作的可靠性，是预测任务最终成败的先行指标。

*   **定义**：在 Agent 运行轨迹（Trace）中，工具调用返回错误、超时或参数校验失败的次数占总工具调用次数的比例 [E5][E6]。
*   **计算公式**：
    $$ \text{Tool Failure Rate} = \frac{\sum \text{Failed Tool Calls}}{\sum \text{Total Tool Calls}} \times 100\% $$
*   **落地要点**：
    *   **分层归因**：需区分“模型侧失败”（如参数格式错误、选错工具）与“系统侧失败”（如 API 404/500、超时）。Arize 等平台建议将工具评估拆解为“工具选择正确性”、“参数格式化正确性”和“结果处理有效性”三个子指标 [E7]。
    *   **轨迹关联**：单纯统计错误数不够，必须将失败挂载到具体 Trace ID，因为一个任务可能包含多次重试，仅看聚合数据会掩盖特定场景下的系统性缺陷 [E4][E16]。

### 2.2 任务中断/完成率 (Task Interruption / Completion Rate)

该指标直接反映 Agent 的业务交付价值，区别于单纯的对话轮次或响应状态码。

*   **定义**：成功达成预设业务目标的任务数占总尝试任务数的比例。与之对应的是“中断率”，即因死循环、Token 耗尽、安全拦截或未定义异常导致任务提前终止的比例 [E3][E5]。
*   **计算公式**：
    $$ \text{Task Success Rate (TSR)} = \frac{\text{Completed Tasks}}{\text{Attempted Tasks}} \times 100\% $$
    *注：行业基准参考值为 TSR >85% 为优秀，<75% 需告警 [E3]。*
*   **落地要点**：
    *   **明确完成标准**：必须在选型前为每类任务定义机器可读的“完成条件”（如数据库写入成功、工单状态变更），而非依赖模型自评 [E3]。
    *   **效率约束**：建议引入“步骤效率”作为辅助指标。即使任务最终完成，若经历了不必要的循环或冗余调用，也应视为“低质量完成”，因为这直接关联成本与延迟 [E4]。

### 2.3 人工接管率 (Human Intervention / Escalation Rate)

该指标量化 Agent 的自治程度与隐性人力成本，是计算 ROI 的核心分母。

*   **定义**：需要人类介入纠正、解锁或兜底的任务占比。包括主动升级（Agent 识别无法处理并转交）和被动干预（用户报错或管理员强制接管）[E1][E2]。
*   **计算公式**：
    $$ \text{Intervention Rate} = \frac{\text{Tasks with Human Touch}}{\text{Total Tasks}} \times 100\% $$
*   **落地要点**：
    *   **区分接管类型**：AutoGen 等框架支持结构化的 Handoff 机制（如 Triage -> Specialist），这类“路由型交接”不应完全等同于“失败型接管” [E15]。选型时应考察平台是否能区分“设计内流转”与“异常兜底”。
    *   ** containment Rate（收敛率）**：作为反向指标，衡量 Agent 在不求助人类的情况下独立解决问题的比例。高收敛率是 Agent 规模化部署的前提 [E5]。

## 3. 选型落地建议

| 观测维度 | 核心指标 | 推荐数据采集粒度 | 选型检查点 |
| :--- | :--- | :--- | :--- |
| 工具稳定性 | 工具调用失败率 | Span/Step 级 | 是否支持自动解析工具入参/出参？是否区分模型错误与API错误？[E7][E16] |
| 业务交付 | 任务成功率 (TSR) | Session/Task 级 | 是否支持自定义“完成判定”逻辑？是否追踪循环次数与Token消耗？[E3][E4] |
| 自治成本 | 人工接管率 | Event/Handoff 级 | 是否有结构化 Handoff 事件标记？是否支持区分主动升级与被动报错？[E1][E15] |

## 4. 不确定性与边界

*   **指标非标准化**：当前业界对“任务成功”和“人工接管”尚无统一标准定义，不同框架（如 LangChain vs AutoGen）的实现差异较大，上述公式需根据实际业务场景适配 [E12][E13]。
*   **语义判断依赖**：工具调用的“参数正确性”和任务的“语义完成度”往往仍需 LLM-as-a-Judge 进行评估，存在评估器本身的误差与成本，无法做到 100% 确定性监控 [E4][E7]。
*   **数据获取门槛**：精确的步骤级指标（如控制流可视化、中间态上下文）依赖于深度 Trace 集成，若选用的 Agent 框架不支持 OpenTelemetry 或专有 SDK 埋点，部分指标可能无法在生产环境低成本获取 [E16][E10]。

- 业界缺乏Agent指标的统一标准定义，TSR与Intervention Rate的计算口径高度依赖具体业务与框架实现
- 工具参数正确性与任务语义完成度的自动化评估仍依赖LLM-as-a-Judge，存在评估噪声与额外成本
- 步骤级细粒度指标（如控制流、中间上下文）的获取强依赖于Agent框架的可观测性集成能力，部分开源框架可能需二次开发

## 引用证据

### Web 证据

- [E1][AI Agent Evaluation Metrics: A 2026 Guide](https://aiagentsquare.com/blog/ai-agent-evaluation-metrics)
- [E2][25 Best Agent Performance Metrics to Track (2026) | Coworker AI](https://coworker.ai/blog/agent-performance-metrics)
- [E3][Voice Agent Evaluation Metrics: Definitions, Formulas & Benchmarks | Hamming AI Resources](https://hamming.ai/resources/voice-agent-evaluation-metrics-guide)
- [E4][LLM Agent Evaluation Metrics in 2026: Tool Calling, Task Completion, ...](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide)
- [E5][AI Agent Evaluation Metrics for Production | Guide](https://www.buildmvpfast.com/blog/ai-agent-evaluation-metrics-production-guide-2026)
- [E6][Agent Observability Metrics](https://docs.datadoghq.com/llm_observability/monitoring/metrics)
- [E7][Agent observability: how to trace, debug, and improve ...](https://arize.com/guides/ai-agent-handbook/agent-observability)
- [E10][LLM Evaluation and AI Observability for Agent Monitoring - The ...](https://blog.jetbrains.com/pycharm/2026/05/llm-evaluation-and-ai-observability-for-agent-monitoring/)
- [E12][Best AI Agent Frameworks 2026: LangGraph, CrewAI, AutoGen | AI Automation Blog | Arsum](https://arsum.com/blog/posts/ai-agent-frameworks)
- [E13][AI Agent Frameworks Compared: LangChain, CrewAI, and More - Atlan](https://atlan.com/know/ai-agents-frameworks-compared)
- [E15] Context7 /microsoft/autogen
- [E16][AI Agent Observability, Tracing & Evaluation with Langfuse - Langfuse](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)