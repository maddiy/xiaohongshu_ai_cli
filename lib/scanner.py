"""
评论扫描器
负责：发现文章 → 扫描评论和楼中楼 → 过滤已回复 → 输出未回复列表
"""
import time
from config import AUTHOR_USER_ID, REQUEST_DELAY
from .xhs_client import XHSClient


class CommentScanner:
    """扫描笔记中所有未回复的评论（含楼中楼）"""

    def __init__(self, client: XHSClient = None):
        self.client = client or XHSClient()

    # ---------- 笔记发现 ----------
    def list_notes_with_comments(self, max_pages: int = None):
        """
        列出所有有评论的笔记
        参数:
            max_pages: 最多翻多少页（None 表示取完所有）
        """
        notes = self.client.get_my_notes(max_pages=max_pages)
        return [n for n in notes if n["comments_count"] > 0]

    # ---------- 解析辅助 ----------
    @staticmethod
    def _is_author(comment: dict) -> bool:
        return comment.get("user_info", {}).get("user_id", "") == AUTHOR_USER_ID

    @staticmethod
    def _has_author_reply(sub_comments: list) -> bool:
        return any(
            sc.get("user_info", {}).get("user_id", "") == AUTHOR_USER_ID
            for sc in sub_comments
        )

    @staticmethod
    def _extract_non_author_subs(sub_comments: list, parent_id: str,
                                  parent_nick: str) -> list:
        """从内联楼中楼数据中提取非作者的楼中楼评论（无需额外API调用）"""
        result = []
        for sc in sub_comments:
            if sc.get("user_info", {}).get("user_id", "") != AUTHOR_USER_ID:
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
    def scan_via_notifications(self, note_id: str = None,
                                verbose: bool = True,
                                num_notifications: int = 50,
                                verify_replied: bool = True) -> dict:
        """
        通过通知快速扫描：只从最新评论通知中提取该文章的新评论，
        不需要拉取全部评论做对比。

        如果 note_id 为 None，返回所有文章的通知聚合结果。

        参数:
            note_id: 指定笔记ID，None 表示所有笔记
            verbose: 打印详情
            num_notifications: 拉取的通知数量
            verify_replied: 是否通过拉取一级评论验证"是否已回复"（默认True）
                           设为 False 则通知中出现的都视为未回复

        返回:
            {
                "note_id": str,                    # 或 "all"
                "note_title": str,
                "unreplied_level1": [],            # 通知中出现的未回复一级评论
                "unreplied_subs": [],
                "total_new_notifications": int,    # 通知中该文章的新评论数
                "source": "notifications",         # 标识来源
            }
        """
        if verbose:
            print(f"📬 拉取最新 {num_notifications} 条评论通知...")
        aggregated = self.client.get_new_comment_notifications(num=num_notifications)

        if verbose:
            print(f"   通知覆盖 {len(aggregated)} 篇笔记")

        skipped_ids = self.client.get_skipped_ids()
        results = []

        for entry in aggregated:
            eid = entry["note_id"]
            if note_id and eid != note_id:
                continue

            new_comments = entry["new_comments"]
            if not new_comments:
                continue

            # 验证"是否已回复"：拉取该笔记的一级评论，获取内联楼中楼数据
            # 这样只拉一次全部评论，而不是每条评论单独调API
            replied_ids = set()
            if verify_replied:
                try:
                    comments, _ = self.client.get_comments_cached(
                        eid, entry.get("note_xsec_token", "")
                    )
                    for c in comments:
                        cid = c.get("id", "")
                        inline_subs = c.get("sub_comments", [])
                        if self._has_author_reply(inline_subs):
                            replied_ids.add(cid)
                except Exception:
                    if verbose:
                        print(f"  ⚠️ 无法拉取评论验证回复状态，假定全部未回复")

            unreplied = []
            for nc in new_comments:
                cid = nc["comment_id"]
                if not cid:
                    continue
                if cid in skipped_ids:
                    continue
                if verify_replied and cid in replied_ids:
                    continue  # 已回复，跳过
                unreplied.append({
                    "comment_id": cid,
                    "nickname": nc["nickname"],
                    "content": nc["content"],
                    "likes": 0,
                    "sub_count": 0,
                    "inline_subs_count": 0,
                    "inline_subs": [],
                })

            result = {
                "note_id": eid,
                "note_title": entry.get("note_title", ""),
                "unreplied_level1": unreplied,
                "unreplied_subs": [],
                "total_new_notifications": len(new_comments),
                "source": "notifications",
                "note_xsec_token": entry.get("note_xsec_token", ""),
            }
            results.append(result)

            if verbose:
                print(f"\n📌 {entry.get('note_title', eid)}")
                print(f"   通知中 {len(new_comments)} 条新评论，实际未回复 {len(unreplied)} 条")
                for u in unreplied:
                    print(f"   ⚠️ [一级] @{u['nickname']}: {u['content'][:60]}")

        if not results:
            return {
                "note_id": note_id or "",
                "note_title": "",
                "unreplied_level1": [],
                "unreplied_subs": [],
                "total_new_notifications": 0,
                "source": "notifications",
            }

        if note_id and results:
            return results[0]

        # 多笔记聚合
        all_l1 = []
        all_subs = []
        for r in results:
            all_l1.extend(r["unreplied_level1"])
            all_subs.extend(r["unreplied_subs"])

        return {
            "note_id": "all",
            "note_title": "",
            "unreplied_level1": all_l1,
            "unreplied_subs": all_subs,
            "total_new_notifications": sum(r.get("total_new_notifications", 0) for r in results),
            "source": "notifications",
            "per_note": results,  # 每个笔记的详细结果
        }

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
        # 如果没有 xsec_token，尝试查找
        if not xsec_token:
            try:
                xsec_token = self.client.find_note_xsec(note_id)
                if verbose:
                    print(f"  找到 xsec_token: {xsec_token[:20]}...")
            except RuntimeError:
                if verbose:
                    print(f"  ⚠️ 未找到 xsec_token，尝试不带 token 请求...")

        if verbose:
            print(f"📥 拉取全部一级评论...")
        comments, from_cache = self.client.get_comments_cached(
            note_id, xsec_token, force_refresh=force_refresh
        )

        # 加载跳过列表
        skipped_ids = self.client.get_skipped_ids()

        unreplied_l1 = []
        unreplied_subs = []
        skipped_sub = 0
        filtered_skipped = 0  # 被跳过列表过滤的数量
        pending_sub_comment_ids = []  # 有待拉取楼中楼的评论ID列表

        for c in comments:
            info = self._extract_comment(c)

            # 跳过作者自己的评论
            if self._is_author(c):
                continue

            # 跳过已存档的评论
            if info["comment_id"] in skipped_ids:
                filtered_skipped += 1
                if verbose:
                    print(f"  ⏭️ [已跳过] @{info['nickname']}: {info['content'][:50]}")
                continue

            # 检查一级评论是否已回复（通过内联 sub_comments）
            inline_subs = info["inline_subs"]
            if not self._has_author_reply(inline_subs):
                unreplied_l1.append(info)
                if verbose:
                    print(f"  ⚠️ [一级] @{info['nickname']}: {info['content'][:50]}")

            # 判断是否需要拉取完整楼中楼
            if info["sub_count"] > info["inline_subs_count"]:
                # 内联数据不全，需要单独拉取
                if include_sub_comments:
                    if verbose:
                        print(f"    🔍 拉取楼中楼 (sub_count={info['sub_count']}, inline={info['inline_subs_count']})...")
                    try:
                        time.sleep(REQUEST_DELAY * 0.3)
                        subs = self.client.get_sub_comments(note_id, info["comment_id"])
                        if subs:
                            for sc in subs:
                                if not self._is_author(sc):
                                    sub_info = {
                                        "comment_id": sc["id"],
                                        "nickname": sc.get("user_info", {}).get("nickname", "?"),
                                        "content": sc.get("content", ""),
                                        "likes": int(sc.get("like_count", 0) or 0),
                                        "sub_count": 0,
                                        "parent_comment_id": info["comment_id"],
                                        "parent_nickname": info["nickname"],
                                    }
                                    unreplied_subs.append(sub_info)
                            if verbose:
                                count = sum(1 for s in subs if not self._is_author(s))
                                print(f"      🔴 [楼中楼] {count}条未回 (@{info['nickname']}的楼层)")
                        else:
                            skipped_sub += 1
                            if verbose:
                                print(f"      ⚠️ 楼中楼未获取到数据（可能需要验证）")
                    except Exception as e:
                        skipped_sub += 1
                        if verbose:
                            print(f"      ⚠️ 楼中楼获取异常: {e}")
                else:
                    # 不自动拉取，标记为待处理
                    pending_sub_comment_ids.append({
                        "comment_id": info["comment_id"],
                        "nickname": info["nickname"],
                        "expected_subs": info["sub_count"] - info["inline_subs_count"],
                    })
            elif info["inline_subs_count"] > 0:
                # 内联数据已完整（sub_count == inline_subs_count），直接从内联数据提取非作者楼中楼
                inline_non_author = self._extract_non_author_subs(
                    inline_subs, info["comment_id"], info["nickname"]
                )
                if inline_non_author:
                    unreplied_subs.extend(inline_non_author)
                    if verbose:
                        print(f"    📋 [内联楼中楼] {len(inline_non_author)}条非作者回复 (@{info['nickname']}的楼层)")

            # 优化：仅在发生网络请求后才延迟，纯内存处理不延迟
            # 延迟已在 get_sub_comments 前添加，这里不需要额外延迟

        result = {
            "note_id": note_id,
            "xsec_token": xsec_token,
            "total": len(comments),
            "unreplied_level1": unreplied_l1,
            "unreplied_subs": unreplied_subs,
            "pending_subs": len(pending_sub_comment_ids),
            "skipped": skipped_sub,
            "filtered_skipped": filtered_skipped,
        }

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
        from config import REQUEST_DELAY

        # 先获取一级评论数据，找出需要拉取楼中楼的评论
        comments, _ = self.client.get_comments_cached(note_id)
        unreplied_subs = []

        targets = []
        for c in comments:
            cid = c["id"]
            if comment_ids and cid not in comment_ids:
                continue
            if self._is_author(c):
                continue
            sc_count = int(c.get("sub_comment_count", 0) or 0)
            inline_subs = c.get("sub_comments", [])
            if sc_count > len(inline_subs):
                targets.append((cid, c))

        if verbose and targets:
            print(f"\n🔍 按需拉取 {len(targets)} 个评论的完整楼中楼...")

        for cid, c in targets:
            nick = c.get("user_info", {}).get("nickname", "?")
            sc_count = int(c.get("sub_comment_count", 0) or 0)
            if verbose:
                print(f"  📥 @{nick}: {c.get('content','')[:40]}... (sub_count={sc_count})")

            time.sleep(REQUEST_DELAY * 0.3)
            try:
                subs = self.client.get_sub_comments(note_id, cid)
            except Exception as e:
                if verbose:
                    print(f"    ⚠️ 失败: {e}")
                continue

            if not subs:
                if verbose:
                    print(f"    ⚠️ 未获取到数据（可能需要浏览器验证）")
                continue

            count = 0
            for sc in subs:
                if not self._is_author(sc):
                    sub_info = {
                        "comment_id": sc["id"],
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
                print(f"    ✅ 获取 {len(subs)} 条楼中楼，其中 {count} 条未回复")

        if verbose:
            print(f"\n📊 楼中楼拉取完成，共 {len(unreplied_subs)} 条未回复")

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
