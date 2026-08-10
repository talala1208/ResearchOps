> 真实运行样例 · LangGraph / Temporal / Prefect 对比 · `run_id=20260808T064845Z`
>
> 公开资料可能随时间变化。

# LangGraph、Temporal 与 Prefect 在长时间运行工作流编排上的适用边界对比

## 1. 概述

在构建需要跨越数分钟、数小时甚至数天的长时间运行工作流时，LangGraph、Temporal 和 Prefect 提供了三种截然不同的架构范式。LangGraph 专注于 AI Agent 的状态机编排与记忆管理 [E1]；Temporal 提供通用的持久化执行引擎（Durable Execution），以应对分布式系统中的崩溃与重试 [E14]；Prefect 则以数据管道为核心，通过轻量级的装饰器模式提供调度与可观测性 [E25]。本报告基于公开资料，对比三者的核心能力、限制与选型边界。

## 2. 核心能力与状态管理机制

### 2.1 LangGraph：Agent 原生与图状态快照
LangGraph 是为构建、管理和部署长时间运行的**有状态 Agent** 而设计的底层编排框架 [E1]。其核心在于将工作流建模为图（Graph），并通过 **Checkpointer** 机制在每一步执行后保存状态快照 [E2]。
*   **状态管理**：通过 `thread_id` 映射持久化的对话或任务 ID，支持短期（线程级）和长期（跨线程 Store）记忆 [E10]。
*   **容错与恢复**：如果工作流中断（如系统故障或 Human-in-the-loop），可从最后记录的 Checkpoint 恢复执行 [E5]。
*   **适用场景**：多轮对话助手、需要复杂推理循环（Cycles）和工具调用的 AI Agent [E43]。

### 2.2 Temporal：持久化执行与事件溯源
Temporal 的核心能力是**持久化执行（Durable Execution）**，它将工作流逻辑与底层基础设施的故障隔离开来 [E14]。
*   **执行模型**：基于事件历史（Event History）重放（Replay）机制。Worker 进程崩溃后，新的 Worker 可以通过重放事件历史精确恢复到中断点 [E18]。
*   **长运行限制**：单个工作流的事件历史有 50,000 个事件或 50 MB 的大小限制，超长工作流需使用 `continue-as-new` 模式开启新执行并传递状态 [E15]。
*   **适用场景**：金融交易、基础设施配置、需要强一致性保证的微服务编排（Saga 模式） [E13]。

### 2.3 Prefect：动态调度与混合架构
Prefect 采用“代码优先”（Code-first）的方法，通过 `@flow` 和 `@task` 装饰器将 Python 脚本转化为可编排的工作流 [E25]。
*   **调度与执行**：采用混合架构，代码和数据保留在用户基础设施中，Prefect Cloud 仅负责调度元数据和状态监控 [E25]。
*   **状态追踪**：提供丰富的状态对象（如 `Retrying`, `Paused`, `Crashed`），支持基于状态的触发器和细粒度的重试策略 [E29]。
*   **适用场景**：数据管道（ETL/ELT）、机器学习训练流水线、定时批处理任务 [E23]。

## 3. 关键维度对比

| 维度 | LangGraph | Temporal | Prefect |
| :--- | :--- | :--- | :--- |
| **核心抽象** | 图（Nodes/Edges）+ 状态机 | 工作流（Workflow）+ 活动（Activity） | 流（Flow）+ 任务（Task） |
| **持久化机制** | Checkpointing（快照存储） | Event Sourcing（事件日志重放） | 状态元数据记录（数据库） |
| **容错/重试** | 基于快照恢复，支持 Human-in-the-loop [E5] | 自动重试，Activity 心跳，确定性重放 [E19] | 装饰器配置重试，任务级状态追踪 [E28] |
| **可观测性** | Agent 原生（Token、Trace、LangSmith） [E30] | 事件历史查看，强运维监控 | 数据流监控，UI 仪表盘，日志聚合 [E21] |
| **扩展性** | 依赖底层存储（如 Postgres/Redis） | 极高的并发扩展性（Worker 池） | 分布式 Worker 轮询 API |
| **运维复杂度** | 中等（需维护图状态存储） | 高（需维护 Temporal Server 或使用 Cloud） | 低（开源版易部署，Cloud 版免运维） |

## 4. 适用边界与选型要点

### 4.1 LangGraph 的边界
*   **推荐**：当工作流的核心是**LLM 推理**、需要动态图结构、循环（Loops）以及上下文记忆管理时 [E43]。它是“Agent 的 Rails” [E41]。
*   **不推荐**：作为通用的后端微服务编排工具，或处理非 AI 相关的传统数据 ETL 任务，因为其抽象层专为 Agent 设计 [E30]。

### 4.2 Temporal 的边界
*   **推荐**：当业务要求**零数据丢失**、工作流可能运行数天/数月、需要复杂的补偿逻辑（Saga）或跨多个微服务的强一致性协调时 [E39]。它是“工作流的 Kubernetes” [E41]。
*   **不推荐**：快速原型开发或简单的脚本调度，其学习曲线（确定性约束、事件溯源概念）和运维成本较高 [E36]。

### 4.3 Prefect 的边界
*   **推荐**：**数据工程**团队构建现代数据栈（Data Stack），需要灵活的 Python 代码支持、动态生成任务（Dynamic Mapping）以及混合云部署环境 [E25]。
*   **不推荐**：需要毫秒级响应的实时流处理，或需要像 Temporal 那样在进程崩溃后精确恢复执行栈（Prefect 更多是任务级重试或流级重跑） [E35]。

## 5. 典型场景推荐

1.  **AI Agent 编排（如 KYC 审核助手）**：
    *   **首选**：LangGraph。利用其 `interrupt` 机制实现 Human-in-the-loop，利用 Checkpoint 保持多轮审核状态 [E40]。
    *   **进阶**：LangGraph + Temporal。用 LangGraph 处理 Agent 逻辑，用 Temporal 包裹整个 Agent 运行过程以确保基础设施级的可靠性 [E34]。
2.  **数据管道（如 FHIR 数据摄取）**：
    *   **首选**：Prefect。利用其 `@task` 装饰器和重试机制处理不稳定的 API，利用 UI 监控每日运行状态 [E38]。
3.  **微服务编排（如订单履约 Saga）**：
    *   **首选**：Temporal。利用其 Activity 心跳和超时机制处理长周期的外部服务调用，确保补偿逻辑的确定性执行 [E13]。

## 6. 不确定性与边界
*   **LangGraph 的持久化性能**：在极高并发下，基于数据库（如 Postgres）的 Checkpointing 可能成为瓶颈，相比 Temporal 的分布式日志架构，其扩展性上限尚需更多大规模生产案例验证 [E9]。
*   **Prefect 的长运行支持**：Prefect 的 Flow 运行通常依赖于底层计算资源的稳定性，对于跨越数天的任务，其 Worker 进程的维护成本可能高于 Temporal 的“休眠-唤醒”模型。
*   **Temporal 的 AI 适配**：虽然 Temporal 提供了 AI SDK，但其缺乏原生的 LLM 上下文管理（如 Token 限制、Prompt 缓存），在纯 Agent 开发场景下开发效率低于 LangGraph [E30]。

- LangGraph 在超大规模并发下的 Checkpoint 存储性能瓶颈缺乏量化数据支持。
- Prefect 对于跨越数天且需要精确到代码行级恢复（而非任务级重试）的场景，其能力边界不如 Temporal 明确。
- 三者结合使用（如 LangGraph 运行在 Temporal 之上）的运维复杂度与最佳实践在公开资料中尚处于探索阶段。

## 引用证据

### Web 证据

- [E1][Graph-Based Agentic AI with LangGraph: Workflow ...](https://arxiv.org/html/2607.19297v1)
- [E2][Medium](https://aws.plainenglish.io/the-missing-piece-in-your-langgraph-workflow-a5c390ed2af4)
- [E5][Durable execution - Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [E9][How to manage state persistence in Langgraph workflows - langgraph - Latenode Official Community](https://community.latenode.com/t/how-to-manage-state-persistence-in-langgraph-workflows/31487)
- [E10][Persistence - Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/persistence)
- [E13][Temporal: Durable Execution Solutions](https://temporal.io)
- [E14][The definitive guide to Durable Execution | Temporal](https://temporal.io/blog/what-is-durable-execution)
- [E15] Context7 /websites/temporal_io
- [E18][Managing very long-running Workflows with Temporal | Temporal](https://temporal.io/blog/very-long-running-workflows)
- [E19][Good Practices for Writing Temporal Workflows and Activities - Raphaël Beamonte](https://raphaelbeamonte.com/posts/good-practices-for-writing-temporal-workflows-and-activities)
- [E21][Why You Need an Observability Platform](https://www.prefect.io/blog/why-you-need-an-observability-platform)
- [E23][Prefect: Python Workflow Orchestration for Data Pipelines | DEV.co](https://dev.co/observability/open-source/prefect)
- [E25][How Prefect Works | Python Workflow Orchestration](https://www.prefect.io/how-it-works)
- [E28][How to automatically rerun your workflow when it fails - Prefect](https://docs.prefect.io/v3/how-to-guides/workflows/retries)
- [E29][States - Prefect](https://docs.prefect.io/v3/concepts/states)
- [E30][LangGraph vs Temporal: AI Agent Orchestration Compared](https://www.langchain.com/resources/langgraph-vs-temporal)
- [E34][Are LangGraph + Temporal a good combo for automating KYC/AML ...](https://www.reddit.com/r/devops/comments/1mokg0f/are_langgraph_temporal_a_good_combo_for)
- [E35][I built the same pipeline twice - Temporal and Prefect in comparison](https://codilime.com/blog/built-same-pipeline-twice-temporal-prefect-comparison)
- [E36][Why Temporal Is Better Than LangGraph for Long-Running AI Workflows ...](https://www.alongside.team/blog/temporal-vs-langgraph-long-running-ai-workflows)
- [E38][LangGraph vs CrewAI vs Temporal for Healthcare](https://nirmitee.io/blog/langgraph-crewai-temporal-custom-orchestration-healthcare-agents-2026)
- [E39][Kinde Orchestrating Multi-Step Agents: Temporal/Dagster/LangGraph ...](https://www.kinde.com/learn/ai-for-software-engineering/ai-devops/orchestrating-multi-step-agents-temporal-dagster-langgraph-patterns-for-long-running-work/)
- [E40][AI Workflow Orchestration Guide 2026 | AI Workflow Lab](https://aiworkflowlab.dev/article/ai-workflow-orchestration-in-production-building-durable-agent-pipelines-with-langgraph-and-temporal)
- [E41][Comparing LangGraph and Temporal for Agentic Workflows - LinkedIn](https://www.linkedin.com/posts/alexskuznetsov_temporal-langgraph-agenticai-activity-7370176563192967168-aHQr)
- [E43][LLM Workflows: Patterns, Tools & Production Architecture (2026) ...](https://www.morphllm.com/llm-workflows)