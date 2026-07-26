"""评论分析器 — 对笔记评论进行情感分类、回复统计、热门排序等分析"""
from collections import Counter

# 关键词库
_POS_KEYWORDS = ["支持", "赞同", "说得对", "有道理", "认同", "深度", "好文", "干货",
                 "客观", "理性", "同意", "确实", "赞", "好", "厉害", "博主", "关注", "👍"]
_NEG_KEYWORDS = ["扯淡", "胡说", "放屁", "扯", "洗脑", "意淫", "自嗨", "骗", "忽悠", "吹", "无聊"]
_NEUT_KEYWORDS = ["讨论", "但是", "然而", "不过", "另一方面", "不一定", "也许"]
_SKEPTIC_KEYWORDS = ["听起茧子", "说了多少年", "还没完", "又来了", "老生常谈", "耳朵", "说到现在"]


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
            "classified": classified,
            "sorted_by_likes": sorted_by_likes,
            "active_users": active_users,
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
            print(f"{i+1}. [{item['sentiment']}] 👍{item['likes']} @{item['nick']}: {item['content'][:80]}")

        print(f"\n{'='*60}")
        print(f"📝 各类型典型评论")
        print(f"{'='*60}")
        for label, sent_key in [("✅ 正面支持", "✅ 正面"), ("🤨 质疑/讽刺", "🤨 质疑型"),
                                 ("⚖️ 中立讨论", "⚖️ 中立"), ("❌ 负面批评", "❌ 负面")]:
            samples = [x for x in result["classified"] if x["sentiment"] == sent_key]
            print(f"\n{label}（共{len(samples)}条）:")
            for item in samples[:5]:
                print(f"  · @{item['nick']}: {item['content'][:80]}")

        print(f"\n{'='*60}")
        print(f"👥 活跃用户（评论数超过1条）")
        print(f"{'='*60}")
        if result["active_users"]:
            for nick, cnt in sorted(result["active_users"].items(), key=lambda x: x[1], reverse=True):
                print(f"  · {nick}: {cnt} 条")
        else:
            print("  无用户发多条评论")


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
            "content": content[:100],
            "sentiment": sentiment,
            "likes": likes,
        })
    return results
