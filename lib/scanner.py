"""
评论扫描器
负责：发现文章 → 扫描评论和楼中楼 → 过滤已回复 → 输出未回复列表
"""
import time
from config import READ_PAGE_DELAY
from .xhs_client import XHSClient
from .scanner_notifications import NotificationScannerMixin
from .scanner_online import OnlineVerificationMixin


class CommentScanner(OnlineVerificationMixin, NotificationScannerMixin):
    """扫描笔记中所有未回复的评论（含楼中楼）"""

    def __init__(self, client: XHSClient = None, author_user_id: str = ""):
        self.client = client or XHSClient()
        self._author_user_id = str(author_user_id or "")

    @property
    def author_user_id(self):
        if not self._author_user_id:
            value = self.client.get_author_user_id()
            if not isinstance(value, str) or not value:
                raise RuntimeError("无法自动识别当前小红书账号ID")
            self._author_user_id = value
        return self._author_user_id

    # ---------- 笔记发现 ----------
    def list_notes_with_comments(self, max_pages: int = None):
        """
        列出所有有评论的笔记
        参数:
            max_pages: 最多翻多少页（None 表示取完所有）
        """
        notes = self.client.get_my_notes(max_pages=max_pages)
        return [n for n in notes if n["comments_count"] > 0]

    def _extract_non_author_subs(self, sub_comments: list, parent_id: str,
                                 parent_nick: str, skipped_ids: set = None,
                                 replied_ids: set = None) -> list:
        """提取尚未被作者直接回复的非作者楼中楼评论。"""
        result = []
        replied_ids = replied_ids or set()
        for sc in sub_comments:
            if (
                sc.get("user_info", {}).get("user_id", "")
                != self.author_user_id
            ):
                if (
                    (skipped_ids and sc["id"] in skipped_ids)
                    or sc["id"] in replied_ids
                ):
                    continue
                result.append({
                    "comment_id": sc["id"],
                    "nickname": sc.get("user_info", {}).get("nickname", "?"),
                    "content": sc.get("content", ""),
                    "likes": int(sc.get("like_count", 0) or 0),
                    "sub_count": 0,
                    "parent_comment_id": parent_id,
                    "parent_nickname": parent_nick,
                })
        return result

    @staticmethod
    def _extract_comment(c: dict) -> dict:
        sub_count = int(c.get("sub_comment_count", 0) or 0)
        inline_subs = c.get("sub_comments", [])
        return {
            "comment_id": c["id"],
            "nickname": c.get("user_info", {}).get("nickname", "?"),
            "content": c.get("content", ""),
            "likes": int(c.get("like_count", 0) or 0),
            "sub_count": sub_count,
            "inline_subs_count": len(inline_subs),
            "inline_subs": inline_subs,  # 内联楼中楼数据，可直接用于检测作者回复
        }

    # ---------- 核心扫描 ----------
    def scan_note(self, note_id: str, xsec_token: str = "",
                  include_sub_comments: bool = False,
                  verbose: bool = True,
                  force_refresh: bool = False) -> dict:
        """
        扫描一篇笔记的所有未回复评论

        一级评论总是完整拉取。
        楼中楼默认不拉取（仅依靠内联数据判断是否已回复），
        设置 include_sub_comments=True 才会通过子命令逐条拉取完整楼中楼。

        返回:
            {
                "note_id": str,
                "xsec_token": str,
                "total": int,              # 一级评论总数
                "unreplied_level1": [],    # 未回复的一级评论
                "unreplied_subs": [],      # 未回复的楼中楼（include_sub_comments=False 时为空）
                "pending_subs": int,       # 还有多少楼中楼待按需拉取
                "skipped": int,            # 无法获取楼中楼的评论数
            }
        """
        resolved_xsec_token = xsec_token
        # 全量楼中楼扫描需要令牌。先从0600本地索引命中，避免先进行一次
        # 无令牌的完整分页后才失败重来。
        if include_sub_comments and not resolved_xsec_token:
            try:
                token = self.client.find_note_xsec(note_id)
                if isinstance(token, str):
                    resolved_xsec_token = token
            except RuntimeError:
                pass
        if resolved_xsec_token and verbose:
            print(f"  🔑 使用指定的 xsec_token")

        if verbose:
            print(f"📥 拉取全部一级评论...")
        comments, from_cache = self.client.get_comments_cached(
            note_id,
            resolved_xsec_token,
            force_refresh=force_refresh,
            include_sub_comments=include_sub_comments,
        )
        if include_sub_comments and not resolved_xsec_token and any(
            int(item.get("sub_comment_count", 0) or 0)
            > len(item.get("sub_comments", []))
            for item in comments
        ):
            try:
                token = self.client.find_note_xsec(note_id)
                if isinstance(token, str):
                    resolved_xsec_token = token
            except RuntimeError:
                pass

        # 加载跳过列表
        skipped_ids = self.client.get_skipped_ids()

        unreplied_l1 = []
        unreplied_subs = []
        skipped_sub = 0
        filtered_skipped = 0
        pending_sub_comment_ids = []
        verification_errors = []

        reply_status_verified = True
        for c in comments:
            info = self._extract_comment(c)
            if self._is_author(c):
                continue

            cid = info["comment_id"]
            inline_subs = info["inline_subs"]
            subs = inline_subs
            subs_complete = info["sub_count"] <= info["inline_subs_count"]
            inline_targets = self._author_reply_targets(inline_subs, cid)

            # 一级评论和楼中楼必须独立判断。即使一级评论已回复，只要用户
            # 要求扫描楼中楼，仍需拉取这个楼层的完整数据。
            must_fetch = (
                not subs_complete
                and (include_sub_comments or cid not in inline_targets)
            )
            if must_fetch:
                try:
                    if READ_PAGE_DELAY:
                        time.sleep(READ_PAGE_DELAY)
                    subs = self.client.get_sub_comments(
                        note_id, cid, resolved_xsec_token
                    )
                    subs_complete = len(subs) >= info["sub_count"]
                    if not subs_complete:
                        skipped_sub += 1
                        reply_status_verified = False
                        verification_errors.append(
                            f"楼中楼数据不完整: {cid}"
                        )
                        if verbose:
                            print(f"  ⚠️ 楼中楼数据不完整: @{info['nickname']}")
                except Exception as error:
                    subs = inline_subs
                    subs_complete = False
                    skipped_sub += 1
                    reply_status_verified = False
                    verification_errors.append(
                        f"楼中楼拉取失败: {cid}: {error}"
                    )
                    if verbose:
                        print(f"  ⚠️ 楼中楼拉取失败: {error}")

            reply_targets = self._author_reply_targets(subs, cid)

            if cid in skipped_ids:
                filtered_skipped += 1
                if verbose:
                    print(f"  ⏭️ [已跳过] @{info['nickname']}: {info['content']}")
            elif cid not in reply_targets and (
                subs_complete or info["sub_count"] == 0
            ):
                unreplied_l1.append(info)
                if verbose:
                    print(f"  ⚠️ [一级] @{info['nickname']}: {info['content']}")

            if not subs_complete and not include_sub_comments:
                pending_sub_comment_ids.append({
                    "comment_id": cid,
                    "nickname": info["nickname"],
                    "expected_subs": info["sub_count"],
                })
                continue

            # 完整楼中楼中的每条用户评论单独判断，作者回复其他评论不能
            # 让整个楼层被视为已回复。
            if subs_complete:
                before = len(unreplied_subs)
                unreplied_subs.extend(self._extract_non_author_subs(
                    subs,
                    cid,
                    info["nickname"],
                    skipped_ids,
                    reply_targets,
                ))
                filtered_skipped += sum(
                    1 for sub in subs
                    if sub.get("id", "") in skipped_ids
                )
                added = len(unreplied_subs) - before
                if verbose and added:
                    print(f"  🔴 [楼中楼] @{info['nickname']}: {added} 条未回")

        result = {
            "note_id": note_id,
            "xsec_token": xsec_token,
            "total": len(comments),
            "unreplied_level1": unreplied_l1,
            "unreplied_subs": unreplied_subs,
            "pending_subs": len(pending_sub_comment_ids),
            "skipped": skipped_sub,
            "filtered_skipped": filtered_skipped,
            "reply_status_verified": reply_status_verified,
        }
        if verification_errors:
            result["scan_error"] = "；".join(verification_errors)

        if verbose:
            l1_count = len(unreplied_l1)
            subs_count = len(unreplied_subs)
            print(f"\n📊 一级评论 {len(comments)} | 未回一级 {l1_count} | 未回楼中楼 {subs_count} | 已跳过 {filtered_skipped} | 待拉取楼中楼 {len(pending_sub_comment_ids)}个楼层")

        return result

    # ---------- 按需拉取楼中楼 ----------
    def fetch_unreplied_subs(self, note_id: str,
                             comment_ids: list = None,
                             verbose: bool = True) -> list:
        """
        按需拉取指定评论的楼中楼中未回复的内容

        参数:
            note_id: 笔记 ID
            comment_ids: 需要拉取的评论ID列表，None 表示从缓存中自动判断
            verbose: 是否打印日志

        返回: 未回复的楼中楼列表 [{comment_id, nickname, content, parent_comment_id, parent_nickname}]
        """
        # 获取跳过列表
        skipped_ids = self.client.get_skipped_ids()

        # 先获取一级评论数据，找出需要拉取楼中楼的评论
        comments, _ = self.client.get_comments_cached(note_id)
        unreplied_subs = []
        filtered_count = 0

        targets = []
        for c in comments:
            cid = c["id"]
            if comment_ids and cid not in comment_ids:
                continue
            if self._is_author(c):
                continue
            sc_count = int(c.get("sub_comment_count", 0) or 0)
            inline_subs = c.get("sub_comments", [])
            if sc_count > 0:
                targets.append((cid, c))

        if verbose and targets:
            print(f"\n🔍 按需拉取 {len(targets)} 个评论的完整楼中楼...")

        for cid, c in targets:
            nick = c.get("user_info", {}).get("nickname", "?")
            sc_count = int(c.get("sub_comment_count", 0) or 0)
            if verbose:
                print(f"  📥 @{nick}: {c.get('content','')} (sub_count={sc_count})")

            inline_subs = c.get("sub_comments", [])
            if sc_count <= len(inline_subs):
                subs = inline_subs
            else:
                if READ_PAGE_DELAY:
                    time.sleep(READ_PAGE_DELAY)
                try:
                    subs = self.client.get_sub_comments(note_id, cid)
                except Exception as e:
                    if verbose:
                        print(f"    ⚠️ 失败: {e}")
                    continue

            if len(subs) < sc_count:
                if verbose:
                    print(f"    ⚠️ 数据不完整（可能需要浏览器验证）")
                continue

            replied_ids = self._author_reply_targets(subs, cid)
            count = 0
            for sc in subs:
                if self._is_author(sc):
                    continue
                sub_id = sc["id"]
                if sub_id in skipped_ids or sub_id in replied_ids:
                    filtered_count += 1
                    continue
                sub_info = {
                    "comment_id": sub_id,
                    "nickname": sc.get("user_info", {}).get("nickname", "?"),
                    "content": sc.get("content", ""),
                    "likes": int(sc.get("like_count", 0) or 0),
                    "sub_count": 0,
                    "parent_comment_id": cid,
                    "parent_nickname": nick,
                }
                unreplied_subs.append(sub_info)
                count += 1

            if verbose:
                print(f"    ✅ 获取 {len(subs)} 条楼中楼，其中 {count} 条未回复" + (f"（跳过 {filtered_count} 条）" if filtered_count else ""))

        if verbose:
            print(f"\n📊 楼中楼拉取完成，共 {len(unreplied_subs)} 条未回复" + (f"，过滤跳过 {filtered_count} 条" if filtered_count else ""))

        return unreplied_subs

    def scan_all_notes(self, include_sub_comments: bool = False,
                       force_refresh: bool = False,
                       max_pages: int = None,
                       verbose: bool = True) -> list:
        """扫描所有有评论的笔记，返回每篇笔记的扫描结果列表"""
        results = []
        notes = self.list_notes_with_comments(max_pages=max_pages)
        if verbose:
            print(f"\n找到 {len(notes)} 篇有评论的笔记\n")

        for i, note in enumerate(notes):
            title = note["title"][:40] or "(无标题)"
            if verbose:
                print(f"\n{'='*60}")
                print(f"[{i+1}/{len(notes)}] {title} ({note['id']})")
                print(f"  评论数: {note['comments_count']}")
                print(f"{'='*60}")

            try:
                r = self.scan_note(
                    note["id"],
                    note.get("xsec_token", ""),
                    include_sub_comments=include_sub_comments,
                    force_refresh=force_refresh,
                    verbose=verbose,
                )
                r["note_title"] = title
                results.append(r)
            except RuntimeError as e:
                if verbose:
                    print(f"  ❌ 扫描失败: {e}")

        return results
