---
name: yuubot-v2-prototype
description: The coding discipline for yuubot-v2 prototype phase. Keep Simple, Extensible and no Test, frequent Human-In-The-Loop Interaction.
---

# Yuubot-v2 重构目标

yuubot-v2重构目标在于将yuubot改造为可以接收外部Integrations并将其公开给agent使用的成熟系统。

# 原型阶段

在原型阶段，我们主要关注代码的抽象层设计。重点在于：

1. 持久层/副作用外推。将持久层/副作用通过依赖注入的形式转移至顶层统一编排，以避免隐式副作用。
2. 可扩展。在预期的扩展点上（如integrations，channels），核心代码应该统一对待，没有例外。内置组件应该在编排时注入，例如初始化时传递非空列表。
3. 解耦，避免抽象依赖。每个组件之间应当互相解耦，避免抽象泄露，造成god object.
4. 避免过度工程。在非扩展点上，应当尽量保持简单和代码可读。
5. 文件夹自组织，一目了然。

## Non-goals

不写测试。在这一阶段，将进行频繁的review互动，以确保核心代码足够精炼和可扩展。
对于扩展不写具体实现。暂时屏蔽掉复杂的实现细节（例如raise NotImpl留空即可）

