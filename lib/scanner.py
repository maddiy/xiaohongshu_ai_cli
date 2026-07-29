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
    def _author_reply_targets(sub_comments: list, parent_id: str = "") -> set:
        """返回作者明确回复到的评论 ID；旧数据缺少目标时视为回复一级评论。"""
        targets = set()
        for sub in sub_comments:
            if sub.get("user_info", {}).get("user_id", "") != AUTHOR_USER_ID:
                continue
            target_id = sub.get("target_comment", {}).get("id", "")
            if target_id:
                targets.add(target_id)
            elif parent_id:
                targets.add(parent_id)
        return targets

    @classmethod
    def _has_author_reply(cls, sub_comments: list,
                          target_id: str = "") -> bool:
        """兼容旧调用；提供 target_id 时只判断作者是否回复了该评论。"""
        if target_id:
            return target_id in cls._author_reply_targets(
                sub_comments, target_id
            )
        return bool(cls._author_reply_targets(sub_comments))

    @staticmethod
    def _online_reply_index(comments: list) -> tuple:
        """
        从平台实时评论树建立"存在的评论"和"作者已直接回复的评论"索引。

        作者回复楼中楼时，以 target_comment.id 为准，不能只把所属的
        一级评论标记为已回复，否则会漏掉人工回复过的楼中楼评论。
        """
        existing_ids = set()
        replied_ids = set()
        for comment in comments:
            comment_id = comment.get("id", "")
            if comment_id:
                existing_ids.add(comment_id)
            for sub in comment.get("sub_comments", []):
                sub_id = sub.get("id", "")
                if sub_id:
                    existing_ids.add(sub_id)
                if sub.get("user_info", {}).get("user_id", "") != AUTHOR_USER_ID:
                    continue
                target_id = sub.get("target_comment", {}).get("id", "")
                if target_id:
                    replied_ids.add(target_id)
                elif comment_id:
                    replied_ids.add(comment_id)
        return existing_ids, replied_ids

    def verify_candidates_online(self, note_id: str, candidates: list,
                                 xsec_token: str = "") -> tuple:
        """
        草稿生成前强制从平台重新核验候选评论。

        返回 (可回复评论, 已排除明细)。在线请求失败时抛出异常，调用方
        必须停止生成草稿，不能用本地数据库状态代替平台状态。
        """
        if not xsec_token:
            try:
                resolved = self.client.find_note_xsec(note_id)
                if isinstance(resolved, str):
                    xsec_token = resolved
            except RuntimeError:
                pass

        candidate_ids = {
            item.get("comment_id", "") for item in candidates
            if item.get("comment_id")
        }
        lookup = self.client.get_comments_until_ids(
            note_id, candidate_ids, xsec_token, with_status=True
        )
        if isinstance(lookup, tuple):
            comments, search_complete = lookup
        else:
            # 兼容旧客户端与测试替身；新客户端会返回完整性标志。
            comments, search_complete = lookup, True
        if not search_complete:
            raise RuntimeError(
                "达到在线核验页数上限，尚未找到全部候选评论；"
                "无法安全判断评论是否已删除"
            )

        # 必须展开所有不完整楼层：候选本身或作者对候选的回复都可能位于
        # 未展示的楼中楼中。只展开"当前看见候选"的楼层仍会误判。
        for comment in comments:
            inline_subs = comment.get("sub_comments", [])
            expected = int(comment.get("sub_comment_count", 0) or 0)
            if expected <= len(inline_subs):
                continue
            full_subs = self.client.get_sub_comments(
                note_id, comment.get("id", ""), xsec_token
            )
            if len(full_subs) < expected:
                raise RuntimeError(
                    "楼中楼在线数据不完整，无法安全判断是否已经回复: "
                    f"{comment.get('id', '')}"
                )
            comment["sub_comments"] = full_subs

        existing_ids, replied_ids = self._online_reply_index(comments)
        eligible = []
        excluded = []
        for candidate in candidates:
            comment_id = candidate.get("comment_id", "")
            if comment_id in replied_ids:
                excluded.append({"comment_id": comment_id, "reason": "online_replied"})
            elif comment_id not in existing_ids:
                excluded.append({"comment_id": comment_id, "reason": "online_missing"})
            else:
                eligible.append(candidate)
        return eligible, excluded

    @staticmethod
    def _extract_non_author_subs(sub_comments: list, parent_id: str,
                                 parent_nick: str, skipped_ids: set = None,
                                 replied_ids: set = None) -> list:
        """提取尚未被作者直接回复的非作者楼中楼评论。"""
        result = []
        replied_ids = replied_ids or set()
        for sc in sub_comments:
            if sc.get("user_info", {}).get("user_id", "") != AUTHOR_USER_ID:
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
        try:
            aggregated = self.client.get_new_comment_notifications(
                num=num_notifications
            )
        except Exception as error:
            if verbose:
                print(f"❌ 读取评论通知失败: {error}")
            return {
                "note_id": note_id or "",
                "note_title": "",
                "unreplied_level1": [],
                "unreplied_subs": [],
                "total_new_notifications": 0,
                "source": "notifications",
                "reply_status_verified": False,
                "scan_error": str(error),
            }

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

            candidates = []
            filtered_deleted = 0
            filtered_skipped = 0
            for notification in new_comments:
                comment_id = notification.get("comment_id", "")
                if not comment_id:
                    continue
                if notification.get("deleted", False):
                    filtered_deleted += 1
                    continue
                if comment_id in skipped_ids:
                    filtered_skipped += 1
                    continue
                candidates.append({
                    "comment_id": comment_id,
                    "nickname": notification.get("nickname", "?"),
                    "content": notification.get("content", ""),
                    "likes": 0,
                    "sub_count": 0,
                })

            reply_status_verified = not verify_replied or not candidates
            online_excluded = []
            verification_error = ""
            if verify_replied and candidates:
                try:
                    candidates, online_excluded = self.verify_candidates_online(
                        eid,
                        candidates,
                        entry.get("note_xsec_token", ""),
                    )
                    reply_status_verified = True
                except Exception as error:
                    candidates = []
                    reply_status_verified = False
                    verification_error = str(error)
                    if verbose:
                        print(f"  ❌ 在线核验失败，未返回待回复候选: {error}")

            unreplied = candidates

            result = {
                "note_id": eid,
                "note_title": entry.get("note_title", ""),
                "unreplied_level1": unreplied,
                "unreplied_subs": [],
                "total_new_notifications": len(new_comments),
                "source": "notifications",
                "note_xsec_token": entry.get("note_xsec_token", ""),
                "reply_status_verified": reply_status_verified,
                "filtered_deleted": filtered_deleted,
                "filtered_skipped": filtered_skipped,
                "filtered_online": len(online_excluded),
            }
            if verification_error:
                result["scan_error"] = verification_error
            results.append(result)

            if verbose:
                print(
                    f"\n📌 {len(results)}. "
                    f"{entry.get('note_title', '') or '无标题'}（{eid}）"
                )
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
                "reply_status_verified": verify_replied,
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
            "reply_status_verified": all(
                item.get("reply_status_verified", False) for item in results
            ),
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
        # 不再主动查找 xsec_token，由 get_comments_cached 内部按需降级
        if xsec_token and verbose:
            print(f"  🔑 使用指定的 xsec_token")

        if verbose:
            print(f"📥 拉取全部一级评论...")
        comments, from_cache = self.client.get_comments_cached(
            note_id, xsec_token, force_refresh=force_refresh
        )
        resolved_xsec_token = xsec_token
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
                    time.sleep(REQUEST_DELAY * 0.3)
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
                    print(f"  ⏭️ [已跳过] @{info['nickname']}: {info['content'][:50]}")
            elif cid not in reply_targets and (
                subs_complete or info["sub_count"] == 0
            ):
                unreplied_l1.append(info)
                if verbose:
                    print(f"  ⚠️ [一级] @{info['nickname']}: {info['content'][:50]}")

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
        from config import REQUEST_DELAY

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
                print(f"  📥 @{nick}: {c.get('content','')[:40]}... (sub_count={sc_count})")

            inline_subs = c.get("sub_comments", [])
            if sc_count <= len(inline_subs):
                subs = inline_subs
            else:
                time.sleep(REQUEST_DELAY * 0.3)
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
