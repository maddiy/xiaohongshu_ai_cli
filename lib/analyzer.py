"""评论分析器 — 对笔记评论进行情感、互动、回复优先级和趋势分析。"""
import datetime
import re
from collections import Counter

# 关键词库
_POS_KEYWORDS = ["支持", "赞同", "说得对", "有道理", "认同", "深度", "好文", "干货",
                 "客观", "理性", "同意", "确实", "赞", "好", "厉害", "博主", "关注", "👍"]
_NEG_KEYWORDS = ["扯淡", "胡说", "放屁", "扯", "洗脑", "意淫", "自嗨", "骗", "忽悠", "吹", "无聊"]
_NEUT_KEYWORDS = ["讨论", "但是", "然而", "不过", "另一方面", "不一定", "也许"]
_SKEPTIC_KEYWORDS = ["听起茧子", "说了多少年", "还没完", "又来了", "老生常谈", "耳朵", "说到现在"]
_QUESTION_WORDS = ["为什么", "怎么", "如何", "是否", "是不是", "哪里", "什么", "谁", "吗", "呢"]
_KEYWORD_STOP = {
    "这个", "那个", "就是", "还是", "不是", "没有", "一个", "什么", "怎么", "可以",
    "觉得", "真的", "现在", "已经", "自己", "你们", "他们", "我们", "因为", "所以",
    "但是", "不过", "如果", "然后", "应该", "可能", "这样", "那样", "哈哈", "确实",
}


class CommentAnalyzer:
    """评论分析器"""

    @staticmethod
    def analyze(note_id, xsec_token="", note_title="", force_refresh=False):
        """分析指定笔记的评论，返回分析结果字典"""
        from .xhs_client import XHSClient

        print(f"📊 拉取笔记 {note_id} 的评论...")
        comments, from_cache = XHSClient.get_comments_cached(
            note_id, xsec_token=xsec_token, force_refresh=force_refresh
        )
        if not comments:
            print("❌ 未获取到评论数据")
            return None

        try:
            author_id = XHSClient.get_author_user_id()
        except Exception:
            # 保留旧数据兼容；身份不可用时宁可少报回复率，也不猜测其他用户。
            author_id = _get_author_id(comments)

        # 回复统计
        replied, unreplied, self_comments = _classify_by_reply(comments, author_id)

        # 情感分析
        classified = _classify_sentiment(comments, author_id)

        # 统计数据
        positive = sum(1 for c in classified if c["sentiment"] == "✅ 正面")
        negative = sum(1 for c in classified if c["sentiment"] == "❌ 负面")
        neutral = sum(1 for c in classified if c["sentiment"] == "⚖️ 中立")
        skeptic = sum(1 for c in classified if c["sentiment"] == "🤨 质疑型")
        other = sum(1 for c in classified if c["sentiment"] == "💬 其他")
        total = positive + negative + neutral + skeptic + other

        sorted_by_likes = sorted(classified, key=lambda x: x["likes"], reverse=True)

        # 活跃用户
        user_counter = Counter()
        for c in comments:
            uid = c.get("user_info", {}).get("user_id", "")
            if uid != author_id:
                user_counter[c.get("user_info", {}).get("nickname", "?")] += 1
        active_users = {k: v for k, v in user_counter.items() if v > 1}
        eligible_count = len(replied) + len(unreplied)
        reply_rate = round(len(replied) * 100 / eligible_count, 1) if eligible_count else 0.0
        question_count = sum(_is_question(item["content"]) for item in classified)
        keywords = _extract_keywords(classified)
        timeline = _build_timeline(comments, author_id)
        reply_priorities = _build_reply_priorities(comments, author_id)
        average_likes = round(
            sum(item["likes"] for item in classified) / len(classified), 1
        ) if classified else 0.0
        recommendations = _build_recommendations(
            reply_rate=reply_rate,
            unreplied=len(unreplied),
            negative=negative,
            skeptic=skeptic,
            positive=positive,
            total=total,
            question_count=question_count,
            keywords=keywords,
        )

        return {
            "note_id": note_id,
            "note_title": note_title,
            "total_comments": len(comments),
            "unique_users": len(set(c.get("user_info", {}).get("nickname", "?") for c in comments)),
            "total_likes": sum(int(c.get("like_count", 0) or 0) for c in comments),
            "total_subs": sum(int(c.get("sub_comment_count", 0) or 0) for c in comments),
            "self_comments": len(self_comments),
            "replied": len(replied),
            "unreplied": len(unreplied),
            "reply_rate": reply_rate,
            "average_likes": average_likes,
            "question_count": question_count,
            "engagement_total": sum(
                int(c.get("like_count", 0) or 0)
                + int(c.get("sub_comment_count", 0) or 0)
                for c in comments
            ),
            "classified": classified,
            "sorted_by_likes": sorted_by_likes,
            "active_users": active_users,
            "keywords": keywords,
            "timeline": timeline,
            "reply_priorities": reply_priorities,
            "recommendations": recommendations,
            "counts": {
                "positive": positive, "negative": negative,
                "neutral": neutral, "skeptic": skeptic, "other": other
            }
        }

    @staticmethod
    def print_report(result):
        """打印格式化的分析报告"""
        if not result:
            print("❌ 无分析数据")
            return

        c = result["counts"]
        total = sum(c.values())

        print(f"\n{'='*60}")
        print(f"📊 基本数据")
        print(f"{'='*60}")
        print(f"笔记标题: {result.get('note_title') or '(未提供)'}")
        print(f"一级评论数: {result['total_comments']}")
        print(f"独立用户数: {result['unique_users']}")
        print(f"评论总点赞: {result['total_likes']}")
        print(f"楼中楼总数: {result['total_subs']}")

        print(f"\n作者回复: 已回复 {result['replied']} / 未回复 {result['unreplied']}"
              + (f" / 回复率 {result['replied']/(result['replied']+result['unreplied'])*100:.1f}%"
                 if (result['replied'] + result['unreplied']) > 0 else ""))
        print(f"总互动: {result.get('engagement_total', 0)} / 平均点赞: "
              f"{result.get('average_likes', 0)} / 提问评论: {result.get('question_count', 0)}")

        print(f"\n{'='*60}")
        print(f"📈 情感分类")
        print(f"{'='*60}")
        print(f"✅ 正面: {c['positive']} ({c['positive']/total*100:.1f}%)" if total > 0 else "无数据")
        print(f"🤨 质疑/讽刺: {c['skeptic']} ({c['skeptic']/total*100:.1f}%)" if total > 0 else "")
        print(f"⚖️ 中立讨论: {c['neutral']} ({c['neutral']/total*100:.1f}%)" if total > 0 else "")
        print(f"❌ 负面: {c['negative']} ({c['negative']/total*100:.1f}%)" if total > 0 else "")
        print(f"💬 其他/纯信息: {c['other']} ({c['other']/total*100:.1f}%)" if total > 0 else "")

        print(f"\n{'='*60}")
        print(f"🔥 最热门评论 TOP 10（按点赞数）")
        print(f"{'='*60}")
        for i, item in enumerate(result["sorted_by_likes"][:10]):
            print(f"{i+1}. [{item['sentiment']}] 👍{item['likes']} @{item['nick']}: {item['content']}")

        print(f"\n{'='*60}")
        print(f"📝 各类型典型评论")
        print(f"{'='*60}")
        for label, sent_key in [("✅ 正面支持", "✅ 正面"), ("🤨 质疑/讽刺", "🤨 质疑型"),
                                 ("⚖️ 中立讨论", "⚖️ 中立"), ("❌ 负面批评", "❌ 负面")]:
            samples = [x for x in result["classified"] if x["sentiment"] == sent_key]
            print(f"\n{label}（共{len(samples)}条）:")
            for item in samples[:5]:
                print(f"  · @{item['nick']}: {item['content']}")

        print(f"\n{'='*60}")
        print(f"👥 活跃用户（评论数超过1条）")
        print(f"{'='*60}")
        if result["active_users"]:
            for nick, cnt in sorted(result["active_users"].items(), key=lambda x: x[1], reverse=True):
                print(f"  · {nick}: {cnt} 条")
        else:
            print("  无用户发多条评论")

        print(f"\n{'='*60}\n🔑 讨论关键词\n{'='*60}")
        print("  " + " / ".join(
            f"{item['term']}({item['count']})" for item in result.get("keywords", [])
        ) if result.get("keywords") else "  暂无稳定重复关键词")

        print(f"\n{'='*60}\n🎯 优先回复\n{'='*60}")
        for index, item in enumerate(result.get("reply_priorities", [])[:10], 1):
            print(f"{index}. 优先分{item['priority_score']} @{item['nick']}: {item['content']}")

        print(f"\n{'='*60}\n💡 运营建议\n{'='*60}")
        for item in result.get("recommendations", []):
            print(f"  · {item}")


# ------- helpers -------

def _get_author_id(comments):
    """从评论中推断作者ID（第一条作者自己的评论）"""
    for c in comments:
        nick = c.get("user_info", {}).get("nickname", "").lower()
        for kw in ["博主", "作者"]:
            if kw in nick:
                return c.get("user_info", {}).get("user_id", "")
    return ""


def _classify_by_reply(comments, author_id):
    """按是否被作者回复分类"""
    replied, unreplied, self_comments = [], [], []
    for c in comments:
        uid = c.get("user_info", {}).get("user_id", "")
        nick = c.get("user_info", {}).get("nickname", "?")
        content = c.get("content", "")
        if uid == author_id:
            self_comments.append((nick, content))
            continue
        subs = c.get("sub_comments", [])
        has_reply = any(sc.get("user_info", {}).get("user_id", "") == author_id for sc in subs)
        if has_reply:
            replied.append((nick, content))
        else:
            unreplied.append((nick, content))
    return replied, unreplied, self_comments


def _classify_sentiment(comments, author_id):
    """情感分类"""
    results = []
    for c in comments:
        uid = c.get("user_info", {}).get("user_id", "")
        if uid == author_id:
            continue
        content = c.get("content", "")
        nick = c.get("user_info", {}).get("nickname", "?")
        likes = int(c.get("like_count", 0) or 0)

        has_pos = any(kw in content for kw in _POS_KEYWORDS)
        has_neg = any(kw in content for kw in _NEG_KEYWORDS)
        has_skeptic = any(kw in content for kw in _SKEPTIC_KEYWORDS)
        has_neut = any(kw in content for kw in _NEUT_KEYWORDS)

        if has_skeptic:
            sentiment = "🤨 质疑型"
        elif has_neg:
            sentiment = "❌ 负面"
        elif has_pos:
            sentiment = "✅ 正面"
        elif has_neut:
            sentiment = "⚖️ 中立"
        else:
            sentiment = "💬 其他"

        results.append({
            "nick": nick,
            "content": content,
            "sentiment": sentiment,
            "likes": likes,
        })
    return results


def _is_question(content):
    text = str(content or "")
    return "?" in text or "？" in text or any(word in text for word in _QUESTION_WORDS)


def _extract_keywords(classified, limit=10):
    """用跨评论文档频率提取稳定短语，避免单条长评论刷满关键词。"""
    frequency = Counter()
    for item in classified:
        text = str(item.get("content", "") or "")
        terms = set()
        terms.update(re.findall(r"#[^#\s，。！？,.!?]{2,20}", text))
        terms.update(word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,19}", text))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for size in (4, 3, 2):
                for index in range(len(run) - size + 1):
                    term = run[index:index + size]
                    if term not in _KEYWORD_STOP:
                        terms.add(term)
        frequency.update(terms)
    minimum = 2 if len(classified) >= 3 else 1
    ranked = sorted(
        ((term, count) for term, count in frequency.items() if count >= minimum),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    selected = []
    for term, count in ranked:
        if any(term in existing or existing in term for existing, _ in selected):
            continue
        selected.append((term, count))
        if len(selected) >= limit:
            break
    return [{"term": term, "count": count} for term, count in selected]


def _comment_day(comment):
    value = comment.get("create_time") or comment.get("time") or comment.get("created_at")
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.datetime.fromtimestamp(number).astimezone().strftime("%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", str(value or ""))
        return match.group(0)[5:].replace("/", "-") if match else ""


def _build_timeline(comments, author_id):
    counts = Counter()
    for comment in comments:
        if comment.get("user_info", {}).get("user_id", "") == author_id:
            continue
        day = _comment_day(comment)
        if day:
            counts[day] += 1
    return [{"date": day, "count": counts[day]} for day in sorted(counts)[-14:]]


def _build_reply_priorities(comments, author_id, limit=10):
    priorities = []
    for comment in comments:
        user = comment.get("user_info", {}) or {}
        if user.get("user_id", "") == author_id:
            continue
        subs = comment.get("sub_comments", []) or []
        if any((sub.get("user_info", {}) or {}).get("user_id", "") == author_id for sub in subs):
            continue
        likes = int(comment.get("like_count", 0) or 0)
        sub_count = int(comment.get("sub_comment_count", 0) or 0)
        content = str(comment.get("content", "") or "")
        question = _is_question(content)
        score = likes * 2 + sub_count * 3 + (4 if question else 0)
        priorities.append({
            "nick": user.get("nickname", "?"),
            "content": content,
            "likes": likes,
            "sub_count": sub_count,
            "is_question": question,
            "priority_score": score,
        })
    return sorted(
        priorities,
        key=lambda item: (-item["priority_score"], -item["likes"]),
    )[:limit]


def _build_recommendations(**metrics):
    recommendations = []
    if metrics["unreplied"] and metrics["reply_rate"] < 60:
        recommendations.append("优先处理高互动、提问型未回复评论，提升回复覆盖率。")
    if metrics["total"] and (metrics["negative"] + metrics["skeptic"]) / metrics["total"] >= 0.3:
        recommendations.append("质疑和负面反馈占比较高，建议补充事实依据并避免情绪化争辩。")
    if metrics["question_count"]:
        recommendations.append(f"发现{metrics['question_count']}条提问型评论，可整理成后续选题或统一答疑。")
    if metrics["keywords"]:
        recommendations.append(f"讨论焦点集中在“{metrics['keywords'][0]['term']}”，后续内容可优先回应该主题。")
    if metrics["total"] and metrics["positive"] / metrics["total"] >= 0.6:
        recommendations.append("正面反馈占比较高，可延续当前选题方向并补充更深入内容。")
    return recommendations or ["当前样本较少，建议积累更多评论后再判断稳定趋势。"]
