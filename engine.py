# -*- coding: utf-8 -*-
"""规则引擎：条件匹配 + 模板渲染，不依赖 AstrBot。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

InjectPosition = Literal[
    "system_start",
    "system_end",
    "message_start",
    "message_end",
]

_PREPEND_POSITIONS = frozenset({"system_start", "message_start"})

_WEEKDAY_ZH = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_DEFAULT_TZ = "Asia/Shanghai"


@dataclass
class EvalContext:
    user_message: str = ""
    user_id: str = ""
    group_id: str = ""
    umo: str = ""
    session_id: str = ""
    self_id: str = ""
    is_private: bool = True
    timezone: str = _DEFAULT_TZ
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class InjectBlock:
    rule_id: str
    position: InjectPosition
    text: str
    ephemeral: bool = False
    priority: int = 0

    @property
    def is_prepend(self) -> bool:
        return self.position in _PREPEND_POSITIONS


def resolve_timezone(raw: str | None) -> ZoneInfo:
    text = (raw or "").strip() or _DEFAULT_TZ
    try:
        return ZoneInfo(text)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def today_key(timezone: str | None) -> str:
    """Return YYYY-MM-DD for the given timezone."""
    return datetime.now(resolve_timezone(timezone)).strftime("%Y-%m-%d")


def load_rules_from_path(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError("rules 文件根节点必须是对象")
    return doc


def _sort_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(rule: dict[str, Any]) -> tuple[int, str]:
        try:
            priority = int(rule.get("priority", 0))
        except (TypeError, ValueError):
            priority = 0
        return (-priority, str(rule.get("id", "")))

    return sorted(rules, key=key)


def _as_str_set(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(item).strip() for item in values if str(item).strip()}


def _rule_matches(when: dict[str, Any], ctx: EvalContext) -> bool:
    chat = str(when.get("chat") or "any").strip().lower()
    if chat == "private" and not ctx.is_private:
        return False
    if chat == "group" and ctx.is_private:
        return False

    user_ids = _as_str_set(when.get("user_ids"))
    if user_ids and ctx.user_id not in user_ids:
        return False

    group_ids = _as_str_set(when.get("group_ids"))
    if group_ids and (ctx.is_private or ctx.group_id not in group_ids):
        return False

    message = ctx.user_message or ""
    pattern = when.get("message_regex")
    if pattern is not None and str(pattern).strip():
        try:
            if not re.search(str(pattern), message):
                return False
        except re.error:
            return False

    contains = when.get("message_contains")
    if contains is not None and str(contains) and str(contains) not in message:
        return False

    return True


def _render_template(template: str, ctx: EvalContext) -> str:
    tz = resolve_timezone(ctx.timezone)
    now = datetime.now(tz)
    weekday_idx = now.weekday()

    values: dict[str, str] = {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": _WEEKDAY_ZH[weekday_idx],
        "user_id": ctx.user_id,
        "group_id": ctx.group_id,
        "umo": ctx.umo,
        "session_id": ctx.session_id,
        "self_id": ctx.self_id,
        "user_message": ctx.user_message,
        "prompt": ctx.user_message,
    }
    for key, val in ctx.extra.items():
        if key not in values:
            values[key] = "" if val is None else str(val)

    def repl(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        return values.get(name, "")

    return re.sub(r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}", repl, template)


_VALID_POSITIONS = frozenset(
    {"system_start", "system_end", "message_start", "message_end"}
)


def _normalize_position(inject: dict[str, Any]) -> InjectPosition | None:
    key = str(inject.get("position") or "").strip().lower().replace("-", "_")
    if key in _VALID_POSITIONS:
        return key  # type: ignore[return-value]
    return None


def evaluate_rules(rules_doc: dict[str, Any], ctx: EvalContext) -> list[InjectBlock]:
    rules = rules_doc.get("rules")
    if not isinstance(rules, list):
        return []

    blocks: list[InjectBlock] = []
    for rule in _sort_rules([r for r in rules if isinstance(r, dict)]):
        if not rule.get("enabled", True):
            continue

        rid = str(rule.get("id") or "").strip() or "unnamed"
        when = rule.get("when")
        if not isinstance(when, dict):
            when = {}
        if not _rule_matches(when, ctx):
            continue

        inject = rule.get("inject")
        if not isinstance(inject, dict):
            continue

        template = inject.get("template")
        if not isinstance(template, str) or not template.strip():
            continue

        text = _render_template(template, ctx).strip()
        if not text:
            continue

        try:
            priority = int(rule.get("priority", 0))
        except (TypeError, ValueError):
            priority = 0

        position = _normalize_position(inject)
        if position is None:
            continue

        blocks.append(
            InjectBlock(
                rule_id=rid,
                position=position,
                text=text,
                ephemeral=bool(inject.get("ephemeral", False)),
                priority=priority,
            )
        )

    return blocks
