"""
智能回复器
支持三种模式: smart(逐条确认) / generic(通用话术) / draft(先生成草稿再发送)
支持跳过列表：回复失败的评论自动存档，下次扫描自动跳过
"""
import json
import os
import time
import random
from config import REQUEST_DELAY, GENERIC_REPLIES
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
    def _archive_on_failure(self, cid: str, nick: str, content: str,
                             err: str, note_id: str, err_type: str = "unknown_error"):
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
            print(f"  📁 内容被拦截，已加入跳过列表（可调整措辞后手动移除重试）")
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
            content = c["content"][:60]
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
            content = c["content"][:60]

            if cid in reply_map:
                entry = reply_map[cid]
                if isinstance(entry, dict):
                    reply_text = entry.get("reply", "")
                    action = entry.get("action", "send")
                else:
                    reply_text = str(entry)
                    action = "send"
            else:
                # 映射中没有的评论，默认跳过
                reply_text = ""
                action = "skip"
                unmatched += 1

            drafts["drafts"].append({
                "comment_id": cid,
                "nickname": nick,
                "content": content,
                "reply": reply_text,
                "action": action,
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
        state_file = os.path.abspath(state_file)
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        temp_file = f"{state_file}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(drafts, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_file, state_file)

    def send_drafts(self, drafts: dict, resume: bool = False,
                    state_file: str = None) -> dict:
        """
        根据草稿文件批量发送回复

        参数:
            drafts: generate_drafts() 的输出或从 JSON 文件加载的同构字典
            resume: 断点续发模式，发送前检查是否已在跳过列表中（已发送/已失败的跳过）

        返回: {"success": int, "fail": int, "skip": int}
        """
        note_id = drafts.get("note_id", "")
        items = drafts.get("drafts", [])

        # 已成功发送的草稿永远不重复发送，确保不同 AI 可安全接续。
        to_send = [
            d for d in items
            if d.get("action") == "send"
            and d.get("send_status") not in ("sent", "failed", "archived")
        ]
        to_archive = [d for d in items if d.get("action") == "archive"]

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

        for i, d in enumerate(to_send):
            cid = d["comment_id"]
            nick = d["nickname"]
            content = d["content"]
            reply = d["reply"]

            print(f"\n[{i+1}/{total}] @{nick}")
            print(f"  💬 评论：{content}")
            print(f"  ✏️ 回复：{reply}")

            ok, err, err_type = self.client.reply(note_id, cid, reply)
            if ok:
                print(f"  ✅ 成功")
                self.stats["success"] += 1
                d["send_status"] = "sent"
                d["sent_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                d.pop("last_error", None)
                d.pop("error_type", None)
            else:
                print(f"  ❌ 失败 ({err_type}): {err[:120]}")
                self.stats["fail"] += 1
                d["send_status"] = "failed"
                d["error_type"] = err_type
                d["last_error"] = err[:200]
                self._archive_on_failure(cid, nick, content, err, note_id, err_type)

            self._save_state(drafts, state_file)
            time.sleep(REQUEST_DELAY)

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
            content = c["content"][:60]
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
                self._archive_on_failure(cid, nick, content, err, note_id, err_type)

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
