---
sidebar_position: 6
title: Web Search Packages
description: Install Brave or Tavily web search as a package-owned web_search tool.
---

# Web Search Packages

Built-in web search packages install a package-owned `web_search` authored tool.
The current providers are Brave Search and Tavily Search.

Install only one web search provider package in a core at a time. Both packages
target:

```text
agent/tools/web_search/
```

## Packages

| Package | Provider | Capability |
| --- | --- | --- |
| `web_search_brave` | Brave Search | `network.fetch` |
| `web_search_tavily` | Tavily Search | `network.fetch` |

Both packages also install a provider-owned lib:

```text
agent/lib/web_search_brave/
agent/lib/web_search_tavily/
```

## Install

Use the interactive manager:

```bash
uv run demiurge package
```

Or preview and install with subcommands:

```bash
uv run demiurge package install web_search_brave --core assistant --preview
uv run demiurge package install web_search_brave --core assistant
```

To switch providers:

```bash
uv run demiurge package uninstall web_search_brave --core assistant
uv run demiurge package install web_search_tavily --core assistant
```

## Options and Credentials

| Package | Option | Environment variables |
| --- | --- | --- |
| `web_search_brave` | `api_key` | `DEMIURGE_BRAVE_SEARCH_API_KEY`, `BRAVE_SEARCH_API_KEY`, or `BRAVE_API_KEY` |
| `web_search_tavily` | `api_key` | `DEMIURGE_TAVILY_API_KEY` or `TAVILY_API_KEY` |

The `api_key` option is optional and secret. Leave it empty to use environment
variables at runtime.

## Tool Approval

Both packages install `web_search` with:

| Field | Value |
| --- | --- |
| `risk` | `medium` |
| `approval_policy` | `prompt` |
| `capability` | `network.fetch` |
| `display_policy` | `summary` |
| `model_output_policy` | `content` |

The host approval and capability systems still control actual network use.

## Brave Tool Arguments

`web_search_brave` accepts:

| Argument | Description |
| --- | --- |
| `query` | Required search query. |
| `count` | Number of results, 1 to 20. Default is 5. |
| `country` | Optional 2-letter country code. |
| `search_lang` | Optional Brave search language code. |
| `ui_lang` | Optional UI locale. |
| `safesearch` | `off`, `moderate`, or `strict`. |
| `freshness` | Brave time filter: `pd`, `pw`, `pm`, or `py`. |
| `date_after` and `date_before` | Optional YYYY-MM-DD range. They must be provided together. |

## Tavily Tool Arguments

`web_search_tavily` accepts:

| Argument | Description |
| --- | --- |
| `query` | Required search query. |
| `search_depth` | `basic` or `advanced`. |
| `topic` | `general`, `news`, or `finance`. |
| `time_range` | `day`, `week`, `month`, `year`, `d`, `w`, `m`, or `y`. |
| `start_date` and `end_date` | Optional YYYY-MM-DD bounds. |
| `max_results` | Number of results, 1 to 20. Default is 5. |
| `include_answer` | `false`, `true`, `basic`, or `advanced`. |
| `include_domains` | Optional list of included domains. |
| `exclude_domains` | Optional list of excluded domains. |
| `country` | Optional country hint. |

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Inspect tools in the TUI:

```text
/tools
```

Run a turn that asks for current information. The model should request approval
before the `web_search` tool performs a network call.

## Uninstall

```bash
uv run demiurge package uninstall web_search_brave --core assistant --preview
uv run demiurge package uninstall web_search_brave --core assistant
```

Uninstall removes the package-owned `web_search` tool and provider lib.
