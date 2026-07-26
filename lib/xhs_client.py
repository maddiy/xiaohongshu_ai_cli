"""
小红书 CLI 统一封装层
所有对 xhs 命令的调用集中在此，便于维护和升级
"""
import json
import os
import subprocess
import sys
import time
from typing import Optional

from config import CACHE_DIR, CACHE_TTL_MINUTES


class XHSClient:
    """封装 xiaohongshu-cli 的所有功能"""

    # 内存缓存：避免批量操作中反复读取 skipped.json
    _skipped_cache: Optional[dict] = None
    _skipped_mtime: float = 0.0  # 文件修改时间，用于自动刷新

    @staticmethod
    def _run_xhs(cmd: list, timeout: int = 30) -> dict:
        """执行 xhs CLI 命令并检查返回码，返回解析后的 JSON"""
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()[:200]
            raise RuntimeError(f"xhs 命令失败 (exit={result.returncode}): {stderr}")
        data = json.loads(result.stdout)
        if not data.get("ok"):
            return data  # 非 ok 但命令本身成功（如无更多数据）
        return data

    # ---------- 账户 ----------
    # 浏览器尝试顺序：Firefox 优先，然后依次尝试其他主流浏览器
    _BROWSER_SOURCES = [
        "firefox",
        "chrome",
        "edge",
        "safari",
        "brave",
        "chromium",
    ]

    @staticmethod
    def login():
        """依次尝试从已安装的浏览器 cookies 登录，Firefox 优先"""
        for browser in XHSClient._BROWSER_SOURCES:
            print(f"🔑 尝试 {browser} 浏览器 cookies 登录...")
            result = subprocess.run(
                ["xhs", "login", "--cookie-source", browser],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout + result.stderr
            if "登录成功" in output or "ok" in result.stdout.lower():
                print(f"  ✅ {browser} 登录成功")
                return True
            print(f"  ❌ {browser} 失败: {result.stderr.strip() or result.stdout.strip()[:80]}")
        print("  💡 所有浏览器均登录失败，请确保已安装并用小红书登录过至少一个浏览器")
        return False

    @staticmethod
    def whoami():
        """获取当前登录账户信息"""
        result = subprocess.run(
            ["xhs", "whoami"],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()

    # ---------- 笔记 ----------
    @staticmethod
    def get_my_notes(max_pages: int = None):
        """
        获取我的笔记列表（自动翻页直到取完或达到 max_pages 页）
        返回 [{id, title, comments_count, xsec_token, time}]
        参数:
            max_pages: 最多翻多少页（None 表示取完所有）
        """
        from config import REQUEST_DELAY

        all_notes = []
        page = 0
        while True:
            if max_pages is not None and page >= max_pages:
                break
            try:
                data = XHSClient._run_xhs(
                    ["xhs", "my-notes", "--page", str(page), "--json"])
            except RuntimeError:
                break
            if not data.get("ok"):
                break
            notes = data["data"]["notes"]
            if not notes:
                break

            for n in notes:
                all_notes.append({
                    "id": n["id"],
                    "title": n.get("display_title", ""),
                    "comments_count": int(n.get("comments_count", 0) or 0),
                    "xsec_token": n.get("xsec_token", ""),
                    "time": n.get("time", ""),
                })
            page += 1
            time.sleep(REQUEST_DELAY * 0.3)  # 翻页间隔

        return all_notes

    @staticmethod
    def list_articles(limit: int = 20):
        """
        获取最新文章列表（格式化输出用）
        返回 [{id, title, comments_count, time, xsec_token}]
        """
        # 计算需要翻多少页（每页10条）
        pages_needed = (limit + 9) // 10
        notes = XHSClient.get_my_notes(max_pages=pages_needed)
        return notes[:limit]

    @staticmethod
    def find_note_xsec(note_id, max_pages: int = None):
        """
        通过翻页查找指定笔记的 xsec_token（带本地索引缓存，O(1) 命中时无需翻页）

        参数:
            max_pages: 最多翻多少页（None 表示翻到底）
        """
        from config import AUTHOR_USER_ID, REQUEST_DELAY

        # 先查本地索引缓存
        cache = XHSClient._load_xsec_index()
        if note_id in cache:
            return cache[note_id]

        # 缓存未命中，翻页查找
        page = 0
        cursor = None
        new_entries = {}  # 本次发现的新条目，统一写入索引
        while True:
            if max_pages is not None and page >= max_pages:
                break
            page += 1
            cmd = ["xhs", "user-posts", AUTHOR_USER_ID, "--json"]
            if cursor:
                cmd += ["--cursor", cursor]
            try:
                data = XHSClient._run_xhs(cmd)
            except RuntimeError:
                break
            if not data.get("ok"):
                break
            notes = data.get("data", {}).get("notes", [])
            cursor = data.get("data", {}).get("cursor", "")
            for n in notes:
                nid = n.get("id", "")
                xtoken = n.get("xsec_token", "")
                if nid:
                    new_entries[nid] = xtoken
                if nid == note_id:
                    # 找到后仍继续记录本页其他条目，减少后续查找开销
                    pass
            if not cursor or not notes:
                break
            time.sleep(REQUEST_DELAY * 0.2)

        # 写入索引缓存
        if new_entries:
            XHSClient._merge_xsec_index(new_entries)

        if note_id in new_entries:
            return new_entries[note_id]

        raise RuntimeError(f"在已发布的笔记中未找到 {note_id}（已翻 {page} 页）")

    @staticmethod
    def _xsec_index_path() -> str:
        from config import CACHE_DIR
        return os.path.join(CACHE_DIR, "xsec_index.json")

    @staticmethod
    def _load_xsec_index() -> dict:
        """加载 xsec_token 本地索引"""
        path = XHSClient._xsec_index_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    @staticmethod
    def _merge_xsec_index(new_entries: dict):
        """合并新条目到本地索引"""
        path = XHSClient._xsec_index_path()
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        existing.update(new_entries)
        XHSClient._ensure_cache_dir()
        with open(path, "w") as f:
            json.dump(existing, f, ensure_ascii=False)

    # ---------- 通知 ----------
    @staticmethod
    def get_notifications(num: int = 50, notification_type: str = "mentions") -> list:
        """
        获取最新通知列表

        参数:
            num: 获取数量（默认50）
            notification_type: 通知类型 "mentions"（评论和@）、"likes"（赞和收藏）、"connections"（新增关注）

        返回: 通知条目列表，每条包含 type, title, user_info, item_info, comment_info 等
        """
        try:
            data = XHSClient._run_xhs(
                ["xhs", "notifications", "--type", notification_type,
                 "--num", str(num), "--json"])
        except RuntimeError:
            return []
        if not data.get("ok"):
            return []
        return data.get("data", {}).get("message_list", [])

    @staticmethod
    def get_new_comment_notifications(num: int = 50) -> list:
        """
        从最新通知中提取"评论了你的笔记"类型的通知，按笔记聚合

        返回: [
            {
                "note_id": str,
                "note_title": str,
                "note_xsec_token": str,
                "new_comments": [
                    {
                        "comment_id": str,
                        "nickname": str,
                        "content": str,
                        "time": int,       # unix timestamp
                    }
                ]
            }
        ]
        """
        notifications = XHSClient.get_notifications(num=num, notification_type="mentions")
        if not notifications:
            return []

        # 按 note_id 聚合
        by_note = {}  # note_id -> {note_title, note_xsec_token, new_comments: []}
        for n in notifications:
            ntype = n.get("type", "")
            # 只处理"评论了你的笔记"类型的通知
            if "comment" not in ntype and "item" not in ntype:
                continue

            item_info = n.get("item_info", {})
            note_id = item_info.get("id", "")
            if not note_id:
                continue

            comment_info = n.get("comment_info", {})
            comment_content = comment_info.get("content", "")

            # 从 link 中提取 anchorCommentId
            link = item_info.get("link", "")
            comment_id = ""
            if "anchorCommentId=" in link:
                comment_id = link.split("anchorCommentId=")[-1].split("&")[0]

            if note_id not in by_note:
                by_note[note_id] = {
                    "note_id": note_id,
                    "note_title": item_info.get("content", ""),
                    "note_xsec_token": item_info.get("xsec_token", ""),
                    "new_comments": [],
                }

            by_note[note_id]["new_comments"].append({
                "comment_id": comment_id,
                "nickname": n.get("user_info", {}).get("nickname", "?"),
                "content": comment_content,
                "time": n.get("time", 0),
            })

        return list(by_note.values())

    # ---------- 评论 ----------
    @staticmethod
    def get_all_comments(note_id, xsec_token=""):
        """获取笔记的全部一级评论（自动翻页）"""
        cmd = ["xhs", "comments", note_id, "--all", "--json"]
        if xsec_token:
            cmd += ["--xsec-token", xsec_token]
        try:
            data = XHSClient._run_xhs(cmd, timeout=120)
        except RuntimeError as e:
            raise RuntimeError(f"获取评论失败: {e}")
        if not data.get("ok"):
            err = data.get("error", {}).get("message", str(data))
            raise RuntimeError(f"获取评论失败: {err}")
        return data["data"]["comments"]

    @staticmethod
    def get_sub_comments(note_id, comment_id):
        """
        获取某条评论下的所有楼中楼（自动翻页，容错返回）

        注意: xhs sub-comments 不支持 --xsec-token 参数，
        对需要 xsec_token 的笔记可能获取不全。
        一级评论内联的 sub_comments 数据更可靠，优先使用。
        """
        from config import REQUEST_DELAY
        all_subs = []
        cursor = None
        page = 0
        while True:
            cmd = ["xhs", "sub-comments", note_id, comment_id, "--json"]
            if cursor:
                cmd += ["--cursor", cursor]
            try:
                data = XHSClient._run_xhs(cmd)
            except (RuntimeError, json.JSONDecodeError):
                # 命令失败或 stdout 为空（参数错误等）
                break
            if not data.get("ok"):
                err_code = data.get("error", {}).get("code", "")
                if "verification" in err_code:
                    print(f"    ⚠️ 楼中楼需要验证，返回已获取的数据")
                break
            page_comments = data.get("data", {}).get("comments", [])
            if page_comments:
                all_subs.extend(page_comments)
            cursor = data.get("data", {}).get("cursor", "")
            page += 1
            if not cursor or not page_comments:
                break
            time.sleep(REQUEST_DELAY * 0.3)
        return all_subs

    @staticmethod
    def reply(note_id, comment_id, content):
        """回复评论"""
        result = subprocess.run(
            ["xhs", "reply", note_id, "--comment-id", comment_id, "-c", content],
            capture_output=True, text=True, timeout=20
        )
        output = result.stdout + result.stderr
        if "ok: true" in output or '"ok": true' in output:
            return True, ""
        # 解析错误码
        try:
            err_data = json.loads(result.stdout)
            err_msg = err_data.get("error", {}).get("message", "unknown")
            return False, f"[{err_data.get('error',{}).get('code','?')}] {err_msg}"
        except Exception:
            return False, output[:200]

    # ---------- 缓存 ----------
    @staticmethod
    def _cache_path(note_id: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(CACHE_DIR, f"{note_id}.json")

    @staticmethod
    def _ensure_cache_dir():
        """确保缓存目录存在"""
        os.makedirs(CACHE_DIR, exist_ok=True)

    @staticmethod
    def load_cache(note_id: str, max_age_minutes: int = None) -> list | None:
        """
        加载缓存的评论数据，如果缓存有效（未过期）则返回评论列表，否则返回 None

        参数:
            note_id: 笔记ID
            max_age_minutes: 最大缓存年龄（分钟），默认使用 CACHE_TTL_MINUTES
        """
        if max_age_minutes is None:
            max_age_minutes = CACHE_TTL_MINUTES

        cache_path = XHSClient._cache_path(note_id)
        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)

            age = time.time() - cached.get("timestamp", 0)
            if age > max_age_minutes * 60:
                print(f"  ⏰ 缓存已过期（{age/60:.1f}分钟），重新拉取...")
                return None

            print(f"  📦 使用缓存（{age/60:.1f}分钟前）")
            return cached.get("comments", [])

        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def save_cache(note_id: str, comments: list):
        """保存评论到缓存"""
        XHSClient._ensure_cache_dir()
        cache_path = XHSClient._cache_path(note_id)
        with open(cache_path, "w") as f:
            json.dump({
                "timestamp": time.time(),
                "comments": comments,
            }, f, ensure_ascii=False)
        print(f"  💾 评论已缓存（{len(comments)}条）")

    @staticmethod
    def get_comments_cached(note_id: str, xsec_token: str = "",
                            force_refresh: bool = False,
                            max_age_minutes: int = None) -> tuple[list, bool]:
        """
        获取评论（优先使用缓存）

        返回: (comments_list, from_cache: bool)
        """
        if not force_refresh:
            cached = XHSClient.load_cache(note_id, max_age_minutes)
            if cached is not None:
                return cached, True

        comments = XHSClient.get_all_comments(note_id, xsec_token)
        XHSClient.save_cache(note_id, comments)
        return comments, False

    @staticmethod
    def invalidate_cache(note_id: str):
        """清除指定笔记的缓存"""
        cache_path = XHSClient._cache_path(note_id)
        if os.path.exists(cache_path):
            os.remove(cache_path)
            return True
        return False

    # ---------- 跳过列表 ----------
    @staticmethod
    def _skipped_path() -> str:
        from config import SKIPPED_FILE
        return SKIPPED_FILE

    @staticmethod
    def load_skipped(force_reload: bool = False) -> dict:
        """
        加载跳过列表（带内存缓存，文件 mtime 变化时自动刷新）

        返回: {comment_id: {nickname, content, reason, skipped_at, note_id}}
        """
        path = XHSClient._skipped_path()
        if not os.path.exists(path):
            XHSClient._skipped_cache = {}
            XHSClient._skipped_mtime = 0
            return {}

        # 检查文件是否被外部修改过
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return {}

        if (not force_reload and XHSClient._skipped_cache is not None
                and mtime <= XHSClient._skipped_mtime):
            return XHSClient._skipped_cache

        try:
            with open(path, "r") as f:
                XHSClient._skipped_cache = json.load(f)
                XHSClient._skipped_mtime = mtime
                return XHSClient._skipped_cache
        except (json.JSONDecodeError, IOError):
            XHSClient._skipped_cache = {}
            XHSClient._skipped_mtime = 0
            return {}

    @staticmethod
    def _write_skipped():
        """将内存缓存写回磁盘并记录 mtime"""
        if XHSClient._skipped_cache is None:
            return
        XHSClient._ensure_cache_dir()
        path = XHSClient._skipped_path()
        with open(path, "w") as f:
            json.dump(XHSClient._skipped_cache, f, ensure_ascii=False, indent=2)
        XHSClient._skipped_mtime = os.path.getmtime(path)

    @staticmethod
    def save_skipped(skipped: dict):
        """保存跳过列表（直接写入磁盘，用于外部导入场景）"""
        XHSClient._skipped_cache = skipped
        XHSClient._write_skipped()

    @staticmethod
    def add_skipped(comment_id: str, nickname: str = "", content: str = "",
                    reason: str = "manual", note_id: str = ""):
        """将一条评论加入跳过列表（先更新内存缓存，再写盘）"""
        skipped = XHSClient.load_skipped()  # 确保缓存已加载
        skipped[comment_id] = {
            "nickname": nickname,
            "content": content[:80],
            "reason": reason,
            "skipped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note_id": note_id,
        }
        XHSClient._write_skipped()

    @staticmethod
    def remove_skipped(comment_id: str) -> bool:
        """从跳过列表中移除"""
        skipped = XHSClient.load_skipped()
        if comment_id in skipped:
            del skipped[comment_id]
            XHSClient._write_skipped()
            return True
        return False

    @staticmethod
    def is_skipped(comment_id: str) -> bool:
        """检查评论是否在跳过列表中（O(1) 内存查找）"""
        return comment_id in XHSClient.load_skipped()

    @staticmethod
    def get_skipped_ids() -> set:
        """获取所有跳过的评论ID集合"""
        return set(XHSClient.load_skipped().keys())
