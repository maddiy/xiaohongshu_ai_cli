# 小红书评论自动回复工具

帮你自动扫描小红书笔记下的所有评论和楼中楼，找出未回复的，生成回复草稿后批量发送。
内置缓存机制和跳过列表，避免重复拉取和无效操作。

## 功能一览

| 功能 | 说明 |
|------|------|
| `login` | 通过浏览器 Cookie 登录（Firefox 优先，自动尝试 Chrome/Edge/Safari 等） |
| `articles` | 查看最新文章列表 |
| `scan` | 扫描未回复评论（支持全量扫描和通知快速扫描） |
| `drafts` | 生成回复草稿，支持交互式和批量导入两种模式 |
| `send` | 发送已审核的草稿（支持预览、二次确认、断点续发） |
| `reply` | 传统模式：扫描后逐条交互式回复（支持 's' 永久跳过） |
| `skipped` | 管理跳过列表（查看、移除、清空） |
| `analyze` | 评论数据统计分析 |

## 安装

```bash
# 1. 安装小红书 CLI
pip install xiaohongshu-cli

# 2. 下载本项目
cd xhs-auto-reply

# 3. 修改配置
vim config.py   # 填入你的 AUTHOR_USER_ID
```

## 配置

编辑 `config.py`：

```python
AUTHOR_USER_ID = "你的小红书用户ID"   # 运行 xhs whoami 查看
REPLY_STRATEGY = "smart"              # smart(逐条确认) / generic(随机话术)
REQUEST_DELAY = 3                     # 请求间隔（秒）
CACHE_TTL_MINUTES = 30                # 缓存有效期（分钟）
SKIPPED_FILE = ".cache/skipped.json"  # 跳过列表存储路径
```

## 使用方法

### 1. 登录（自动尝试多浏览器 Cookie）

```bash
python3 main.py login
```

### 2. 查看文章列表

```bash
python3 main.py articles
python3 main.py articles --limit 50
```

### 3. 扫描评论

```bash
# 通知快速扫描（推荐）：从最新评论通知中提取新评论，秒级完成
python3 main.py scan --note-id <note_id> --from-notifications

# 全量扫描：拉取全部评论后逐个比对（精确但较慢）
python3 main.py scan --note-id <note_id>

# 同时拉取完整楼中楼
python3 main.py scan --note-id <note_id> --with-subs

# 扫描所有有评论的笔记
python3 main.py scan
python3 main.py scan --max-pages 5
python3 main.py scan --from-notifications   # 通知快速扫描所有笔记
```

### 4. 生成回复草稿（推荐）

**方式一：交互式逐条输入**

```bash
python3 main.py drafts --note-id <note_id>
```

```
[1/28] @用户示例A: 示例评论内容，用户表达了一些观点...
  💬 回复内容 (回车=跳过, 's'=永久跳过): 这是示例回复内容...
  ✅ 草稿

[2/28] @用户示例B: 😂
  💬 回复内容 (回车=跳过, 's'=永久跳过): s
  📁 永久跳过（加入跳过列表）
```

**方式二：从已有扫描结果加载（跳过重扫）**

```bash
python3 main.py scan --note-id <note_id>   # 先扫描
python3 main.py drafts --note-id <note_id> \
  --from-scan /tmp/unreplied_<note_id>.json
```

**方式三：批量导入AI预写的回复（非交互，最快）**

准备映射文件 `reply_map.json`：
```json
{
  "<comment_id_1>": "这是一条示例回复",
  "<comment_id_2>": {"reply": "", "action": "archive"},
  "6a65...": "另一条示例文本"
}
```

```bash
python3 main.py drafts --note-id <note_id> \
  --from-scan /tmp/unreplied_note.json \
  --batch reply_map.json
```

> **说明**：`--from-scan` 加载 scan 结果跳过重复拉取；`--batch` 非交互导入预写回复。
> 两者可单独或组合使用。映射中未找到的评论自动跳过。

生成完毕后保存到 `/tmp/drafts_<note_id>.json`，可手动编辑调整回复内容。

### 5. 发送草稿

```bash
# 预览模式（不实际发送）
python3 main.py send --file /tmp/drafts_note.json --dry-run

# 确认后发送
python3 main.py send --file /tmp/drafts_note.json --confirm

# 直接发送
python3 main.py send --file /tmp/drafts_note.json

# 断点续发（跳过已在跳过列表中的，适用于中断后恢复）
python3 main.py send --file /tmp/drafts_note.json --resume
```

> 在草稿中 action 为 `"archive"` 的评论会自动加入永久跳过列表；
> 发送失败的评论也会自动加入跳过列表，避免下次重复尝试。

### 6. 传统回复模式

```bash
python3 main.py reply --note-id <note_id>

# 交互中:
#   输入回复内容 → 发送
#   回车 → 本次跳过
#   s → 永久跳过（加入跳过列表）

python3 main.py reply --note-id <note_id> --strategy generic
python3 main.py reply --note-id <note_id> --from-file /tmp/unreplied_xxx.json
```

### 7. 管理跳过列表

```bash
python3 main.py skipped                     # 查看所有跳过记录
python3 main.py skipped --remove <comment_id>  # 移除某条记录
python3 main.py skipped --clear              # 清空全部
```

跳过列表存储在 `.cache/skipped.json`，被跳过的评论在后续扫描时自动过滤。

### 8. 分析评论

```bash
python3 main.py analyze --note-id <note_id>
```

## 典型工作流

### 全手动工作流

```bash
python3 main.py login
python3 main.py articles
python3 main.py scan --note-id <note_id>
python3 main.py drafts --note-id <note_id>
python3 main.py send --file /tmp/drafts_note.json --dry-run
python3 main.py send --file /tmp/drafts_note.json --confirm
```

### AI辅助批量工作流（推荐）

```bash
# 1. 通知快速扫描
python3 main.py scan --note-id <note_id> --from-notifications

# 2. 让AI生成所有回复（在CodeBuddy中），保存为 reply_map.json

# 3. 批量导入生成草稿
python3 main.py drafts --note-id <note_id> \
  --from-scan /tmp/unreplied_note.json \
  --batch reply_map.json

# 4. 预览确认后发送
python3 main.py send --file /tmp/drafts_note.json --dry-run
python3 main.py send --file /tmp/drafts_note.json --confirm
```

## 草稿文件格式

```json
{
  "note_id": "<note_id>",
  "note_title": "你的笔记标题",
  "generated_at": "2026-07-26 22:30:00",
  "drafts": [
    {
      "comment_id": "<comment_id_a>",
      "nickname": "用户示例A",
      "content": "示例评论内容...",
      "reply": "这是示例回复内容...",
      "action": "send"
    },
    {
      "comment_id": "<comment_id_b>",
      "nickname": "用户示例B",
      "content": "😂",
      "reply": "",
      "action": "archive"
    }
  ]
}
```

action 取值：
- `"send"` — 发送该回复
- `"skip"` — 本次跳过（下次扫描仍会出现）
- `"archive"` — 永久跳过，加入跳过列表（下次扫描不再出现）

## 项目结构

```
├── main.py              # 统一入口，8个子命令
├── config.py            # 配置文件
├── lib/
│   ├── xhs_client.py    # 小红书 CLI 封装（API + 缓存 + 跳过列表 + 通知）
│   ├── scanner.py       # 评论扫描器（过滤跳过列表）
│   ├── replier.py       # 回复器（draft/send/reply三模式）
│   └── analyzer.py      # 评论分析器
├── .cache/              # 缓存 + 跳过列表（自动生成）
└── legacy/              # 早期脚本（参考）
```

## 性能优化

| 优化项 | 说明 |
|--------|------|
| 通知快速扫描 | `--from-notifications` 只从最新评论通知中提取新评论，无需拉取全部评论对比，**秒级完成** |
| skipped 内存缓存 | `is_skipped` 从每次读文件 (~1ms) 降到 O(1) 内存查找 (~0.01ms)，批量操作提升 100x |
| scanner 延迟优化 | 仅网络请求后 sleep，纯内存遍历不再延迟，扫描 200+ 评论从 ~40s 降到 ~5s |
| xsec_token 索引缓存 | 首次翻页后缓存全量映射到 `.cache/xsec_index.json`，后续 O(1) 命中无需翻页 |
| inline 楼中楼提取 | 内联数据完整时直接从内存提取非作者楼中楼，节省逐条 API 调用 |
| 预编译正则 | 情感/主题分类从多次 `any(kw in content)` 改为单次正则扫描，分析速度提升 3-5x |

## 注意事项

- 登录默认读取 Firefox 浏览器 Cookie，失败后自动尝试 Chrome/Edge/Safari/Brave/Chromium。请确保至少有一个浏览器已登录小红书
- 回复间隔默认 3 秒，可在 `config.py` 中调整
- 频繁调用可能触发验证码，建议分批处理
- 回复失败的评论自动加入跳过列表，避免反复尝试
- **楼中楼限制**：`xhs sub-comments` 不支持 `--xsec-token`，一级评论内联的楼中楼数据已足够判断作者是否已回复
- 跳过列表存储在 `.cache/skipped.json`，可手动编辑
- `send --resume` 可在发送中断后恢复，无需重新开始
- xsec_token 索引自动维护在 `.cache/xsec_index.json`，无需手动管理
