# project-state-spec

Kiro Agent Skill — 三阶段 Spec 编写工作流（Requirement → Design → Task），在磁盘上生成完整的 PST 工件集（R + D + Plan + LP + TP）并通过 `apply_changes.py` 注册到 `status.yaml`。

## 功能

- **Stage 1 — Requirement**: 引导式需求收集 → 生成 Research (R-NNN) + Decision (D-NNN, EARS 格式)
- **Stage 2 — Design**: 架构方案对比 → 生成 Plan (含 AC 追溯)
- **Stage 3 — Tasks**: 任务分解 → 生成 LandingPrompt[] + TestPrompt[] 配对
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
├── SKILL.md          # 核心 Prompt 协议
├── tools/
│   └── scaffold_spec.py   # CLI 脚手架脚本
└── templates/        # R / D / Plan / LP / TP 模板
```

## 安装

将本文件夹放置于 `~/.kiro/skills/project-state-spec/`。

## 许可证

MIT
