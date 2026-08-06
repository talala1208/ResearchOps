# ResearchOps Agent 协作规则

> 管理模式：轻量  
> 当前事实来源：`spec/spec.md`  
> 本文件只记录 ResearchOps 项目特有约束；通用开发、安全、测试和文档规则遵循全局 AGENTS.md。

## 工作区约束

- 使用 `uv` 管理现有环境，不新建其他虚拟环境。
- 源码放在 `src/`，手动脚本放在 `scripts/`，生成文件放在 `outputs/`。
- `reference/` 只读且不得作为运行依赖。
- `template/` 只读，除非用户明确要求维护。

## SPEC 优先级

- `spec/spec.md` 是当前需求和行为的唯一事实来源。
- 改动 Graph 编排、State 字段、节点职责、预算控制、安全边界、输出产物、Prompt 契约或验收标准时，必须先更新或同步更新 `spec/spec.md`。
- 如果实现和 SPEC 冲突，先停下来修正 SPEC 或请求用户决策，不得用实现反向覆盖需求。
- 本文件不复述 SPEC 中的业务流程、字段、配置、权限或验收细节。
- 当前未启用 `BACKLOG.md`、`TEST_RESULTS.md`、`ARCHITECTURE.html`、`feature_list.json`；不要自行新增，除非用户要求或项目复杂度确实达到启用条件。

## 代码组织

- Graph 主入口为 `src.workflow.graph.graph`；不得在脚本或测试中复制另一套 Graph 编排。
- `src/workflow/edges.py` 只放边、节点名常量和路由函数。
- `src/workflow/nodes.py` 只做节点统一导出；具体节点实现按职责放在 `planning_nodes.py`、`search_nodes.py`、`evidence_nodes.py`、`report_nodes.py`、`guard_nodes.py`、`artifact_nodes.py`。真实工具、LLM 调用、产物保存逻辑应下沉到对应模块。

## 测试与验证

- 默认只运行与改动相关的测试，不为小改动运行完整套件。
- 可重复验证写入 `tests/`；一次性手动验证放在 `scripts/`。
- 真实联网或付费服务验证必须与默认离线测试隔离。
