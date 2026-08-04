"""手动新评论监控命令。"""

from collections import defaultdict

from .cli_comment_view import (
    COMMENT_DISPLAY_RULES,
    build_comment_display_groups,
)
from .cli_support import print_json
from .comment_watcher import (
    CommentWatchError,
    CommentWatcher,
    public_watch_item,
)
from .state_io import StateLockTimeout


WATCH_COLUMNS = ["序号", "时间", "用户", "评论", "状态"]


def watch_event_payload(event: dict) -> dict:
    """把内部监控事件转换为不含评论ID、用户ID和令牌的界面协议。"""
    public_items = [
        public_watch_item(item) for item in event.get("items", [])
    ]
    grouped = defaultdict(list)
    note_titles = {}
    for item in public_items:
        note_id = item.get("note_id", "")
        grouped[note_id].append({
            "time": item.get("time", ""),
            "nickname": item.get("nickname", "?"),
            "content": item.get("content", ""),
            "status": item.get("status", ""),
        })
        note_titles[note_id] = item.get("note_title", "") or "无标题"
    raw_groups = [{
        "note_index": index,
        "note_id": note_id,
        "note_title": note_titles.get(note_id, "无标题"),
        "comments": comments,
    } for index, (note_id, comments) in enumerate(grouped.items(), start=1)]
    payload = {
        key: value for key, value in event.items() if key != "items"
    }
    payload.update({
        "columns": WATCH_COLUMNS,
        "groups": build_comment_display_groups(raw_groups),
        "items": public_items,
        "display": COMMENT_DISPLAY_RULES,
    })
    return payload


def _print_watch_text(event: dict) -> None:
    event_name = event.get("event", "")
    if event_name == "baseline":
        print(
            f"✅ 已建立基线（{event.get('baseline_count', 0)}条），"
            "本次没有处理历史通知"
        )
        return
    if event_name == "heartbeat":
        print(f"{event.get('polled_at', '')}｜暂无新评论")
        return
    print(
        f"\n{event.get('polled_at', '')}｜发现"
        f"{event.get('detected_count', 0)}条新评论"
    )
    for item in event.get("items", []):
        print(
            f"\n[{item.get('note_title', '无标题')}｜"
            f"{item.get('note_id', '')}] @{item.get('nickname', '?')}"
        )
        print(item.get("content", ""))
        print(f"状态：{item.get('status', '')}")
        if item.get("reply"):
            print(f"回复：{item['reply']}")


def cmd_watch(args) -> None:
    """前台监控；按Ctrl+C停止，不创建系统后台服务。"""
    try:
        if getattr(args, "status", False):
            watcher = CommentWatcher(
                note_id=args.note_id or "",
                user=args.user or "",
                interval=args.interval,
                limit=args.limit,
            )
            payload = watcher.status()
            print_json(payload) if args.json else print(
                f"监控检查点：{'存在' if payload['checkpoint_exists'] else '不存在'}"
                f"｜已见{payload['seen_count']}条｜当前未运行"
            )
            return
        watcher = CommentWatcher(
            note_id=args.note_id or "",
            user=args.user or "",
            auto_reply=args.auto_reply,
            confirmed=args.confirmed,
            reply_text=args.reply_text or "",
            interval=args.interval,
            limit=args.limit,
        )
        if not args.json:
            print(
                "🔎 新评论监控已手动开启｜"
                f"模式={watcher.mode}｜间隔={watcher.interval:g}秒｜"
                f"自动回复={'开启' if watcher.auto_reply else '关闭'}"
            )
            print("按 Ctrl+C 停止；程序退出后不会在后台继续运行。")

        def on_event(event):
            payload = watch_event_payload(event)
            if args.json:
                print_json(payload)
            else:
                _print_watch_text(payload)

        watcher.run(
            once=args.once,
            on_event=on_event,
            reset=args.reset,
        )
    except KeyboardInterrupt:
        payload = {
            "ok": True,
            "event": "stopped",
            "active": False,
            "message": "监控已由用户手动停止",
        }
        print_json(payload) if args.json else print("\n⏹️ 监控已停止")
    except StateLockTimeout as error:
        payload = {
            "ok": False,
            "error_type": "watch_already_running",
            "error": str(error),
            "automatic_retry": False,
        }
        print_json(payload) if args.json else print(
            "❌ 相同过滤条件的监控已经在运行"
        )
    except (CommentWatchError, RuntimeError, OSError) as error:
        payload = {
            "ok": False,
            "error_type": "watch_error",
            "error": str(error),
            "automatic_retry": False,
        }
        print_json(payload) if args.json else print(f"❌ 监控停止: {error}")
