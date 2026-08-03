"""最新评论通知扫描；由 :class:`CommentScanner` 组合使用。"""


class NotificationScannerMixin:
    """把通知候选收集与全量评论扫描职责分开。"""

    def scan_via_notifications(self, note_id: str = None,
                                verbose: bool = True,
                                num_notifications: int = 50,
                                verify_replied: bool = True,
                                excluded_comment_ids=None,
                                verification_max_pages: int = 50,
                                allow_partial_verification: bool = False) -> dict:
        """从最新评论通知收集候选，并按需在线核验回复状态。"""
        excluded_comment_ids = set(excluded_comment_ids or ())
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
            filtered_local = 0
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
                if comment_id in excluded_comment_ids:
                    filtered_local += 1
                    continue
                candidates.append({
                    "comment_id": comment_id,
                    "nickname": notification.get("nickname", "?"),
                    "content": notification.get("content", ""),
                    "likes": 0,
                    "sub_count": 0,
                    **({
                        "target_comment_id": notification["target_comment_id"],
                    } if notification.get("target_comment_id") else {}),
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
                        max_pages=verification_max_pages,
                        allow_partial=allow_partial_verification,
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
                "deferred_online": sum(
                    item.get("reason") == "online_unresolved"
                    for item in online_excluded
                ),
                "filtered_local": filtered_local,
            }
            if verification_error:
                result["scan_error"] = verification_error
            results.append(result)

            if verbose:
                print(
                    f"\n📌 {len(results)}. "
                    f"{entry.get('note_title', '') or '无标题'}（{eid}）"
                )
                print(
                    f"   通知中 {len(new_comments)} 条新评论，"
                    f"实际未回复 {len(unreplied)} 条"
                )
                for item in unreplied:
                    print(
                        f"   ⚠️ [一级] @{item['nickname']}: "
                        f"{item['content']}"
                    )

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

        all_level1 = []
        all_subs = []
        for result in results:
            all_level1.extend(result["unreplied_level1"])
            all_subs.extend(result["unreplied_subs"])

        return {
            "note_id": "all",
            "note_title": "",
            "unreplied_level1": all_level1,
            "unreplied_subs": all_subs,
            "total_new_notifications": sum(
                result.get("total_new_notifications", 0)
                for result in results
            ),
            "source": "notifications",
            "per_note": results,
            "reply_status_verified": all(
                item.get("reply_status_verified", False) for item in results
            ),
        }
