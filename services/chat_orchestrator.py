"""聊天主编排服务。

第一阶段先承接 chat_handler 中最复杂的流式发送主链路，
通过依赖注入复用旧模块函数，避免一次性重写业务细节。
"""

from __future__ import annotations

import asyncio
import re
from telegram.error import TelegramError, RetryAfter, BadRequest
from plugins.base import ReplyHookContext
from plugins.manager import get_plugin_manager


class ChatOrchestrator:
    def __init__(self, **deps):
        self.d = deps

    async def run(self, *, msg, context, chat_id: int, text: str, sender: str, bot_username: str,
                  bot_id: int, is_reply: bool, is_mention: bool, trigger_type: str, has_photo: bool, has_sticker: bool,
                  image_b64, image_file_id, image_caption_text, persona_users, context_snapshot,
                  focus_reserved: bool, is_group: bool, is_private: bool, sender_not_in_group: bool = False,
                  sender_is_channel: bool = False):
        llm = self.d['get_llm_client']()
        free_reply_mode = self.d['get_group_free_reply_mode'](chat_id)
        context_message_ids = [
            int(getattr(cm, "message_id", 0))
            for cm in (context_snapshot or [])
            if getattr(cm, "message_id", None)
        ]
        if getattr(msg, "message_id", None):
            context_message_ids.append(int(msg.message_id))
        valid_reply_ids = self.d['get_replied_map'](chat_id, context_message_ids)
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(self.d['keep_typing'](context.bot, chat_id, typing_stop))

        raw_text = ""
        refused = False
        finalized_parts = 0
        current_msg = None
        sent_msgs = []
        last_edit_at = 0.0
        executed_reactions: set[tuple[str, int]] = set()
        current_reply_targets: list[int] = []
        completed_segments: list[str] = []
        tool_status_msg = None
        memory_tool_used = False

        def _extract_reply_targets(part: str) -> tuple[list[int], str]:
            text = part or ""
            # 兼容两种控制标记：旧格式 [REPLY_TO:123]，以及提示词中要求的新格式 [[REPLY:123]] / [[REPLY:123,456]]。
            targets: list[int] = []
            for match in re.findall(r"\[\[REPLY:([0-9,\s]+)\]\]", text):
                for item in match.split(','):
                    item = item.strip()
                    if item.isdigit():
                        targets.append(int(item))
            for item in re.findall(r"\[REPLY_TO:(\d+)\]", text):
                if item.isdigit():
                    targets.append(int(item))
            cleaned = re.sub(r"\[\[REPLY:[0-9,\s]+\]\]", "", text)
            cleaned = re.sub(r"\[REPLY_TO:\d+\]", "", cleaned)
            deduped: list[int] = []
            seen: set[int] = set()
            for target in targets:
                if target not in seen:
                    seen.add(target)
                    deduped.append(target)
            return deduped, cleaned.strip()

        def _extract_stickers(text_value: str) -> list[str]:
            return re.findall(re.escape(self.d['sticker_prefix']) + r"(.*?)" + re.escape(self.d['sticker_suffix']), text_value or "")

        def _clean_for_display(text_value: str) -> str:
            text_value = text_value or ""
            text_value = re.sub(r"\[\[REPLY:[0-9,\s]+\]\]", "", text_value)
            text_value = re.sub(r"\[REPLY_TO:\d+\]", "", text_value)
            text_value = self.d['game_marker_re'].sub("", text_value)
            text_value = self.d['text_tool_marker_re'].sub("", text_value)
            text_value = re.sub(re.escape(self.d['sticker_prefix']) + r".*?" + re.escape(self.d['sticker_suffix']), "", text_value)
            text_value = re.sub(r"\[REACTION:\d+:[^\]\s]+\]", "", text_value)
            text_value = re.sub(r"\n{3,}", "\n\n", text_value).strip()
            return self.d['deanon_text'](text_value, chat_id)

        def _remove_incomplete_control_suffix(t: str) -> str:
            for marker in ("[REACTION:", "[REPLY_TO:", self.d['sticker_prefix'], "[TOOL:", "[TOOL_RESULT:"):
                idx = t.rfind(marker)
                if idx != -1 and "]" not in t[idx:]:
                    return t[:idx].rstrip()
            for n in range(min(len(t) // 2, 120), 5, -1):
                if t[-n:] == t[-2*n:-n]:
                    return t[:-n].rstrip()
            parts = re.split(r"([。！？!?])", t)
            sentences = []
            for i in range(0, len(parts), 2):
                body = parts[i]
                punct = parts[i + 1] if i + 1 < len(parts) else ""
                if body.strip():
                    sentences.append((body + punct).strip())
            if len(sentences) >= 2 and sentences[-1] == sentences[-2]:
                return t[: -len(sentences[-1])].rstrip()
            return t


        async def _send_text(out_text: str):
            nonlocal sent_msgs, current_reply_targets
            out_text, entities = await get_plugin_manager().enrich_outgoing_text(out_text, chat_id=chat_id)
            targets = list(current_reply_targets or [])

            async def _send_with_reply_fallback(reply_to_id: int | None, mark_replied_target: int | None = None):
                """发送消息，若 reply_to 的消息已被删除则降级为普通发送。"""
                try:
                    if reply_to_id is not None:
                        return await context.bot.send_message(
                            chat_id=chat_id, text=out_text, entities=entities,
                            reply_to_message_id=reply_to_id,
                        )
                    else:
                        return await context.bot.send_message(
                            chat_id=chat_id, text=out_text, entities=entities,
                        )
                except BadRequest as e:
                    if "replied not found" in str(e).lower() and reply_to_id is not None:
                        self.d['logger'].warning(
                            f"回复目标消息已删除，降级为普通发送 | reply_to={reply_to_id}"
                        )
                        return await context.bot.send_message(
                            chat_id=chat_id, text=out_text, entities=entities,
                        )
                    raise

            if free_reply_mode:
                requested_targets = list(targets)
                if not targets and not sent_msgs:
                    targets = [msg.message_id]
                # ── 发送者不在群内：确保所有消息都回复原消息 ──
                if sender_not_in_group:
                    if msg.message_id not in targets:
                        targets.insert(0, msg.message_id)
                # ── 频道消息：确保所有回复引用原消息（不覆盖自由回复模式的多目标逻辑）──
                if sender_is_channel and msg.message_id not in targets:
                    targets.insert(0, msg.message_id)
                targets = [t for t in targets if int(t) in valid_reply_ids]
                # ── 发送者不在群内：即使 valid_reply_ids 过滤后为空，也强行加入原消息 ──
                if sender_not_in_group and msg.message_id not in targets:
                    targets = [msg.message_id]
                if not targets:
                    self.d['logger'].info(
                        f"自由回复：无有效 reply target，fallback 普通发送 | "
                        f"requested={requested_targets} | valid={sorted(valid_reply_ids.keys())[:12]}"
                    )
                    try:
                        if not sent_msgs or sender_not_in_group:
                            m = await _send_with_reply_fallback(msg.message_id, msg.message_id)
                            if m:
                                self.d['mark_replied'](chat_id, msg.message_id, m.message_id)
                        else:
                            m = await _send_with_reply_fallback(None)
                        sent_msgs.append(m)
                        return m
                    except RetryAfter as e:
                        self.d['logger'].warning(f"Telegram flood control: sleep {e.retry_after}s before fallback send")
                        await asyncio.sleep(e.retry_after + 1)
                        if not sent_msgs or sender_not_in_group:
                            m = await _send_with_reply_fallback(msg.message_id, msg.message_id)
                            if m:
                                self.d['mark_replied'](chat_id, msg.message_id, m.message_id)
                        else:
                            m = await _send_with_reply_fallback(None)
                        sent_msgs.append(m)
                        return m
                    except TelegramError as e:
                        self.d['logger'].warning(f"自由回复 fallback 普通发送失败 | err={e}")
                        return None
                last_m = None
                for target_id in targets:
                    try:
                        m = await _send_with_reply_fallback(int(target_id), int(target_id))
                        sent_msgs.append(m)
                        if m:
                            self.d['mark_replied'](chat_id, int(target_id), m.message_id)
                        last_m = m
                    except RetryAfter as e:
                        self.d['logger'].warning(f"Telegram flood control: sleep {e.retry_after}s before free reply")
                        await asyncio.sleep(e.retry_after + 1)
                        try:
                            m = await _send_with_reply_fallback(int(target_id), int(target_id))
                            sent_msgs.append(m)
                            if m:
                                self.d['mark_replied'](chat_id, int(target_id), m.message_id)
                            last_m = m
                        except TelegramError as e2:
                            self.d['logger'].warning(f"自由回复失败，已跳过 | target={target_id} | err={e2}")
                    except TelegramError as e:
                        self.d['logger'].warning(f"自由回复失败，已跳过 | target={target_id} | err={e}")
                return last_m

            # ── 非自由回复：频道 / 退群用户消息，所有段均引用原消息 ──
            should_reply_all = sender_not_in_group or sender_is_channel
            try:
                if not sent_msgs or should_reply_all:
                    m = await _send_with_reply_fallback(msg.message_id, msg.message_id)
                    if m:
                        self.d['mark_replied'](chat_id, msg.message_id, m.message_id)
                else:
                    m = await _send_with_reply_fallback(None)
            except RetryAfter as e:
                self.d['logger'].warning(f"Telegram flood control: sleep {e.retry_after}s before send")
                await asyncio.sleep(e.retry_after + 1)
                if not sent_msgs or should_reply_all:
                    m = await _send_with_reply_fallback(msg.message_id, msg.message_id)
                    if m:
                        self.d['mark_replied'](chat_id, msg.message_id, m.message_id)
                else:
                    m = await _send_with_reply_fallback(None)
            sent_msgs.append(m)
            return m

        async def _set_current_text(out_text: str, force: bool = False):
            nonlocal current_msg, last_edit_at
            if not out_text:
                return
            if free_reply_mode and not force:
                return
            if current_msg is None:
                current_msg = await _send_text(out_text)
                if current_msg is None:
                    return
                last_edit_at = asyncio.get_running_loop().time()
                return
            now = asyncio.get_running_loop().time()
            if (not force) and now - last_edit_at < self.d['get_edit_interval_seconds']():
                return
            try:
                edit_text, edit_entities = await get_plugin_manager().enrich_outgoing_text(out_text, chat_id=chat_id)
                await current_msg.edit_text(edit_text, entities=edit_entities)
                last_edit_at = now
            except RetryAfter as e:
                self.d['logger'].warning(f"Telegram flood control: skip edit after RetryAfter {e.retry_after}s")
            except TelegramError as e:
                if force:
                    self.d['logger'].debug(f"强制落地 edit 未执行，已忽略: {e}")

        async def _execute_reactions_from_text(text_value: str):
            _cleaned, reactions = self.d['extract_reaction_markers'](text_value or "")
            for emoji, target_mid in reactions[:3]:
                key = (emoji, int(target_mid))
                if key in executed_reactions:
                    continue
                executed_reactions.add(key)
                await self.d['set_message_reaction_safe'](context.bot, chat_id, int(target_mid), emoji)

        async def _execute_game_markers_from_text(text_value: str):
            if not text_value or not self.d['game_marker_re'].search(text_value):
                return
            try:
                self.d['clear_active_game'](chat_id)
                self.d['logger'].info(f"🎮 游戏结束标记已处理 | chat={chat_id}")
            except Exception as e:
                self.d['logger'].warning(f"处理游戏结束标记失败: {e}")

        async def _finalize_part(part: str):
            nonlocal current_msg, finalized_parts
            targets, part = _extract_reply_targets(part)
            current_reply_targets[:] = targets
            await _execute_reactions_from_text(part)
            await _execute_game_markers_from_text(part)
            clean = _clean_for_display(part)
            if self.d['de_ai_enabled'] and clean:
                clean = self.d['de_ai'](clean)
            if clean and len(completed_segments) < self.d['get_max_reply_segments']():
                await _set_current_text(clean, force=True)
                completed_segments.append(clean)
            current_msg = None
            finalized_parts += 1

        try:
            async for ev in llm.chat_stream(
                context_mgr=self.d['get_context_mgr'](), chat_id=chat_id,
                current_message=(f"[贴纸:{msg.sticker.emoji}] {text}" if has_sticker and msg.sticker and msg.sticker.emoji and text else
                                (f"[贴纸:{msg.sticker.emoji}]" if has_sticker and msg.sticker and msg.sticker.emoji and not image_b64 else
                                ("[贴纸] " + text if has_sticker and text and not image_b64 else
                                ("[贴纸]" if has_sticker and not image_b64 else text)))),
                current_sender=sender, is_reply=is_reply, is_mention=is_mention,
                image_base64=image_b64,
                image_file_id=image_file_id,
                telegram_context=context,
                persona_users=persona_users,
                trigger_type=trigger_type,
                current_message_id=msg.message_id,
                current_reply_to_message_id=(msg.reply_to_message.message_id if msg.reply_to_message else None),
                chat_title=(msg.chat.title if getattr(msg.chat, 'title', None) else None),
            ):
                if ev.type == "text_chunk":
                    raw_text += ev.text
                    if not completed_segments and raw_text.strip().startswith("[REFUSE"):
                        if trigger_type in ("focus_light_hint", "bot_mention", "bot_called"):
                            self.d['logger'].info(f"🧲 LLM 拒绝插话 [REFUSE] | chat={chat_id}")
                            refused = True
                            raw_text = ""
                            break
                        else:
                            raw_text = raw_text[len("[REFUSE]"):].strip()
                    parts = raw_text.split(self.d['msg_separator'])
                    if len(parts) <= 1 and "\n\n" in raw_text:
                        parts = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
                    complete_count = len(parts) - 1
                    while finalized_parts < complete_count:
                        await _finalize_part(parts[finalized_parts])
                    draft = _clean_for_display(parts[-1])
                    if draft:
                        await _set_current_text(draft)

                elif ev.type == "tool_call":
                    if ev.tool_name in {"remember_group_fact", "update_group_fact", "delete_group_fact"}:
                        memory_tool_used = True
                    if trigger_type != "focus_light_hint":
                        try:
                            if tool_status_msg:
                                await tool_status_msg.delete()
                        except TelegramError:
                            pass
                        try:
                            tool_status_msg = await context.bot.send_message(
                                chat_id=chat_id, text=ev.text or f"🔍 {ev.tool_name}...",
                                disable_notification=True)
                        except TelegramError as e:
                            self.d['logger'].warning(f"发送工具状态消息失败: {e}")
                            tool_status_msg = None
                    current_msg = None
                    raw_text = ""
                    finalized_parts = 0
                    current_reply_targets.clear()

                elif ev.type == "error":
                    self.d['logger'].warning(f"LLM 接口错误: {ev.text}")
                    raw_text = ""
                    break

                elif ev.type == "done":
                    if ev.text:
                        raw_text = ev.text
                    if not completed_segments and raw_text.strip().startswith("[REFUSE"):
                        if trigger_type in ("focus_light_hint", "bot_mention", "bot_called"):
                            self.d['logger'].info(f"🧲 LLM 拒绝插话 [REFUSE] (done) | chat={chat_id}")
                            refused = True
                            raw_text = ""
                        else:
                            raw_text = raw_text[len("[REFUSE]"):].strip()
                    break
        finally:
            typing_stop.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        if tool_status_msg:
            try:
                await tool_status_msg.delete()
            except TelegramError:
                pass

        await _execute_reactions_from_text(raw_text)
        await _execute_game_markers_from_text(raw_text)
        raw_text = _remove_incomplete_control_suffix(raw_text.strip())
        if "[REFUSE" in raw_text:
            self.d['logger'].info(f"🧲 LLM 拒绝插话 [REFUSE] (post) | chat={chat_id}")
            refused = True
            raw_text = ""
        sticker_emojis = _extract_stickers(raw_text)
        parts = raw_text.split(self.d['msg_separator'])
        if len(parts) <= 1 and "\n\n" in raw_text:
            parts = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
        while finalized_parts < len(parts):
            await _finalize_part(parts[finalized_parts])

        try:
            reply_hook_ctx = ReplyHookContext(
                chat_id=chat_id,
                trigger_type=trigger_type,
                raw_text=raw_text,
                segments=completed_segments,
                stickers=sticker_emojis,
                refused=refused,
                reply_to_message_id=getattr(msg, "message_id", None),
                msg=msg,
                telegram_context=context,
            )
            await get_plugin_manager().before_reply(reply_hook_ctx)
            completed_segments = reply_hook_ctx.segments
            sticker_emojis = reply_hook_ctx.stickers
            refused = reply_hook_ctx.refused
        except Exception:
            self.d['logger'].exception("执行插件 before_reply hook 失败")

        self.d['logger'].info(f"🤖 LLM 回复 | {len(completed_segments)} 段 | stickers={sticker_emojis}")

        for emoji in sticker_emojis:
            sticker_mgr = self.d['get_sticker_mgr']()
            if emoji and sticker_mgr:
                file_id = sticker_mgr.get_file_id(emoji)
                if file_id:
                    self.d['logger'].info(f"🎴 回复贴纸 | emoji={emoji} | file_id={file_id[:24]}...")
                    try:
                        await context.bot.send_sticker(chat_id=chat_id, sticker=file_id)
                    except RetryAfter as e:
                        self.d['logger'].warning(f"贴纸发送限流，等待 {e.retry_after}s 后重试 | emoji={emoji}")
                        try:
                            await asyncio.sleep(e.retry_after + 1)
                            await context.bot.send_sticker(chat_id=chat_id, sticker=file_id)
                        except TelegramError as e2:
                            self.d['logger'].warning(f"贴纸重试发送失败 | emoji={emoji} | err={e2}")
                    except TelegramError as e:
                        self.d['logger'].warning(f"贴纸发送失败 | emoji={emoji} | err={e}")
                elif sticker_mgr.available_emojis:
                    import random as _rand
                    fallback_emoji = _rand.choice(sticker_mgr.available_emojis)
                    fallback_id = sticker_mgr.get_file_id(fallback_emoji)
                    if fallback_id:
                        self.d['logger'].info(f"🎴 贴纸 fallback: {emoji} -> {fallback_emoji}")
                        try:
                            await context.bot.send_sticker(chat_id=chat_id, sticker=fallback_id)
                        except TelegramError as e:
                            self.d['logger'].warning(f"贴纸 fallback 发送失败 | err={e}")
                else:
                    self.d['logger'].warning(f"贴纸无 file_id: {emoji}")
                break

        if is_group or is_private:
            try:
                await self.d['record_message'](msg, bot_username, bot_id)
                await self.d['record_bot_response'](chat_id, bot_username, completed_segments, sticker_emojis)
                try:
                    self.d['record_bot_reply'](chat_id, completed_segments, sticker_emojis)
                except Exception as e:
                    self.d['logger'].debug(f"记录拟人化状态失败: {e}")
            except Exception as e:
                self.d['logger'].warning(f"记录对话上下文失败: {e}")

            if self.d['persona_memory_enabled'] and completed_segments and persona_users:
                try:
                    task = context.application.create_task(self.d['update_persona_after_turn'](
                        chat_id=chat_id,
                        users=persona_users,
                        context_messages=context_snapshot,
                        current_message=text or ("[贴纸]" if has_sticker else "[图片]"),
                        bot_reply=" ".join(completed_segments)[:1200],
                        allow_memory_write=(not memory_tool_used),
                    ), name=f"persona-update-{chat_id}")
                    task.add_done_callback(self.d['log_async_task_exception'])
                except Exception as e:
                    self.d['logger'].warning(f"启动人格记忆更新失败: {e}")

            context_mgr = self.d['get_context_mgr']()
            if context_mgr is not None:
                try:
                    await context_mgr.compact_chat(
                        chat_id,
                        self.d['context_max_text_chars'],
                        self.d['bot_context_max_chars'],
                    )
                except Exception as e:
                    self.d['logger'].warning(f"压缩上下文失败: {e}")

        if self.d['conversation_memory_enabled'] and completed_segments:
            bot_reply_text = " ".join(completed_segments)[:150]
            topic_info = self.d['extract_topic'](bot_reply_text)
            if topic_info:
                topic, summary = topic_info
                self.d['get_memory'](chat_id).add_topic(topic, summary)

        return {"segments": completed_segments, "replied": bool(completed_segments or sticker_emojis), "refused": refused}
