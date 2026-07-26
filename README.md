# 小红书命令行助手

一个面向中文用户和 AI 助手的小红书命令行工具。

它负责连接小红书、读取笔记与评论、保存回复草稿以及执行发布；内容创作和回复文案可以由任意 AI 编程助手完成，不依赖特定模型或厂商。

**AI 一句话安装使用：** 在任意支持终端和文件操作的 AI 编程助手中打开本项目，然后说“安装并检查本项目，使用浏览器 Cookie 登录小红书，按统一工作目录读取笔记和评论、生成并预览回复或笔记，获得我的确认后再发送或发布”即可。

## 可以做什么

| 功能 | 命令 | 说明 |
|---|---|---|
| 环境检查 | `doctor` | 检查 Python、`xhs`、账号和浏览器配置 |
| 登录 | `login` | 从指定浏览器读取小红书 Cookie |
| 查看笔记 | `articles` | 查看账号最近发布的笔记 |
| 扫描评论 | `scan` | 查找尚未回复的评论 |
| 生成草稿 | `drafts` | 交互输入或批量导入 AI 回复 |
| 发送回复 | `send` | 预览并发送审核后的草稿 |
| 直接回复 | `reply` | 传统的逐条交互回复模式 |
| 评论分析 | `analyze` | 统计评论、互动和粗略情感倾向 |
| 跳过列表 | `skipped` | 管理不再处理的评论 |
| 发布笔记 | `post` | 发布 AI 或人工准备好的图文笔记 |
| AI 协议 | `ai-help` | 输出机器可读的标准调用流程 |

## 设计目标

- **中文优先**：命令帮助、提示、错误信息和文档以中文为主。
- **AI 无关**：Cursor、Codex、Claude Code、Copilot、CodeBuddy 等都能通过文件和命令调用。
- **人机分工**：AI 负责理解、创作和生成回复，CLI 负责读取、校验、缓存和执行。
- **先审后发**：回复和笔记都支持预览，默认工作流要求用户确认后再执行。
- **先查后写**：生成回复前先核验评论是否已经回复，避免重复生成和发送。
- **机器可读**：关键查询支持 UTF-8 JSON，AI 不需要解析终端表格。
- **可恢复**：使用缓存、跳过列表和断点续发，减少重复请求和重复回复。

## AI 展示约定

所有 AI 助手向用户展示以下内容时，必须使用 Markdown 表格：

| 内容 | 固定列 |
|---|---|
| 笔记列表 | **序号、发布时间、评论数、标题、笔记 ID** |
| 评论列表 | **序号、时间、用户、评论、状态** |
| 回复草稿 | 序号、用户、原评论、拟回复、操作 |
| 发送结果 | 序号、用户、回复摘要、结果、失败原因 |

补充规则：

- 即使只有一条记录，也使用表格。
- 长内容可以截断，但必须用省略号标明。
- 无标题统一显示“无标题”。
- 已删除、已跳过、已回复和发送失败必须明确标注。
- 评论列表默认固定使用 `序号、时间、用户、评论、状态`，并保持这一顺序。
- 除非用户明确要求，否则所有 AI 不得增删、改名或调整评论列表的列。
- 笔记列表默认固定使用 `序号、发布时间、评论数、标题、笔记 ID`，并保持这一顺序。
- 除非用户明确要求，否则所有 AI 不得增删、改名或调整笔记列表的列。
- 表格用于向用户展示；AI 与 CLI 之间仍使用 JSON。
- 不得在表格中显示 Cookie、`xsec_token` 或其他账号凭据。
- 多篇笔记的评论必须按笔记分组，每篇笔记分别显示一张表。
- 分组标题使用 `笔记标题（笔记 ID）`；无标题时使用 `无标题（笔记 ID）`。
- 笔记标题和笔记 ID 只显示在分组标题中，不在评论表内重复展示。

## 统一工作目录

为了让 Codex、Cursor、Claude Code、Copilot、CodeBuddy 等不同 AI 可以接续同一任务，所有临时工作文件统一保存在：

```text
.cache/workflows/
├── <笔记ID>/
│   ├── scan.json
│   ├── reply_map.json
│   └── drafts.json
└── post/
    └── note.json
```

文件用途：

| 文件 | 用途 |
|---|---|
| `scan.json` | 最近一次评论扫描结果 |
| `reply_map.json` | AI 生成并可继续修改的回复映射 |
| `drafts.json` | 已组装、待预览或待发送的回复草稿 |
| `post/note.json` | 待预览或待发布的笔记 |

查询某篇笔记的固定路径和文件状态：

```bash
python3 main.py paths --note-id <笔记ID>
```

所有 AI 必须遵守：

- 文件存在时先读取并复用，不重复扫描或重复存档。
- 同一笔记不创建带日期、时间或批次号的副本。
- 只有用户明确要求保留多个版本时才创建额外文件。
- `.cache/` 已被 Git 忽略，不会提交账号工作数据。
- 每条发送结果会立即写回 `drafts.json`；其他 AI 读取 `send_status` 后不会重复发送。

## 安装

### 1. 安装 Python

需要 Python 3.10 或更高版本：

```bash
python3 --version
```

### 2. 安装小红书 CLI

推荐使用 `uv` 隔离安装：

```bash
uv tool install xiaohongshu-cli
```

也可以使用 `pip`：

```bash
python3 -m pip install xiaohongshu-cli
```

确认安装成功：

```bash
xhs --version
```

### 3. 进入项目目录

```bash
cd /path/to/xhs-auto-reply
```

### 4. 检查环境

```bash
python3 main.py doctor
```

AI 或脚本可使用：

```bash
python3 main.py doctor --json
```

## 配置

编辑 `config.py`：

```python
AUTHOR_USER_ID = "你的小红书用户ID"
LOGIN_COOKIE_SOURCE = "firefox"
REQUEST_DELAY = 3
CACHE_TTL_MINUTES = 30
```

主要配置：

| 配置项 | 作用 |
|---|---|
| `AUTHOR_USER_ID` | 用于判断评论是否由笔记作者回复 |
| `LOGIN_COOKIE_SOURCE` | 登录时读取 Cookie 的浏览器 |
| `REQUEST_DELAY` | 连续请求或回复之间的间隔秒数 |
| `CACHE_TTL_MINUTES` | 评论缓存有效时间 |
| `GENERIC_REPLIES` | 通用回复模式使用的话术 |
| `SKIPPED_FILE` | 永久跳过列表的保存位置 |

当前登录支持的浏览器来源由 `xiaohongshu-cli` 决定，例如 `firefox`、`chrome`、`edge` 和 `safari`。

## 快速开始

### 1. 登录

先在配置的浏览器中登录小红书网页版，然后运行：

```bash
python3 main.py login
```

查看当前账号：

```bash
xhs whoami
```

将返回的用户 ID 写入 `config.py` 的 `AUTHOR_USER_ID`。

### 2. 查看最近笔记

```bash
python3 main.py articles
python3 main.py articles --limit 20
```

机器可读输出：

```bash
python3 main.py articles --limit 20 --json
```

### 3. 扫描最新评论

默认模式从评论通知开始，适合日常处理：

```bash
python3 main.py scan
python3 main.py scan --note-id <笔记ID>
```

推荐 AI 工作流使用固定输出文件：

```bash
python3 main.py scan \
  --note-id <笔记ID> \
  --json
```

结果默认保存到 `.cache/workflows/<笔记ID>/scan.json`。

### 4. 生成回复草稿

人工逐条输入：

```bash
python3 main.py drafts --note-id <笔记ID>
```

使用已有扫描结果，避免重新扫描：

```bash
python3 main.py drafts \
  --note-id <笔记ID> \
  --from-scan .cache/workflows/<笔记ID>/scan.json
```

让 AI 生成 `reply_map.json` 后批量导入：

```bash
python3 main.py drafts \
  --note-id <笔记ID> \
  --from-scan .cache/workflows/<笔记ID>/scan.json \
  --batch .cache/workflows/<笔记ID>/reply_map.json
```

### 5. 预览并发送

先预览，不会发送：

```bash
python3 main.py send --file .cache/workflows/<笔记ID>/drafts.json --dry-run
```

确认内容无误后发送：

```bash
python3 main.py send --file .cache/workflows/<笔记ID>/drafts.json
```

如果发送中断：

```bash
python3 main.py send --file .cache/workflows/<笔记ID>/drafts.json --resume
```

## AI 标准工作流

任意 AI 助手都可以遵循以下流程：

```bash
# 1. 检查环境
python3 main.py doctor --json

# 2. 查看笔记并取得 note_id
python3 main.py articles --limit 20 --json

# 3. 扫描评论
python3 main.py scan --note-id <note_id> --json

# 4. AI 读取固定 scan.json，并更新同目录 reply_map.json

# 5. 生成可审核草稿
python3 main.py drafts \
  --note-id <note_id> \
  --from-scan .cache/workflows/<note_id>/scan.json \
  --batch .cache/workflows/<note_id>/reply_map.json

# 6. 预览
python3 main.py send --file .cache/workflows/<note_id>/drafts.json --dry-run

# 7. 获得用户确认后发送
python3 main.py send --file .cache/workflows/<note_id>/drafts.json
```

查看完整的机器调用协议：

```bash
python3 main.py ai-help
```

## 扫描模式

### 通知快速扫描

`scan` 默认读取最新评论通知，然后核对回复状态。适合日常处理，扫描范围受通知数量限制：

```bash
python3 main.py scan --num-notifications 50
```

### 全量扫描

只有需要检查一篇笔记的全部历史评论时才使用：

```bash
python3 main.py scan \
  --note-id <笔记ID> \
  --full-scan
```

同时检查完整楼中楼：

```bash
python3 main.py scan \
  --note-id <笔记ID> \
  --full-scan \
  --with-subs
```

忽略缓存重新获取：

```bash
python3 main.py scan \
  --note-id <笔记ID> \
  --full-scan \
  --refresh
```

全量扫描评论较多的笔记可能需要较长时间，也更容易触发平台验证。

## 数据格式

### 扫描结果

```json
{
  "note_id": "<note_id>",
  "note_title": "笔记标题",
  "source": "notifications",
  "unreplied_level1": [
    {
      "comment_id": "<comment_id>",
      "nickname": "评论用户",
      "content": "评论内容"
    }
  ],
  "unreplied_subs": []
}
```

扫描输出会自动移除 `xsec_token`、原始楼中楼对象等内部字段，只保留生成回复所需的数据。

使用 `--json` 时，完整结果写入固定的 `scan.json`，终端只返回文件路径和数量摘要，避免同一内容消耗两次 AI 上下文。

扫描结果包含：

```json
{"reply_status_verified": true}
```

`drafts` 默认只接受已经核验回复状态的扫描文件。核验失败时应重新扫描，不要直接生成回复；只有用户明确接受重复回复风险时才使用 `--allow-unverified`。

### AI 回复映射

最简单的格式：

```json
{
  "<comment_id_1>": "第一条回复",
  "<comment_id_2>": "第二条回复"
}
```

需要控制操作时：

```json
{
  "<comment_id_1>": {
    "reply": "准备发送的回复",
    "action": "send"
  },
  "<comment_id_2>": {
    "reply": "",
    "action": "skip"
  },
  "<comment_id_3>": {
    "reply": "",
    "action": "archive"
  }
}
```

`action` 的含义：

- `send`：发送回复
- `skip`：本次跳过，下次扫描仍可能出现
- `archive`：永久跳过，写入 `.cache/skipped.json`

### 回复草稿

```json
{
  "note_id": "<note_id>",
  "note_title": "笔记标题",
  "generated_at": "2026-07-26 22:30:00",
  "drafts": [
    {
      "comment_id": "<comment_id>",
      "nickname": "评论用户",
      "content": "原评论",
      "reply": "准备发送的回复",
      "action": "send"
    }
  ]
}
```

## 发布笔记

小红书图文笔记至少需要一张本地图片。

直接使用命令参数：

```bash
python3 main.py post \
  --title "笔记标题" \
  --body "笔记正文" \
  --images /绝对路径/封面.jpg \
  --topics "读书,成长"
```

AI 推荐使用 `.cache/workflows/post/note.json`：

```json
{
  "title": "简洁标题",
  "body": "笔记正文",
  "images": ["/绝对路径/封面.jpg"],
  "topics": ["读书", "成长"],
  "private": false
}
```

先预览：

```bash
python3 main.py post --input .cache/workflows/post/note.json --dry-run
```

用户确认后发布：

```bash
python3 main.py post --input .cache/workflows/post/note.json
```

使用 `private: true` 或命令参数 `--private` 可设为仅自己可见。

## 评论分析

```bash
python3 main.py analyze --note-id <笔记ID>
python3 main.py analyze --note-id <笔记ID> --json
```

分析内容包括评论数量、回复情况、点赞、活跃用户和基于关键词的粗略情感分类。

情感分类只适合辅助浏览，不应作为对用户人格、立场或心理状态的可靠判断。

## 管理跳过列表

查看：

```bash
python3 main.py skipped
```

恢复某条评论：

```bash
python3 main.py skipped --remove <comment_id>
```

清空：

```bash
python3 main.py skipped --clear
```

清空操作会要求确认。

## 错误处理

| 错误类型 | 建议处理 |
|---|---|
| `comment_deleted` | 评论已删除，不再重试 |
| `rate_limited` | 停止或延迟发送，避免连续请求 |
| `content_rejected` | 修改措辞，重新预览后再发送 |
| `unknown_error` | 保存错误信息，检查登录和平台状态 |

如果出现验证码或平台验证，应停止自动操作，由用户亲自完成验证。

## 安全建议

- 不要提交或公开 Cookie、`xsec_token` 和账号凭据。
- 不要在未经审核的情况下批量发送 AI 生成内容。
- 发布和回复前始终使用 `--dry-run`。
- 对争议或攻击性评论保持克制，优先讨论事实和逻辑。
- 不要使用过短的请求间隔规避平台限制。
- 大批量操作应拆分执行，并检查每批结果。

## 减少 AI Token 使用

- 查询命令的 JSON 使用紧凑编码，不输出格式化空白。
- `articles --json` 不输出 `xsec_token`。
- `scan --json` 只在终端返回摘要，评论正文保存在固定工作文件中。
- 扫描文件移除 `inline_subs` 等内部重复结构。
- `analyze --json` 默认只输出统计、热门评论和活跃用户。
- 只有确实需要全部分析明细时才使用 `analyze --json --details`。
- AI 应复用固定的 `scan.json`、`reply_map.json` 和 `drafts.json`，不要重复扫描、存档或粘贴完整 JSON。

## 项目结构

```text
.
├── main.py                 # 中文命令行入口
├── config.py               # 账号与请求配置
├── SKILL.md                # AI 助手执行规则
├── lib/
│   ├── xhs_client.py       # xhs 命令封装、缓存和跳过列表
│   ├── scanner.py          # 评论扫描与回复状态判断
│   ├── replier.py          # 草稿生成和回复发送
│   ├── analyzer.py         # 评论统计与情感分类
│   └── poster.py           # 图文笔记校验与发布
├── references/
│   └── commands.md         # 详细命令和 Python API
└── .cache/                 # 自动生成的缓存与工作文件
```

## 命令帮助

```bash
python3 main.py --help
python3 main.py scan --help
python3 main.py drafts --help
python3 main.py send --help
python3 main.py post --help
```

更详细的命令参考见 [`references/commands.md`](references/commands.md)。
