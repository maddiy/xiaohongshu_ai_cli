"""
智能回复器
支持三种模式: smart(逐条确认) / generic(通用话术) / draft(先生成草稿再发送)
支持跳过列表：回复失败后保留 failed 状态并加入排除列表，下次扫描自动跳过
"""
from contextlib import nullcontext
import time
import random
from config import (
    BATCH_REPLY_PAUSE_EVERY,
    BATCH_REPLY_PAUSE_SECONDS,
    GENERIC_REPLIES,
    REQUEST_DELAY,
)
from .cli_support import NON_RESEND_STATUSES, write_json
from .xhs_client import XHSClient


class Replier:
    """回复评论"""

    def __init__(self, client: XHSClient = None):
        self.client = client or XHSClient()
        self.stats = {"success": 0, "fail": 0, "skip": 0}

    # ---------- 回复生成 ----------
    @staticmethod
    def generate_reply(comment: dict, strategy: str = "generic") -> str:
        """
        根据评论内容生成回复

        strategy:
          - "generic": 从话术池随机选一条
          - "smart": 返回空字符串，由调用方 reply_batch 处理交互（见 strategy=="smart" 分支）
        """
        if strategy == "generic":
            return random.choice(GENERIC_REPLIES)
        # smart 策略由 reply_batch 中的交互逻辑处理，这里返回空字符串兜底
        return ""

    # ---------- 内部工具 ----------
    def _exclude_on_failure(self, cid: str, nick: str, content: str,
                            err: str, note_id: str,
                            err_type: str = "unknown_error"):
        """
        回复失败处理：所有失败评论统一加入跳过列表，避免后续扫描或其他 AI 重试。
        """
        reason = f"api_error[{err_type}]: {err[:50]}"
        self.client.add_skipped(
            cid, nick, content,
            reason=reason, note_id=note_id
        )
        if err_type == "comment_deleted":
            print(f"  📁 评论已删除，已加入跳过列表")
        elif err_type == "content_rejected":
            print(
                "  📁 内容被拦截，已加入跳过列表"
                "（重试需用户授权并重置失败状态）"
            )
        else:
            print(f"  📁 回复失败，已自动加入跳过列表")

    # ---------- 草稿模式 ----------
    def generate_drafts(self, unreplied: list,
                        note_id: str = "",
                        note_title: str = "") -> dict:
        """
        交互式生成所有回复草稿（逐条手动输入）

        返回:
            {
                "note_id": str,
                "note_title": str,
                "generated_at": str,
                "drafts": [{"comment_id", "nickname", "content", "reply", "action"}]
            }
        """
        drafts = {
            "note_id": note_id,
            "note_title": note_title,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "drafts": [],
        }

        total = len(unreplied)
        if total == 0:
            return drafts

        print(f"\n{'='*60}")
        print(f"📝 生成回复草稿 — {total} 条评论")
        if note_title:
            print(f"  文章: {note_title}")
        print(f"{'='*60}\n")

        for i, c in enumerate(unreplied):
            nick = c["nickname"]
            content = c["content"]
            cid = c["comment_id"]

            print(f"[{i+1}/{total}] @{nick}: {content}")

            reply_text = input("  💬 回复内容 (回车=跳过, 's'=永久跳过): ").strip()

            action = "send"
            if reply_text == "":
                action = "skip"
                print(f"  ⏭️ 本次跳过")
            elif reply_text.lower() == "s":
                action = "archive"
                reply_text = ""
                print(f"  📁 永久跳过（加入跳过列表）")
            else:
                print(f"  ✅ 草稿: {reply_text[:50]}")

            drafts["drafts"].append({
                "comment_id": cid,
                "nickname": nick,
                "content": content,
                "reply": reply_text,
                "action": action,
            })

        to_send = sum(1 for d in drafts["drafts"] if d["action"] == "send")
        to_skip = sum(1 for d in drafts["drafts"] if d["action"] == "skip")
        to_archive = sum(1 for d in drafts["drafts"] if d["action"] == "archive")
        print(f"\n📊 草稿生成完成")
        print(f"  ✅ 待发送: {to_send} | ⏭️ 本次跳过: {to_skip} | 📁 永久跳过: {to_archive}")
        return drafts

    def generate_drafts_from_mapping(self, unreplied: list,
                                     reply_map: dict,
                                     note_id: str = "",
                                     note_title: str = "") -> dict:
        """
        批量导入回复映射，非交互式生成草稿

        参数:
            unreplied: 未回复评论列表
            reply_map: {"comment_id": "回复文案", ...}  或  {"comment_id": {"reply": "...", "action": "send|skip|archive"}}
            note_id: 笔记ID
            note_title: 笔记标题
        """
        drafts = {
            "note_id": note_id,
            "note_title": note_title,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "drafts": [],
        }

        total = len(unreplied)
        to_send = to_skip = to_archive = 0
        unmatched = 0

        for c in unreplied:
            cid = c["comment_id"]
            nick = c["nickname"]
            content = c["content"]

            if cid in reply_map:
                entry = reply_map[cid]
                if isinstance(entry, dict):
                    reply_text = entry.get("reply", "")
                    action = entry.get("action", "send")
                    review = entry.get("review", {})
                else:
                    reply_text = str(entry)
                    action = "send"
                    review = {}
            else:
                # 映射中没有的评论，默认跳过
                reply_text = ""
                action = "skip"
                review = {}
                unmatched += 1

            drafts["drafts"].append({
                "comment_id": cid,
                "nickname": nick,
                "content": content,
                "reply": reply_text,
                "action": action,
                **({"review": review} if review else {}),
            })

            if action == "send":
                to_send += 1
            elif action == "archive":
                to_archive += 1
            else:
                to_skip += 1

        print(f"\n📊 批量草稿生成完成")
        print(f"  ✅ 待发送: {to_send} | ⏭️ 本次跳过: {to_skip} | 📁 永久跳过: {to_archive}")
        if unmatched > 0:
            print(f"  ⚠️ {unmatched} 条评论在映射中未找到，已自动跳过")
        return drafts

    @staticmethod
    def _save_state(drafts: dict, state_file: str = None):
        """原子替换草稿状态文件，避免中断后丢失已发送进度。"""
        if not state_file:
            return
        write_json(drafts, state_file)

    def send_drafts(self, drafts: dict, resume: bool = False,
                    state_file: str = None) -> dict:
        """
        根据草稿文件批量发送回复

        参数:
            drafts: generate_drafts() 的输出或从 JSON 文件加载的同构字典
            resume: 兼容参数，仅改变续发提示文案；终态和排除过滤始终执行

        返回: {"success": int, "fail": int, "skip": int}
        """
        note_id = drafts.get("note_id", "")
        items = drafts.get("drafts", [])
        active_ids = drafts.get("active_comment_ids")
        if isinstance(active_ids, list):
            active_ids = set(active_ids)
            items = [
                item for item in items
                if item.get("comment_id") in active_ids
            ]

        # 默认终态均不重复发送。failed只有在用户授权并由外部同时清理
        # skipped记录、重置草稿状态后才能重新进入待发送集合。
        to_send = [
            d for d in items
            if d.get("action") == "send"
            and d.get("send_status") not in NON_RESEND_STATUSES
        ]
        to_archive = [
            d for d in items
            if d.get("action") == "archive"
            and d.get("send_status") != "archived"
        ]

        # 先处理永久跳过（加入跳过列表）
        archived_count = 0
        for d in to_archive:
            cid = d["comment_id"]
            if not self.client.is_skipped(cid):
                self.client.add_skipped(
                    d["comment_id"], d["nickname"], d["content"],
                    reason="manual", note_id=note_id
                )
                archived_count += 1
            d["send_status"] = "archived"
            self.stats["skip"] += 1
        if archived_count:
            print(f"📁 {archived_count} 条新评论已加入跳过列表")
        self._save_state(drafts, state_file)

        # 无论由哪个 AI 接续，都跳过已发送或已归档的评论。
        remaining = []
        completed_skip = 0
        for d in to_send:
            if (d.get("send_status") == "sent"
                    or self.client.is_skipped(d["comment_id"])):
                completed_skip += 1
            else:
                remaining.append(d)
        if completed_skip:
            label = "断点续发" if resume else "状态检查"
            print(f"🔄 {label}: {completed_skip} 条已完成/归档，跳过")
        to_send = remaining

        # 发送回复
        total = len(to_send)
        if total == 0:
            print("✨ 没有需要发送的回复")
            return self.stats

        print(f"\n{'='*60}")
        print(f"🚀 发送回复 — {total} 条")
        print(f"{'='*60}")

        session_factory = getattr(type(self.client), "reply_session", None)
        reply_method = getattr(type(self.client), "reply", None)
        native_reply_method = (
            getattr(reply_method, "__module__", "") == "lib.xhs_client"
        )
        session_context = (
            self.client.reply_session(note_id)
            if callable(session_factory) and native_reply_method
            else nullcontext(None)
        )
        stop_error_types = {
            "rate_limited",
            "verification_required",
            "not_authenticated",
            "session_error",
        }
        with session_context as reply_session:
            persistent = bool(
                reply_session is not None
                and getattr(reply_session, "process", None) is not None
            )
            for i, d in enumerate(to_send):
                cid = d["comment_id"]
                nick = d["nickname"]
                content = d["content"]
                reply = d["reply"]

                print(f"\n[{i+1}/{total}] @{nick}")
                print(f"  💬 评论：{content}")
                print(f"  ✏️ 回复：{reply}")

                # 在平台写请求之前先落盘。若进程在请求期间退出，状态会停在
                # sending，后续流程只能在线对账，不能自动重复发送。
                d["send_status"] = "sending"
                d["send_started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save_state(drafts, state_file)
                if reply_session is not None:
                    ok, err, err_type = reply_session.reply(cid, reply)
                else:
                    ok, err, err_type = self.client.reply(
                        note_id, cid, reply
                    )
                if ok:
                    print(f"  ✅ 成功")
                    self.stats["success"] += 1
                    d["send_status"] = "sent"
                    d["sent_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    d.pop("send_started_at", None)
                    d.pop("last_error", None)
                    d.pop("error_type", None)
                else:
                    print(f"  ❌ 失败 ({err_type}): {err[:120]}")
                    self.stats["fail"] += 1
                    d["send_status"] = "failed"
                    d.pop("send_started_at", None)
                    d["error_type"] = err_type
                    d["last_error"] = err[:200]
                    self._exclude_on_failure(
                        cid, nick, content, err, note_id, err_type
                    )

                self._save_state(drafts, state_file)
                if not ok and err_type in stop_error_types:
                    self.stats["stopped"] = True
                    self.stats["stop_reason"] = err_type
                    self.stats["remaining"] = total - i - 1
                    print(
                        "  ⏸️ 检测到账号/会话级错误，已停止本批，"
                        "避免后续评论连续失败"
                    )
                    break
                # 持久会话内部已经执行动态间隔和随机抖动；旧命令模式保留
                # 原有固定间隔，避免重复等待拖慢推荐路径。
                if not persistent and i + 1 < total:
                    time.sleep(REQUEST_DELAY)
                elif (
                    persistent
                    and BATCH_REPLY_PAUSE_EVERY > 0
                    and (i + 1) % BATCH_REPLY_PAUSE_EVERY == 0
                    and i + 1 < total
                ):
                    print(
                        f"  ⏸️ 已连续发送{i + 1}条，"
                        f"主动休息{BATCH_REPLY_PAUSE_SECONDS:g}秒"
                    )
                    time.sleep(BATCH_REPLY_PAUSE_SECONDS)

        self._print_summary()
        return self.stats

    # ---------- 传统批量回复 ----------
    def reply_batch(self, note_id: str, unreplied: list,
                    strategy: str = "smart",
                    note_title: str = "") -> dict:
        """
        批量回复（传统模式）

        参数:
            note_id: 笔记ID
            unreplied: [{"comment_id", "nickname", "content", "parent_comment_id"(可选)}]
            strategy: "smart" | "generic"
            note_title: 笔记标题（用于日志显示）

        返回: {"success": int, "fail": int, "skip": int}
        """
        total = len(unreplied)
        if total == 0:
            print("  ✨ 无需回复")
            return self.stats

        print(f"\n{'='*60}")
        print(f"📝 准备回复 {total} 条评论")
        if note_title:
            print(f"  文章: {note_title}")
        print(f"  策略: {strategy}")
        print(f"{'='*60}")

        for i, c in enumerate(unreplied):
            nick = c["nickname"]
            content = c["content"]
            cid = c["comment_id"]

            print(f"\n[{i+1}/{total}] @{nick}: {content}")

            if strategy == "smart":
                reply_text = input("  💬 回复内容 (回车=跳过, 's'=永久跳过): ").strip()
                if not reply_text:
                    print("  ⏭️ 跳过")
                    self.stats["skip"] += 1
                    continue
                if reply_text.lower() == "s":
                    self.client.add_skipped(
                        cid, nick, content,
                        reason="manual", note_id=note_id
                    )
                    print("  📁 永久跳过（已加入跳过列表）")
                    self.stats["skip"] += 1
                    continue
            else:
                reply_text = self.generate_reply(c, strategy)
                print(f"  🤖 自动回复: {reply_text[:50]}")

            ok, err, err_type = self.client.reply(note_id, cid, reply_text)
            if ok:
                print(f"  ✅ 成功")
                self.stats["success"] += 1
            else:
                print(f"  ❌ 失败 ({err_type}): {err[:120]}")
                self.stats["fail"] += 1
                self._exclude_on_failure(cid, nick, content, err, note_id, err_type)

            time.sleep(REQUEST_DELAY)

        self._print_summary()
        return self.stats

    # ---------- 全量回复 ----------
    def reply_all_unreplied(self, scan_results: list,
                            strategy: str = "smart") -> dict:
        """
        根据扫描结果批量回复所有未回复评论

        参数:
            scan_results: CommentScanner.scan_all_notes() 的返回结果
            strategy: 回复策略
        """
        total_l1 = 0
        total_subs = 0
        self.stats = {"success": 0, "fail": 0, "skip": 0}

        for r in scan_results:
            note_id = r["note_id"]
            title = r.get("note_title", "")
            unreplied = r.get("unreplied_level1", [])
            subs = r.get("unreplied_subs", [])

            if not unreplied and not subs:
                print(f"\n✨ [{title}] 全部已回复")
                continue

            # 回复一级评论
            if unreplied:
                print(f"\n{'—'*40}")
                print(f"📌 一级评论 ({title})")
                self.reply_batch(note_id, unreplied, strategy, title)
                total_l1 += len(unreplied)

            # 回复楼中楼
            if subs:
                print(f"\n{'—'*40}")
                print(f"📌 楼中楼 ({title})")
                self.reply_batch(note_id, subs, strategy, title)
                total_subs += len(subs)

        print(f"\n{'='*60}")
        print(f"📊 全部完成")
        print(f"  一级评论: {total_l1} 条")
        print(f"  楼中楼:   {total_subs} 条")
        self._print_summary()

        return self.stats

    def _print_summary(self):
        s = self.stats
        print(f"  ✅ 成功: {s['success']} | ❌ 失败: {s['fail']} | ⏭️ 跳过: {s['skip']}")
