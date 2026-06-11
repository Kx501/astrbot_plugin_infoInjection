# -*- coding: utf-8 -*-
"""AstrBot 入口：按 rules.json 注入上下文，每个会话每日仅注入一次。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart

from .engine import (
    EvalContext,
    InjectBlock,
    evaluate_rules,
    load_rules_from_path,
    today_key,
)

_ROOT = Path(__file__).resolve().parent
_SAMPLE_RULES = _ROOT / "sample_rules.json"
_EXTRA_INJECTED = "_ii_injected"
_KV_DAILY_DATES = "daily_inject_dates"


class InfoInjectionStar(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self._data_dir = Path(StarTools.get_data_dir(None))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._rules_path = self._data_dir / "rules.json"
        self._rules_mtime: float | None = None
        self._rules_cache: dict[str, Any] | None = None
        self._init_rules()

    def _init_rules(self) -> None:
        if self._rules_path.is_file():
            return
        if _SAMPLE_RULES.is_file():
            shutil.copy2(_SAMPLE_RULES, self._rules_path)
            logger.info("InfoInjection: 已从 sample_rules.json 初始化 %s", self._rules_path)
            return
        stub = {"schema_version": 1, "rules": []}
        with open(self._rules_path, "w", encoding="utf-8") as f:
            json.dump(stub, f, ensure_ascii=False, indent=2)

    def _rules_doc(self) -> dict[str, Any]:
        if not self._rules_path.is_file():
            return {"schema_version": 1, "rules": []}
        try:
            mtime = self._rules_path.stat().st_mtime
        except OSError:
            return {"schema_version": 1, "rules": []}
        if self._rules_cache is not None and self._rules_mtime == mtime:
            return self._rules_cache
        try:
            doc = load_rules_from_path(self._rules_path)
        except Exception:
            logger.exception("InfoInjection: 加载 rules.json 失败")
            return {"schema_version": 1, "rules": []}
        self._rules_mtime = mtime
        self._rules_cache = doc
        return doc

    def _timezone(self, event: AstrMessageEvent) -> str:
        try:
            global_tz = self.context.get_config(umo=event.unified_msg_origin).get("timezone")
            if global_tz:
                return str(global_tz)
        except Exception:
            pass
        return "Asia/Shanghai"

    def _session_key(self, event: AstrMessageEvent, req: ProviderRequest) -> str:
        session_id = (req.session_id or "").strip()
        if session_id:
            return session_id
        return str(event.unified_msg_origin)

    async def _get_daily_dates(self) -> dict[str, str]:
        raw = await self.get_kv_data(_KV_DAILY_DATES, {})
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if v is not None}

    async def _already_injected_today(self, session_key: str, today: str) -> bool:
        dates = await self._get_daily_dates()
        return dates.get(session_key) == today

    async def _mark_injected_today(self, session_key: str, today: str) -> None:
        dates = await self._get_daily_dates()
        dates[session_key] = today
        await self.put_kv_data(_KV_DAILY_DATES, dates)

    def _user_nickname(self, event: AstrMessageEvent) -> str:
        try:
            sender = event.message_obj.sender
            name = getattr(sender, "nickname", None) or getattr(sender, "user_id", None)
            if name:
                return str(name).strip()
        except Exception:
            pass
        return str(event.get_sender_id())

    def _build_eval_context(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> EvalContext:
        group_id = str(event.get_group_id() or "").strip()
        return EvalContext(
            user_message=(req.prompt or "").strip(),
            user_id=str(event.get_sender_id()),
            user_nickname=self._user_nickname(event),
            group_id=group_id,
            umo=str(event.unified_msg_origin),
            session_id=self._session_key(event, req),
            self_id=str(event.get_self_id()),
            is_private=event.is_private_chat(),
            timezone=self._timezone(event),
        )

    def _apply_blocks(self, req: ProviderRequest, blocks: list[InjectBlock]) -> None:
        replace_blocks = [b for b in blocks if b.is_replace]
        if replace_blocks:
            best = max(replace_blocks, key=lambda b: b.priority)
            self._apply_one(req, best)

        others = [b for b in blocks if not b.is_replace]
        prepends = sorted((b for b in others if b.is_prepend), key=lambda b: b.priority)
        appends = sorted((b for b in others if not b.is_prepend), key=lambda b: -b.priority)

        for block in prepends:
            self._apply_one(req, block)
        for block in appends:
            self._apply_one(req, block)

    def _apply_one(self, req: ProviderRequest, block: InjectBlock) -> None:
        if block.position == "message_replace":
            user_text = (req.prompt or "").strip()
            if not user_text:
                return
            if user_text.startswith("<msg ") and "</msg>" in user_text:
                return
            req.prompt = block.text
            return

        if block.position == "system_start":
            if req.system_prompt:
                req.system_prompt = f"{block.text}\n{req.system_prompt.lstrip()}"
            else:
                req.system_prompt = block.text
            return

        if block.position == "system_end":
            if req.system_prompt:
                req.system_prompt = f"{req.system_prompt.rstrip()}\n{block.text}"
            else:
                req.system_prompt = block.text
            return

        if block.position == "message_start":
            user_text = (req.prompt or "").strip()
            if user_text:
                req.prompt = f"{block.text}\n\n{user_text}"
            else:
                req.prompt = block.text
            if block.ephemeral:
                logger.debug(
                    "InfoInjection: message_start 无法单独 mark_as_temp，"
                    "注入内容会随 prompt 一并持久化；如需仅本轮生效请改用 message_end"
                )
            return

        part = TextPart(text=block.text)
        if block.ephemeral:
            part.mark_as_temp()
        req.extra_user_content_parts.append(part)

    def _record_injection(
        self,
        event: AstrMessageEvent,
        *,
        today: str,
        session_key: str,
        blocks: list[InjectBlock],
    ) -> None:
        event.set_extra(
            _EXTRA_INJECTED,
            {
                "date": today,
                "session_key": session_key,
                "rule_ids": [b.rule_id for b in blocks],
                "blocks": [
                    {
                        "rule_id": b.rule_id,
                        "position": b.position,
                        "ephemeral": b.ephemeral,
                        "priority": b.priority,
                        "text_len": len(b.text),
                    }
                    for b in blocks
                ],
            },
        )

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        try:
            tz = self._timezone(event)
            today = today_key(tz)
            session_key = self._session_key(event, req)
            doc = self._rules_doc()
            ctx = self._build_eval_context(event, req)

            applied: list[InjectBlock] = []

            always_blocks = evaluate_rules(doc, ctx, schedule="always")
            if always_blocks:
                self._apply_blocks(req, always_blocks)
                applied.extend(always_blocks)

            daily_blocks: list[InjectBlock] = []
            if not await self._already_injected_today(session_key, today):
                daily_blocks = evaluate_rules(doc, ctx, schedule="daily")
                if daily_blocks:
                    self._apply_blocks(req, daily_blocks)
                    applied.extend(daily_blocks)
                    await self._mark_injected_today(session_key, today)

            if not applied:
                return

            self._record_injection(
                event,
                today=today,
                session_key=session_key,
                blocks=applied,
            )
            logger.info(
                "InfoInjection: applied %s block(s) rules=%s session=%s date=%s",
                len(applied),
                [b.rule_id for b in applied],
                session_key,
                today,
            )
        except Exception:
            logger.exception("InfoInjection: on_llm_request 异常")
