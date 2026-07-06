from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from demiurge.runtime.interactions import InteractionInbound, UserPromptRequest


@dataclass(frozen=True, slots=True)
class PromptChoiceResolution:
    text: str
    consumed: bool = False
    matched_choice: bool = False
    index: int | None = None


@dataclass(frozen=True, slots=True)
class PromptDelivery:
    source: str
    text: str
    reply_to: str | None
    metadata: dict[str, Any]
    choices: tuple[str, ...] = field(default_factory=tuple)


def normalize_prompt_answer(
    answer: Any,
    choices: list[str] | tuple[str, ...],
    *,
    empty: str = "raw",
) -> PromptChoiceResolution:
    text = str(answer or "").strip()
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(choices):
            return PromptChoiceResolution(str(choices[index]), matched_choice=True, index=index)
    if not text and empty == "first" and choices:
        return PromptChoiceResolution(str(choices[0]), matched_choice=True, index=0)
    return PromptChoiceResolution(text)


def format_prompt_text(question: str, choices: list[str] | tuple[str, ...]) -> str:
    lines = [str(question)]
    for index, choice in enumerate(choices, start=1):
        lines.append(f"{index}. {choice}")
    return "\n".join(lines)


def choice_callback_data(index: int, *, prefix: str = "choice") -> str:
    return f"{prefix}:{index}"


def parse_choice_callback_data(data: Any, *, prefix: str = "choice") -> int | None:
    value = str(data or "")
    marker = f"{prefix}:"
    if not value.startswith(marker):
        return None
    try:
        return int(value.removeprefix(marker))
    except ValueError:
        return None


def choice_button_rows(
    choices: list[str] | tuple[str, ...],
    *,
    prefix: str = "choice",
    label_limit: int = 32,
) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for index, choice in enumerate(choices):
        rows.append(
            [
                {
                    "text": f"{index + 1}. {_shorten(choice, limit=label_limit)}",
                    "callback_data": choice_callback_data(index, prefix=prefix),
                }
            ]
        )
    return rows


@dataclass(slots=True)
class PromptChoiceRuntime:
    _choices: dict[str, list[str]] = field(default_factory=dict)

    def remember(self, conversation_key: str | None, choices: list[str] | tuple[str, ...]) -> None:
        if not conversation_key:
            return
        key = str(conversation_key)
        if choices:
            self._choices[key] = [str(choice) for choice in choices]
            return
        self._choices.pop(key, None)

    def get(self, conversation_key: str, default: Any = None) -> list[str] | Any:
        choices = self._choices.get(conversation_key)
        if choices is None:
            return default
        return list(choices)

    def consume_text(self, conversation_key: str | None, text: Any) -> PromptChoiceResolution:
        if not conversation_key:
            return PromptChoiceResolution(str(text or "").strip())
        choices = self._choices.pop(str(conversation_key), None)
        if not choices:
            return PromptChoiceResolution(str(text or "").strip())
        resolution = normalize_prompt_answer(text, choices)
        return PromptChoiceResolution(
            resolution.text,
            consumed=True,
            matched_choice=resolution.matched_choice,
            index=resolution.index,
        )

    def consume_index(self, conversation_key: str | None, index: int) -> PromptChoiceResolution | None:
        if not conversation_key:
            return None
        choices = self._choices.get(str(conversation_key))
        if not choices or index < 0 or index >= len(choices):
            return None
        self._choices.pop(str(conversation_key), None)
        return PromptChoiceResolution(
            choices[index],
            consumed=True,
            matched_choice=True,
            index=index,
        )

    def consume_callback_data(
        self,
        conversation_key: str | None,
        data: Any,
        *,
        prefix: str = "choice",
    ) -> PromptChoiceResolution | None:
        index = parse_choice_callback_data(data, prefix=prefix)
        if index is None:
            return None
        return self.consume_index(conversation_key, index)


@dataclass(slots=True)
class PromptDeliveryRuntime:
    choices: PromptChoiceRuntime = field(default_factory=PromptChoiceRuntime)

    def prepare(self, prompt: UserPromptRequest) -> PromptDelivery | None:
        prompt_choices = tuple(str(choice) for choice in prompt.choices)
        self.choices.remember(prompt.conversation_key, prompt_choices)
        source = prompt.metadata.get("source")
        if source is None:
            return None
        reply_to = prompt.metadata.get("reply_to")
        return PromptDelivery(
            source=str(source),
            text=format_prompt_text(prompt.question, prompt_choices),
            reply_to=str(reply_to) if reply_to is not None else None,
            metadata=dict(prompt.metadata),
            choices=prompt_choices,
        )

    def resolve_inbound(self, inbound: InteractionInbound) -> InteractionInbound:
        if not inbound.conversation_key:
            return inbound
        text = self.choices.consume_text(inbound.conversation_key, inbound.text.strip()).text
        if text == inbound.text:
            return inbound
        return InteractionInbound(
            channel=inbound.channel,
            text=text,
            source=inbound.source,
            reply_to=inbound.reply_to,
            conversation_key=inbound.conversation_key,
            metadata=dict(inbound.metadata),
            attachments=list(inbound.attachments),
        )

    def consume_callback_data(
        self,
        conversation_key: str | None,
        data: Any,
        *,
        prefix: str = "choice",
    ) -> PromptChoiceResolution | None:
        return self.choices.consume_callback_data(conversation_key, data, prefix=prefix)

    def pending_choices(self, conversation_key: str, default: Any = None) -> list[str] | Any:
        return self.choices.get(conversation_key, default)


def _shorten(value: Any, *, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."
