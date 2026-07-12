---
title: MCP 运行时
description: 面向贡献者的 MCP server discovery、naming、transports 与 result conversion 说明。
---

# MCP 运行时

MCP 运行时从 Agent Core 发现 server declaration，并通过 Host tool registry 暴露过滤后的
tools。

在当前 alpha 运行时中，普通 `TurnExecution` 准备 catalog 时会先要求
`mcp.connect:<server>`，并解析 server risk/approval policy。缺少 authority 或被拒绝时，
会在 client construction 与 `list_tools()` 之前跳过该 server。后续 model call 还会独立要求
`mcp.call:<server>`（或 manifest 显式 call capability）及 call approval。参见
[Host 运行时契约](runtime-contracts.md#effectruntime)。

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
失败。Namespacing 仍不能替代独立的 connect 与 call authority check。

## 环境与 Headers

Declaration 可以提供 environment variables、headers、cwd、timeouts、risk、approval
policy 与 parallel-call support。Secret 应来自 Host environment。Interpolation 现在只在
connect capability/approval 通过后发生；configured cwd 还必须在 approval 与 client
construction 前解析到 Host workspace 内。Approval preview 会显示 command、cwd、option
形状、environment/header 名称以及移除 credential 的 URL；positional value 只显示
hash/length 摘要，secret-bearing option value 会被脱敏。Sanitized environment/secret binding
与共享 URL policy 仍属于后续 security 工作。

## 结果转换

MCP result 会在 model replay 与 display 前转换成 Demiurge tool result。

## 边界

Core 声明 MCP servers。Host 拥有 transport lifecycle、discovery、timeouts、policy 与
tool execution。`list_tools()` 目前按 server 使用 `connect_timeout_seconds` 限时；超时会
关闭该 connection、记录 diagnostic，并继续处理后续 server。Discovery 在整个 runtime
内跨 session 最多并发处理四个 server，并在之后确定性组装 catalog name。Discovery
failure diagnostic 按 server 使用 30 秒 negative-cache TTL；在同一 catalog authority 内，
过期时只重试该 server，健康 peer connection 保持可用。Authority denial 也会在下一个
turn 按 server 重新检查，而不是成为 negative cache。每个 connection identity 都包含该
server 自己的 manifest fingerprint，因此同 authority refresh 可以只重连变化的 server。
principal、capability、core revision、workspace 或 effective policy 变化时会驱逐并重新授权
整个旧 catalog，不会跨 snapshot 复用 peer。删除全部 declaration 会关闭该 session 剩余 connection。Catalog identity 还绑定 principal、capability snapshot、core revision 与
effective connect policy，因此收紧 authority 时不能复用旧 connection。显式 session
切换到新 session 或 resume 其他 session 时，会通过 tracked background cleanup 驱逐旧
session；显式 eviction 仍只关闭选定 session 的 catalogs。Delegated child 使用 Host-issued
scope 准备 catalog，并在 child run 结束时释放 MCP connections。Evolution review 会为
MCP declaration 变化生成 secret-safe before/after security diff 和内容绑定的
`mcp-review:<sha256>` token；除普通 promote approval 外，promotion 还要求原样返回该
token。token 缺失或已过期时 Git refs 保持不变。Sanitized env/secret binding 与 URL
validation 仍属于后续 security layer。
