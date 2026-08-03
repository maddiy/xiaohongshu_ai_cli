"""评论候选的在线存在性、楼中楼完整性和作者回复核验。"""


class OnlineVerificationMixin:
    # ---------- 解析辅助 ----------
    def _is_author(self, comment: dict) -> bool:
        return (
            comment.get("user_info", {}).get("user_id", "")
            == self.author_user_id
        )

    def _author_reply_targets(self, sub_comments: list,
                              parent_id: str = "") -> set:
        """返回作者明确回复到的评论 ID；旧数据缺少目标时视为回复一级评论。"""
        targets = set()
        for sub in sub_comments:
            if (
                sub.get("user_info", {}).get("user_id", "")
                != self.author_user_id
            ):
                continue
            target_id = sub.get("target_comment", {}).get("id", "")
            if target_id:
                targets.add(target_id)
            elif parent_id:
                targets.add(parent_id)
        return targets

    def _has_author_reply(self, sub_comments: list,
                          target_id: str = "") -> bool:
        """兼容旧调用；提供 target_id 时只判断作者是否回复了该评论。"""
        if target_id:
            return target_id in self._author_reply_targets(
                sub_comments, target_id
            )
        return bool(self._author_reply_targets(sub_comments))

    def _online_reply_index(self, comments: list) -> tuple:
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
                if (
                    sub.get("user_info", {}).get("user_id", "")
                    != self.author_user_id
                ):
                    continue
                target_id = sub.get("target_comment", {}).get("id", "")
                if target_id:
                    replied_ids.add(target_id)
                elif comment_id:
                    replied_ids.add(comment_id)
        return existing_ids, replied_ids

    @staticmethod
    def _thread_comment_ids(comment: dict) -> set:
        """返回一个一级评论楼层当前已加载的全部评论ID。"""
        ids = {comment.get("id", "")}
        ids.update(
            sub.get("id", "")
            for sub in comment.get("sub_comments", [])
        )
        ids.discard("")
        return ids

    def _expand_online_thread(self, note_id: str, comment: dict,
                              xsec_token: str = "") -> tuple:
        """补全一个楼层，返回(是否完整, 错误说明)，不吞掉不完整状态。"""
        inline_subs = comment.get("sub_comments", [])
        expected = int(comment.get("sub_comment_count", 0) or 0)
        if expected <= len(inline_subs):
            return True, ""
        comment_id = comment.get("id", "")
        try:
            try:
                full_subs = self.client.get_sub_comments(
                    note_id, comment_id, xsec_token, strict=True
                )
            except TypeError:
                # 兼容旧客户端替身。
                full_subs = self.client.get_sub_comments(
                    note_id, comment_id, xsec_token
                )
        except Exception as error:
            return False, f"{comment_id}: {error}"
        if len(full_subs) < expected:
            return (
                False,
                f"{comment_id}: 期望{expected}条，实际{len(full_subs)}条",
            )
        comment["sub_comments"] = full_subs
        return True, ""

    def verify_candidates_online(self, note_id: str, candidates: list,
                                 xsec_token: str = "",
                                 max_pages: int = 50,
                                 allow_partial: bool = False) -> tuple:
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
        if not candidate_ids:
            return [], []
        context_groups = []
        context_anchors = []
        context_ids = set()
        context_hints = {}
        for candidate in candidates:
            comment_id = candidate.get("comment_id", "")
            if not comment_id:
                continue
            group = {comment_id}
            anchors = []
            for key in ("parent_comment_id", "target_comment_id"):
                hint_id = candidate.get(key, "")
                if hint_id:
                    group.add(hint_id)
                    anchors.append(hint_id)
            anchors.append(comment_id)
            context_groups.append(group)
            context_anchors.append(list(dict.fromkeys(anchors)))
            context_hints[comment_id] = set(anchors[:-1])
            context_ids.update(group)
        try:
            lookup = self.client.get_comments_until_ids(
                note_id,
                candidate_ids,
                xsec_token,
                max_pages=max_pages,
                with_status=True,
                target_groups=context_groups,
                target_anchors=context_anchors,
                expand_unresolved=not allow_partial,
            )
        except TypeError:
            # 兼容尚未支持target_groups的旧客户端替身。
            lookup = self.client.get_comments_until_ids(
                note_id, candidate_ids, xsec_token, with_status=True
            )
        if isinstance(lookup, tuple):
            comments, search_complete = lookup
        else:
            # 兼容旧客户端与测试替身；新客户端会返回完整性标志。
            comments, search_complete = lookup, True

        # 先严格补全已经定位到候选的楼层。作者对候选的直接回复只会位于
        # 同一楼层，因此无须让确定无关的楼层阻断整个候选批次。
        checked_threads = set()
        for comment in comments:
            if not (
                self._thread_comment_ids(comment) & context_ids
            ):
                continue
            complete, error = self._expand_online_thread(
                note_id, comment, xsec_token
            )
            if not complete:
                raise RuntimeError(
                    "楼中楼在线数据不完整，无法安全判断是否已经回复: "
                    f"{error}"
                )
            checked_threads.add(id(comment))

        # 候选若未出现在一级评论或内联楼中楼中，可能藏在任一尚未展开的
        # 楼层。逐层尝试定位；无关楼层拉取失败先记录，不立即阻断。只要
        # 所有候选最终都在完整楼层中找到，这些无关失败就不影响安全判断。
        existing_ids, _ = self._online_reply_index(comments)
        completed_thread_ids = []
        for comment in comments:
            if id(comment) in checked_threads:
                completed_thread_ids.append(
                    self._thread_comment_ids(comment)
                )
        context_resolved_missing = {
            comment_id
            for comment_id, hints in context_hints.items()
            if (
                comment_id not in existing_ids
                and hints
                and any(hints & ids for ids in completed_thread_ids)
            )
        }
        # 已知父楼层已完整展开时，候选不在其中即可安全判定为已删除，
        # 不需要再遍历与它无关的其他楼层。
        unresolved_ids = (
            candidate_ids - existing_ids - context_resolved_missing
        )
        deferred_incomplete = []
        if unresolved_ids and not allow_partial:
            for comment in comments:
                if id(comment) in checked_threads:
                    continue
                inline_subs = comment.get("sub_comments", [])
                expected = int(
                    comment.get("sub_comment_count", 0) or 0
                )
                if expected <= len(inline_subs):
                    continue
                complete, error = self._expand_online_thread(
                    note_id, comment, xsec_token
                )
                checked_threads.add(id(comment))
                if not complete:
                    deferred_incomplete.append(error)
                    continue
                existing_ids, _ = self._online_reply_index(comments)
                unresolved_ids = (
                    candidate_ids
                    - existing_ids
                    - context_resolved_missing
                )
                if not unresolved_ids:
                    break

        existing_ids, replied_ids = self._online_reply_index(comments)
        unresolved_ids = (
            candidate_ids - existing_ids - context_resolved_missing
        )
        if unresolved_ids and deferred_incomplete:
            if not allow_partial:
                raise RuntimeError(
                    "候选评论尚未定位，且部分楼中楼在线数据不完整，"
                    "无法安全判断评论是否已删除: "
                    + "；".join(deferred_incomplete)
                )
        if unresolved_ids and not search_complete:
            if not allow_partial:
                raise RuntimeError(
                    "达到在线核验页数上限，尚未找到全部候选评论；"
                    "无法安全判断评论是否已删除"
                )

        eligible = []
        excluded = []
        for candidate in candidates:
            comment_id = candidate.get("comment_id", "")
            if comment_id in replied_ids:
                excluded.append({"comment_id": comment_id, "reason": "online_replied"})
            elif comment_id not in existing_ids:
                reason = (
                    "online_unresolved"
                    if allow_partial and (
                        not search_complete or deferred_incomplete
                    )
                    else "online_missing"
                )
                excluded.append({"comment_id": comment_id, "reason": reason})
            else:
                eligible.append(candidate)
        return eligible, excluded
