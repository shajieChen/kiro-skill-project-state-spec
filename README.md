---
agent_load: false
---

# project-state-spec

Kiro Agent Skill — 三阶段 Spec 编写工作流（Requirement -> Design -> Task），在磁盘上生成完整的 PST 工件集（R + D + Plan + LP + TP）并通过 `apply_changes.py` 注册到 `status.yaml`。

## 三件套协作关系

### 全局定位

```
+===========================================================================+
|                    PSS 在三件套中的角色: [规划者]                            |
+===========================================================================+
|                                                                           |
|  用户需求                                                                  |
|     |                                                                     |
|     v                                                                     |
|  +---------------------+                                                  |
|  | project-state-spec  |  <-- 你在这里                                    |
|  |       (PSS)         |                                                  |
|  |                     |  输入: 用户需求 + 现有代码                         |
|  |  Stage 1: R + D     |  输出: 完整工件集 (R+D+Plan+LP+TP)               |
|  |  Stage 2: Plan      |  写入: status.yaml (null->draft)                 |
|  |  Stage 3: LP + TP   |                                                  |
|  +----------+----------+                                                  |
|             |                                                             |
|             | scaffold_spec.py --> apply_changes.py                        |
|             | 每个工件注册为 draft 状态                                     |
|             v                                                             |
|  +-------------------------------------------------------+                |
|  |            project-state-tracker (PST)                 |                |
|  |                   [管家]                                |                |
|  |                                                       |                |
|  |  接收 PSS 注册的工件, 管理生命周期:                      |                |
|  |  - AUDIT 推进状态 (draft->reviewed->approved->ready)   |                |
|  |  - 为 LP 生成 preconditions                            |                |
|  |  - backfill pending_consumers                          |                |
|  |  - REVIEW 审计落地质量                                  |                |
|  +-------------------------------------------------------+                |
|             |                                                             |
|             | status.yaml 提供依赖信息                                     |
|             v                                                             |
|  +------------------------------+                                         |
|  |  Execute-LandingPrompt (ELP) |                                         |
|  |         [执行者]              |                                         |
|  |                              |                                         |
|  |  逐个执行 PSS 生成的 LP 文件   |                                         |
|  |  执行结果回流到 status.yaml    |                                         |
|  +------------------------------+                                         |
|                                                                           |
+===========================================================================+
```

### PSS 产出物与下游消费

```
PSS 产出                    PST 消费方式                 ELP 消费方式
+-----------+              +------------------+        +------------------+
| R-NNN.md  |---注册------>| artifacts[] 记录  |        | (不直接消费)      |
+-----------+              | depends_on 推导   |        |                  |
                           +------------------+        +------------------+

+-----------+              +------------------+        +------------------+
| D-NNN.yaml|---注册------>| artifacts[] 记录  |        | (不直接消费)      |
| (EARS AC) |              | AC 用于 REVIEW    |        |                  |
+-----------+              +------------------+        +------------------+

+-----------+              +------------------+        +------------------+
| Plan.topic|---注册------>| artifacts[] 记录  |        | (不直接消费)      |
|           |              | 架构用于 REVIEW   |        |                  |
+-----------+              +------------------+        +------------------+

+-----------+              +------------------+        +------------------+
| LP-NNN.md |---注册------>| artifacts[] 记录  |------->| Phase A 执行     |
|           |              | 生成 PCs + Gates  |        | 依赖门读取 PCs   |
|           |              | 生成 HC consumed  |        | Phase B 回流状态 |
+-----------+              +------------------+        +------------------+

+-----------+              +------------------+        +------------------+
| TP-NNN.md |---注册------>| artifacts[] 记录  |        | (不直接消费)      |
+-----------+              +------------------+        +------------------+
```

### PSS 与 PST 的接口协议

```
PSS 写入 PST 的数据:
  +-- approved_transitions.json:
  |     {
  |       "transitions": [{
  |         "artifact": "<id>",
  |         "type": "<type>",
  |         "from": null,
  |         "to": "draft",
  |         "reason": "PSS scaffold: <topic>",
  |         "source": "project-state-spec"
  |       }]
  |     }
  |
  +-- 磁盘文件:
  |     research/R-NNN-<topic>.md
  |     decisions/D-NNN-<topic>.yaml
  |     plan/Plan.<topic>.md
  |     prompts/landing/LP-NNN-<slug>.md
  |     prompts/test/TP-NNN-<slug>.md
  |
  +-- prompts/landing/README.md:
        lp_sequence_source: "auto"
        ## LP 序列: LP-001-x -> LP-002-y -> ...
        ## Coding Standards: (如有)

PSS 对 PST 的前置要求:
  +-- status/status.yaml 必须存在 (先运行 PST INIT)
  +-- tools/apply_changes.py 必须可用
```

### PSS 与 ELP 的间接关系

```
PSS 不直接调用 ELP, 但 PSS 的产出决定了 ELP 的行为:

  PSS 决定                          ELP 受到的影响
  +-------------------------------+--------------------------------------+
  | LP 文件内容 (任务描述)          | Phase A 执行什么                      |
  | LP 文件中的 scope/allowed files | ELP 允许修改哪些文件                  |
  | LP 序列顺序                     | ELP 依赖门检查的前置链                |
  | AC 定义 (在 D 中)              | PST REVIEW 用 AC 评估 ELP 执行质量   |
  | Plan 架构描述                   | PST REVIEW 用架构评估 ELP 一致性     |
  +-------------------------------+--------------------------------------+

  PSS 的 lp_sequence 直接决定 ELP 的:
    - 依赖门: 序列中前面的 LP 必须 ready 才能执行后面的
    - Handoff: 前一个 LP 的 HC 被后一个 LP 消费
    - 下一个 Prompt: ELP 从序列中找到当前 LP 的后继
```

### 完整生命周期时序

```
时间轴 -->

[PSS Stage 1]  用户需求 --> R-001 + D-001 (draft)
[PSS Stage 2]  架构选型 --> Plan.topic (draft)
[PSS Stage 3]  任务分解 --> LP-001..N + TP-001..N (draft)
                |
                v
[PST AUDIT]    scan --> propagate --> apply --> validate --> render
               draft -> reviewed -> approved -> ready (R/D/Plan)
               LP 获得 preconditions + gates
                |
                v
[ELP x N]      LP-001: 依赖门 -> 执行 -> 回流 (ready)
               LP-002: 依赖门(需LP-001 ready) -> 执行 -> 回流
               LP-003: ...
                |
                v
[PST REVIEW]   读 Result + 代码 --> AC 评估 --> 架构评估 --> 评级 A/B/C/D
```

## 功能

- **Stage 1 -- Requirement**: 引导式需求收集 -> 生成 Research (R-NNN) + Decision (D-NNN, EARS 格式)
- **Stage 2 -- Design**: 架构方案对比 -> 生成 Plan (含 AC 追溯)
- **Stage 3 -- Tasks**: 任务分解 -> 生成 LandingPrompt[] + TestPrompt[] 配对
- 支持 `new` / `continue` / `--force` 模式
- 内置 EARS 关键字自检、AC 覆盖率验证

## 调用方式

```text
Skill project-state-spec + new <topic>
Skill project-state-spec + continue <topic>
```

## 前置条件

- 工作区必须包含 `<pst_root>/status/status.yaml`（先运行 PST INIT）
- Python 3.9+、PyYAML

## 目录结构

```
project-state-spec/
+-- SKILL.md          # 核心 Prompt 协议
+-- tools/
|   +-- scaffold_spec.py   # CLI 脚手架脚本
+-- templates/        # R / D / Plan / LP / TP 模板
```

## 安装

将本文件夹放置于 `~/.kiro/skills/project-state-spec/`。

## 许可证

MIT
