---
sidebar_position: 6
title: 网页搜索包
description: 把 Brave 或 Tavily web search 安装为 package-owned web_search tool。
---

# 网页搜索包

内置 web search packages 会安装一个 package-owned `web_search` authored tool。当前 providers 是 Brave Search 和 Tavily Search。

同一个 core 中一次只安装一个 web search provider package。两个 packages 都 target：

```text
agent/tools/web_search/
```

## 包

| 包 | Provider | 能力 |
| --- | --- | --- |
| `web_search_brave` | Brave Search | `network.fetch` |
| `web_search_tavily` | Tavily Search | `network.fetch` |

两个 packages 还会安装 provider-owned lib：

```text
agent/lib/web_search_brave/
agent/lib/web_search_tavily/
```

## 安装

使用交互式 manager：

```bash
uv run demiurge package
```

或者用 subcommands 预览并安装：

```bash
uv run demiurge package install web_search_brave --core assistant --preview
uv run demiurge package install web_search_brave --core assistant
```

切换 providers：

```bash
uv run demiurge package uninstall web_search_brave --core assistant
uv run demiurge package install web_search_tavily --core assistant
```

## 选项和凭证

| 包 | 选项 | 环境变量 |
| --- | --- | --- |
| `web_search_brave` | `api_key` | `DEMIURGE_BRAVE_SEARCH_API_KEY`, `BRAVE_SEARCH_API_KEY`, or `BRAVE_API_KEY` |
| `web_search_tavily` | `api_key` | `DEMIURGE_TAVILY_API_KEY` or `TAVILY_API_KEY` |

`api_key` option 是可选 secret。留空则在运行时使用环境变量。

## 工具审批

两个 packages 安装的 `web_search` 带有：

| 字段 | 值 |
| --- | --- |
| `risk` | `medium` |
| `approval_policy` | `prompt` |
| `capability` | `network.fetch` |
| `display_policy` | `summary` |
| `model_output_policy` | `content` |

Host approval 和 capability systems 仍然控制实际 network use。

## Brave 工具参数

`web_search_brave` 接受：

| 参数 | 说明 |
| --- | --- |
| `query` | 必需 search query。 |
| `count` | 结果数量，1 到 20。默认是 5。 |
| `country` | 可选 2-letter country code。 |
| `search_lang` | 可选 Brave search language code。 |
| `ui_lang` | 可选 UI locale。 |
| `safesearch` | `off`、`moderate` 或 `strict`。 |
| `freshness` | Brave time filter：`pd`、`pw`、`pm` 或 `py`。 |
| `date_after` and `date_before` | 可选 YYYY-MM-DD range。它们必须一起提供。 |

## Tavily 工具参数

`web_search_tavily` 接受：

| 参数 | 说明 |
| --- | --- |
| `query` | 必需 search query。 |
| `search_depth` | `basic` 或 `advanced`。 |
| `topic` | `general`、`news` 或 `finance`。 |
| `time_range` | `day`、`week`、`month`、`year`、`d`、`w`、`m` 或 `y`。 |
| `start_date` and `end_date` | 可选 YYYY-MM-DD bounds。 |
| `max_results` | 结果数量，1 到 20。默认是 5。 |
| `include_answer` | `false`、`true`、`basic` 或 `advanced`。 |
| `include_domains` | 可选 included domains 列表。 |
| `exclude_domains` | 可选 excluded domains 列表。 |
| `country` | 可选 country hint。 |

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

在 TUI 中检查 tools：

```text
/tools
```

运行一个请求当前信息的 turn。Model 应该在 `web_search` tool 执行 network call 前请求 approval。

## 卸载

```bash
uv run demiurge package uninstall web_search_brave --core assistant --preview
uv run demiurge package uninstall web_search_brave --core assistant
```

Uninstall 会移除 package-owned `web_search` tool 和 provider lib。
