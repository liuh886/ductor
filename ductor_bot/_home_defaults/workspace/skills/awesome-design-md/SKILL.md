---
name: awesome-design-md
description: Use when selecting or applying AI-readable DESIGN.md style systems for DFTD+E design-first workflows.
---

# Skill: Awesome Design MD

## Description
提供一系列“AI 可读”的设计系统规范（DESIGN.md 模板），作为 DFTD+E (Design-First, Todo-Driven, Evaluation-Closed-Loop) 模式中 **Design-First** 环节的标准化事实来源。

## Capabilities
1. **Style Selection**: 提供多种成熟产品的视觉风格模板（如 Linear, Stripe, Claude, Vercel 等）。
2. **DFTD+E Initializer**: 快速为项目生成配套的 `DESIGN.md`, `TASKS.md` 和 `EVALUATE.md`。
3. **Visual Guardrails**: 提取设计规范中的色彩、排版、组件定义等，为 Agent 开发 UI 提供高保真约束。

## Usage Patterns
- "帮我初始化一个新项目，视觉风格参考 Linear。"
- "从 awesome-design-md 中挑选一个适合金融产品的风格应用到当前项目。"
- "检查现在的 UI 代码是否符合 DESIGN.md 中的视觉规则。"

## Integration with DFTD+E
- **Design (D)**: 从 `library/design-md/` 中选择主题并拷贝其 `DESIGN.md`。
- **Todo (T)**: 解析 `DESIGN.md` 中的组件和布局原则，自动生成 `TASKS.md`。
- **Evaluate (E)**: 将规范中的 "Do's and Don'ts" 转化为 `EVALUATE.md` 的验收检查清单。

## Theme Library
可以通过 `scripts/list_themes.py` 获取完整主题列表。常用主题示例：
- `linear.app`: 高对比度、极简工程风格。
- `stripe`: 优雅、高细节、富有流动感的风格。
- `claude`: 温暖、拟物、注重阅读体验的 AI 风格。
- `vercel`: 现代、简洁、开发者优先的风格。

## Directory Structure
- `library/design-md/<theme_name>/DESIGN.md`: 该主题的详细设计规范。
- `scripts/list_themes.py`: 列出所有可用主题。
