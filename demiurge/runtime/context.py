from __future__ import annotations

from dataclasses import dataclass, field

from demiurge.core import LoadedCore
from demiurge.providers import LLMMessage, ToolCall
from demiurge.sdk import ContextContribution
from demiurge.storage import SessionMessage


@dataclass(slots=True)
class ContextLayer:
    name: str
    messages: list[LLMMessage] = field(default_factory=list)

    def summary(self) -> dict[str, int | str]:
        return {
            "name": self.name,
            "messages": len(self.messages),
            "chars": sum(len(message.content or "") for message in self.messages),
        }


@dataclass(slots=True)
class AssembledContext:
    messages: list[LLMMessage]
    layers: list[ContextLayer]

    def layer_summaries(self) -> list[dict[str, int | str]]:
        return [layer.summary() for layer in self.layers]


class ContextAssembler:
    def assemble(
        self,
        *,
        core: LoadedCore,
        context: list[ContextContribution],
        session_history: list[SessionMessage],
        current_turn_messages: list[LLMMessage],
        bootstrap_context: str | None = None,
        compaction_summary: SessionMessage | None = None,
    ) -> AssembledContext:
        by_placement = self._group_contributions(context)
        layers = [
            self._core_soul_layer(core),
            self._skill_index_layer(core),
            self._bootstrap_context_layer(bootstrap_context),
            self._context_contributions_layer(by_placement["system_context"], "system_context"),
            self._compaction_layer(compaction_summary),
            self._context_contributions_layer(by_placement["pre_history"], "pre_history"),
            self._session_history_layer(session_history),
            self._context_contributions_layer(by_placement["pre_current_user"], "pre_current_user"),
            self._current_turn_layer(current_turn_messages, by_placement["post_current_user"]),
        ]
        active_layers = [layer for layer in layers if layer.messages]
        messages = [message for layer in active_layers for message in layer.messages]
        return AssembledContext(messages=messages, layers=active_layers)

    def _core_soul_layer(self, core: LoadedCore) -> ContextLayer:
        messages = [LLMMessage(role="system", content=core.soul)] if core.soul else []
        return ContextLayer("core_soul", messages)

    def _skill_index_layer(self, core: LoadedCore) -> ContextLayer:
        content = self._build_skill_index(core)
        messages = [LLMMessage(role="system", content=content)] if content else []
        return ContextLayer("skill_index", messages)

    def _bootstrap_context_layer(self, content: str | None) -> ContextLayer:
        text = content or ""
        messages = [LLMMessage(role="system", content=text)] if text.strip() else []
        return ContextLayer("bootstrap_context", messages)

    def _group_contributions(self, context: list[ContextContribution]) -> dict[str, list[ContextContribution]]:
        grouped = {
            "system_context": [],
            "pre_history": [],
            "pre_current_user": [],
            "post_current_user": [],
        }
        for item in context:
            placement = item.placement if item.placement in grouped else "pre_current_user"
            grouped[placement].append(item)
        return grouped

    def _context_contributions_layer(self, context: list[ContextContribution], name: str) -> ContextLayer:
        messages: list[LLMMessage] = []
        for item in context:
            if item.type == "instruction" and item.content:
                messages.append(LLMMessage(role="system", content=item.content))
            elif item.type == "state_slice":
                messages.append(LLMMessage(role="system", content=f"State {item.key}: {item.value}"))
            elif item.type == "skill" and item.content:
                label = f"Skill {item.key}" if item.key else "Activated skill"
                messages.append(LLMMessage(role="system", content=f"## {label}\n\n{item.content}"))
            elif item.content:
                messages.append(LLMMessage(role="system", content=item.content))
        return ContextLayer(name, messages)

    def _compaction_layer(self, summary: SessionMessage | None) -> ContextLayer:
        if not summary or not summary.content:
            return ContextLayer("compaction_summary", [])
        return ContextLayer("compaction_summary", [LLMMessage(role="system", content=summary.content)])

    def _session_history_layer(self, history: list[SessionMessage]) -> ContextLayer:
        messages = [
            llm_message
            for message in history
            if (llm_message := self._session_message_to_llm(message)) is not None
        ]
        return ContextLayer("session_history", messages)

    def _session_message_to_llm(self, message: SessionMessage) -> LLMMessage | None:
        metadata = message.metadata or {}
        if message.role == "assistant":
            tool_calls = self._tool_calls_from_metadata(metadata)
            if not message.content and not tool_calls:
                return None
            return LLMMessage(role="assistant", content=message.content, tool_calls=tool_calls)
        if message.role == "tool":
            if not message.content:
                return None
            return LLMMessage(
                role="tool",
                name=metadata.get("tool_name"),
                tool_call_id=metadata.get("tool_call_id"),
                content=message.content,
            )
        if message.role in {"system", "user"} and message.content:
            return LLMMessage(role=message.role, content=message.content)
        return None

    def _tool_calls_from_metadata(self, metadata: dict[str, object]) -> list[ToolCall]:
        raw_calls = metadata.get("tool_calls")
        if not isinstance(raw_calls, list):
            return []
        calls: list[ToolCall] = []
        for index, raw_call in enumerate(raw_calls, start=1):
            if not isinstance(raw_call, dict):
                continue
            name = str(raw_call.get("name") or "").strip()
            if not name:
                continue
            arguments = raw_call.get("arguments")
            calls.append(
                ToolCall(
                    id=str(raw_call.get("id") or f"tool_call_{index}"),
                    name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return calls

    def _current_turn_layer(
        self,
        current_turn_messages: list[LLMMessage],
        post_current_user: list[ContextContribution],
    ) -> ContextLayer:
        if not current_turn_messages:
            return ContextLayer("current_turn", [])
        messages = [current_turn_messages[0]]
        post_user = self._context_contributions_layer(post_current_user, "post_current_user").messages
        messages.extend(post_user)
        messages.extend(current_turn_messages[1:])
        return ContextLayer("current_turn", messages)

    def _build_skill_index(self, core: LoadedCore) -> str:
        if not core.skills:
            return ""
        lines = [
            "## Skills (progressive loading)",
            "Before replying, scan the skills below. If a skill is relevant, you MUST call skill_view(name) and follow its instructions before acting.",
            "Use skills_list(category) to inspect metadata. Use skill_view(name, file_path) for linked files. The index below is metadata only; it is not the skill body.",
            "",
            "<available_skills>",
        ]
        for skill in sorted(core.skills, key=lambda item: (item.category, item.name)):
            description = f": {skill.description}" if skill.description else ""
            lines.append(f"- {skill.name} [{skill.category}]{description}")
        lines.append("</available_skills>")
        return "\n".join(lines)
