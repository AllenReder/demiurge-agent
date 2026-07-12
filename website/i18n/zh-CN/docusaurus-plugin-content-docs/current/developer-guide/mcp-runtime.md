---
title: MCP 运行时
description: 面向贡献者的 MCP server discovery、naming、transports 与 result conversion 说明。
---

# MCP 运行时

MCP 运行时从 Agent Core 发现 server declaration，并通过 Host tool registry 暴露过滤后的
tools。

在当前 alpha 运行时中，catalog cache miss 会启动或连接每个 enabled server，并在之后
model-call 的 `mcp.call:*` capability 与 approval check 之前调用 `list_tools()`。目前应把
MCP declaration 视为可信代码/配置。冻结目标把 `mcp.connect:<server>` 与
`mcp.call:<server>` 分开，并在 spawn、network I/O 或 discovery 前应用 connect policy；
参见 [Host 运行时契约](runtime-contracts.md#effectruntime)。

## 发现

Declarations 位于：

```text
agent/mcp/*.yaml
```

Disabled declaration 会被忽略。Stdio declaration 必须包含 `command`。Streamable HTTP
declaration 必须使用 `http://` 或 `https://` URL。

## 命名

Tool name 会在可见前被 normalized、加上 server prefix 并过滤。当前 alpha catalog 与
dispatcher 已通过 per-turn resolved catalog，把每个可见 MCP tool 绑定到对应的
session/revision connection 与 dispatcher adapter。Call 会使用该 connection-bound entry，
不再依赖 legacy global name index。跨 source name collision 会同时报告两侧 provenance 并
失败。Namespacing 仍不能替代 DG-P3-T02 负责的 connect/discovery authority 与 lifecycle。

## 环境与 Headers

Declaration 可以提供 environment variables、headers、cwd、timeouts、risk、approval
policy 与 parallel-call support。Secret 应来自 Host environment。当前 environment 与
header interpolation 会在构建 catalog 时发生，此时尚不存在 connect approval。

## 结果转换

MCP result 会在 model replay 与 display 前转换成 Demiurge tool result。

## 边界

Core 声明 MCP servers。Host 拥有 transport lifecycle、discovery、timeouts、policy 与
tool execution。当前 alpha 实现尚未按要求顺序强制落实其中部分 ownership；不要把当前
discovery path 理解为最终 EffectRuntime 接口。
