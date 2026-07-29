# 小红书AI智能运营系统：命令与 Python API 参考

命令行程序：`小红书AI智能运营系统`。

所有命令都在项目根目录运行。日常使用优先参考 `README.md`；需要精确参数或直接调用 Python API 时再读取本文档。

AI首次进入项目时优先读取根目录`AGENTS.md`，无需通读本文档。

当前应用版本为 `3.0.0`，共有14个子命令，不提供快捷别名：

```bash
python3 main.py --version
```

## 固定工作目录

每篇笔记使用唯一目录：

```text
.cache/workflows/<笔记ID>/
├── scan.json
├── reply_map.json
└── drafts.json
```

查看路径和文件状态：

```bash
python3 main.py paths --note-id <笔记ID>
```

发布笔记统一使用：

```text
.cache/workflows/post/note.json
```

## `doctor`：检查环境

```bash
python3 main.py doctor
python3 main.py doctor --json
```

检查 Python、`xhs` 命令、作者用户 ID、Cookie 来源和缓存目录。

## `login`：登录账号

```bash
python3 main.py login
```

从 `config.py` 的 `LOGIN_COOKIE_SOURCE` 指定浏览器读取 Cookie。

## `articles`：查看笔记

```bash
python3 main.py articles
python3 main.py articles --limit 10
python3 main.py articles --limit 10 --json
```

`--limit`可省略，默认10。该命令对平台只读，但`get_my_notes`可能更新本地
权限为0600的敏感`.cache/xsec_index.json`。
默认列为 `序号｜发布时间｜评论数｜标题｜笔记 ID`，无标题时显示
“无标题”。`--json` 会返回同样的 `columns` 列名，并且不包含
`xsec_token`。

## `comments`：查看最新评论

```bash
python3 main.py comments
python3 main.py comments --limit 50
python3 main.py comments --note-id <笔记ID>
python3 main.py comments --json
```

`--limit`可省略，默认20。
面向用户固定显示 `序号｜时间｜用户｜评论｜状态`，不显示评论 ID；
JSON 内仍保留 `comment_id`，仅供在线核验和回复定位使用。

完整扫描楼中楼时，程序会自动从本地文章索引取得 `xsec_token`，兼容
`xiaohongshu-cli 0.6.4` 未给楼中楼接口传递文章令牌的问题。

该命令只读通知并按文章分组，结合评论删除状态、`drafts.json` 本地终态和
`.cache/skipped.json` 显示“正常、已回复、发送失败、已跳过、已删除”等状态。
它不会生成回复候选，也不会修改草稿。

## `scan`：扫描未回复评论

`scan` 只用于准备回复，不用于普通查看。通知快速模式会依次过滤已删除、
跳过、本地终态，并在线核验平台评论树：

```bash
python3 main.py scan
python3 main.py scan --note-id <笔记ID>
python3 main.py scan --note-id <笔记ID> --num-notifications 100
python3 main.py scan --note-id <笔记ID> --json
```

全量扫描：

```bash
python3 main.py scan --note-id <笔记ID> --full-scan
python3 main.py scan --note-id <笔记ID> --full-scan --refresh
python3 main.py scan --note-id <笔记ID> --full-scan --with-subs
python3 main.py scan --note-id <笔记ID> --full-scan --xsec-token <令牌>
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--note-id` | 指定单篇笔记 |
| `--xsec-token` | 某些笔记请求所需的内部令牌 |
| `--refresh` | 忽略缓存并重新获取 |
| `--with-subs` | 获取完整楼中楼 |
| `--max-pages` | 扫描全部笔记时限制翻页数 |
| `--full-scan` | 获取全部历史评论 |
| `--num-notifications` | 通知模式读取数量，默认 20 |
| `--output` | 覆盖默认扫描结果路径 |
| `--json` | 终端只输出紧凑 JSON 摘要 |

未指定 `--output` 时，扫描结果自动保存到固定的 `scan.json`。
扫描多篇笔记时，`per_note` 中每篇文章都包含从 1 开始的
`note_index`。面向用户展示评论时，文章分组标题使用
`序号. 标题（笔记 ID）`。

### 扫描候选与最终可回复清单

`scan.json` 的 `unreplied_level1` 和 `unreplied_subs` 只是平台扫描候选。通知数据可能延迟，因此已经成功发送的评论仍可能再次出现。AI 在生成回复前必须以 `comment_id` 为键，同时读取：

- `.cache/workflows/<笔记ID>/scan.json`
- `.cache/workflows/<笔记ID>/drafts.json`
- `.cache/workflows/<笔记ID>/reply_map.json`
- `.cache/skipped.json`

只有同时满足以下条件的评论才允许进入 `reply_map.json`：

```text
reply_status_verified == true
且 comment_id 位于本次扫描候选中
且 drafts.json 不存在相同 comment_id 的 sent、failed、archived 状态
且 comment_id 不在跳过列表
且评论没有被删除
```

状态优先级为：`sent` > `failed` > `archived/跳过` > 平台状态 >
本次扫描候选。这样失败评论即使同时位于跳过列表，仍显示“发送失败”并保留
错误原因；候选列表不得覆盖本地终态。

如果过滤后没有评论，直接结束并报告“没有可回复评论”。不得复用旧 `reply_map.json` 重建草稿。

## `drafts`：生成回复草稿

交互式输入：

```bash
python3 main.py drafts --note-id <笔记ID>
```

批量导入 AI 回复：

```bash
python3 main.py drafts \
  --note-id <笔记ID> \
  --from-scan .cache/workflows/<笔记ID>/scan.json \
  --batch .cache/workflows/<笔记ID>/reply_map.json
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--from-scan` | 读取已有扫描结果，避免重新扫描 |
| `--batch` | 读取评论 ID 到回复文案的 JSON 映射 |
| `--output` | 覆盖默认草稿路径 |
| `--with-subs` | 实时扫描时包含完整楼中楼 |
| `--refresh` | 实时扫描时忽略缓存 |
| `--allow-unverified` | 兼容缺少核验标记的旧扫描文件；生成前仍强制在线核验 |

未指定 `--output` 时，草稿自动保存到固定的 `drafts.json`。
默认要求扫描文件中的 `reply_status_verified` 为 `true`。
此外，`drafts` 在生成草稿前会强制在线读取最新评论树，按作者回复的
`target_comment.id` 再次核验一级评论和楼中楼。即使本地没有发送记录，
平台已经回复的评论也会被排除；在线核验失败时不会生成草稿。

## `send`：预览和发送回复

```bash
python3 main.py send \
  --file .cache/workflows/<笔记ID>/drafts.json \
  --dry-run

python3 main.py send \
  --file .cache/workflows/<笔记ID>/drafts.json

python3 main.py send \
  --file .cache/workflows/<笔记ID>/drafts.json \
  --resume
```

参数说明：

| 参数 | 说明 |
|---|---|
| `--file` | 草稿文件路径，必填 |
| `--dry-run` | 只预览，不发送 |
| `--confirm` | 在终端再次询问确认 |
| `--resume` | 兼容选项，仅改变续发提示；终态和排除过滤始终执行 |

传统 `send` 会按 `active_comment_ids` 限定本批草稿，并在实际发送前强制在线
核验 `action=send` 的评论。草稿缺少 `note_id`、平台分页不完整、楼中楼数据
不完整或核验失败时立即停止；`--dry-run` 只展示，不进行发送阶段核验。
平台明确判定为`online_replied`或`online_missing`的项目会标记为
`archived`并写回草稿文件。

每条发送结果都会立即写回 `drafts.json`：

- `send_status: "sent"`：发送成功，不会重复发送
- `send_status: "failed"`：发送失败，保留错误类型
- `send_status: "archived"`：已经归档

当前实现把 `sent`、`failed` 和 `archived` 都视为自动发送终态。所有
`failed` 评论会自动加入 `.cache/skipped.json`，不会自动重试；需要重试时，
必须先向用户展示 `error_type` 和 `last_error`、取得明确授权，同时用
`skipped --remove <comment_id>` 移出排除列表并重置 `drafts.json` 中对应评论
的 `failed` 终态。只移出排除列表不会自动重置草稿。

## `reply`：兼容旧版的交互回复

该命令保留给旧脚本使用，但不会绕过安全检查：发送前仍会排除本地已处理状态，并从平台在线核验是否已经回复。在线分页、验证码或楼中楼数据不完整时会停止，不会形成回复或发送。

`drafts` 和 `reply` 在没有指定扫描文件时，默认只处理最新20条评论通知。
只有明确需要全部历史评论时才使用 `--full-scan`。

```bash
python3 main.py reply --note-id <笔记ID>
python3 main.py reply --note-id <笔记ID> --strategy generic
python3 main.py reply --from-file <扫描结果.json>
```

`smart` 为逐条输入，`generic` 从通用话术池随机选择。

## `analyze`：分析评论

```bash
python3 main.py analyze --note-id <笔记ID>
python3 main.py analyze --note-id <笔记ID> --refresh
python3 main.py analyze --note-id <笔记ID> --json
python3 main.py analyze --note-id <笔记ID> --json --details
```

`--json` 默认只输出统计摘要、热门评论和活跃用户。只有确实需要全部分类明细时才使用 `--details`。

## `skipped`：管理跳过列表

```bash
python3 main.py skipped
python3 main.py skipped --remove <评论ID>
python3 main.py skipped --clear
```

清空跳过列表会在终端要求确认。

## `post`：发布图文笔记

直接传参：

```bash
python3 main.py post \
  --title "标题" \
  --body "正文内容" \
  --images /绝对路径/图片1.jpg /绝对路径/图片2.jpg \
  --topics "标签1,标签2"
```

使用统一 JSON 文件：

```bash
python3 main.py post \
  --input .cache/workflows/post/note.json \
  --dry-run

python3 main.py post \
  --input .cache/workflows/post/note.json
```

`note.json` 支持 `title`、`body`、`images`、`topics` 和 `private`。

## `ai-help`：获取 AI 调用协议

```bash
python3 main.py ai-help --summary
python3 main.py ai-help --command send
python3 main.py ai-help --tests
python3 main.py ai-help
```

`--summary`只输出名称、命令、默认值、推荐回复流程和最容易误读的权威事实，
适合其他AI首次进入项目；`--command <命令>`从当前`argparse`定义生成该命令
的精确参数、默认值、副作用和输出约定；`--tests`列出当前测试方法，但不能
证明相对旧版本的新增、删除或修改；不带参数时输出完整协议。

## `ai-reply`：AI 专用紧凑回复工作流

```bash
python3 main.py ai-reply --note-id <笔记ID> --action prepare
python3 main.py ai-reply --note-id <笔记ID> --action draft
python3 main.py ai-reply --note-id <笔记ID> --action send --confirmed
```

| 动作 | 输出 | 说明 |
|---|---|---|
| `prepare` | `candidates`、`paths` | 默认扫描最新20条通知中的评论和楼中楼 |
| `draft` | `preview`、`paths` | 读取固定回复映射并再次在线核验 |
| `send` | `results`、发送统计 | 发送前再次核验；必须提供 `--confirmed` |

默认回复映射位置为
`.cache/workflows/<笔记ID>/reply_map.json`。每个动作的标准输出都只有
一个紧凑 JSON 文档，适合 AI 直接解析。

回复映射的顶层必须是以 `comment_id` 为键的 JSON 对象，`action` 只能为
`send`、`skip` 或 `archive`；`action=send` 时 `reply` 必须是非空字符串。
映射错误时 `draft` 返回 `ok=false` 和 `details`，不会生成可发送草稿。
映射值也可直接使用非空字符串，等价于`action=send`；本次候选缺少映射时
默认`skip`。
`skip`和`archive`的`reply`可以为空；只有`action=send`要求非空回复。
`skip`只跳过本批；`archive`在用户确认并执行`send`动作后写入
`.cache/skipped.json`，但不会向平台发送回复。

`prepare` 会停用上一批草稿，`draft` 把当前候选写入
`drafts.json.active_comment_ids`，`send` 只处理该活动批次。历史
`sent`、`failed`、`archived` 记录仍然保留，但不会混入本次发送。
`draft`开始时也会先停用旧活动批次；它保留历史草稿和终态，只在成功后
写入新的`active_comment_ids`。

`prepare` 可使用 `--limit <数量>` 调整最新评论范围。只有明确需要检查
全部历史评论时才使用 `--full-scan`。
全量`prepare`会传入`include_sub_comments=true`和`force_refresh=true`，
绕过评论TTL缓存并拉取完整楼中楼。

楼中楼在线数据不完整时，`prepare`、`draft` 和 `send` 都会停止。这是
避免重复回复的安全策略，不是针对某篇笔记的临时兼容行为。
如果补拉后的楼中楼数量仍少于平台`sub_comment_count`，程序抛出
`RuntimeError`硬停止，不使用部分数据判断作者是否已回复。
核验展开本次在线查询返回的所有不完整楼层，是因为内联数据不足时无法
预先定位候选或作者回复所在楼层；这不表示回复关系可以跨楼层。

需要区分两种结果：在线请求、分页或完整性核验失败时硬停止且不改变候选
终态；平台成功返回并明确判定`online_replied`或`online_missing`时，发送
阶段才把对应候选标记为`archived`。

---

## Python API

### `XHSClient`

```python
from lib.xhs_client import XHSClient

client = XHSClient()

client.login()
client.whoami()
client.list_articles(limit=20)
client.get_notifications(num=20, notification_type="mentions")
client.get_new_comment_notifications(num=20)
client.get_comments_cached(note_id, xsec_token="", force_refresh=False)
client.get_sub_comments(note_id, comment_id)
client.reply(note_id, comment_id, "回复内容")

client.get_skipped_ids()
client.add_skipped(comment_id, nickname, content, reason="manual", note_id=note_id)
client.remove_skipped(comment_id)
```

`reply` 返回：

```python
(ok: bool, error: str, error_type: str)
```

权威清单为`XHSClient.REPLY_ERROR_TYPES`，当前包括 `comment_deleted`、
`rate_limited`、`content_rejected` 和 `unknown_error`；识别条件以
`XHSClient.REPLY_ERROR_MARKERS`为准。

### `CommentScanner`

```python
from lib.scanner import CommentScanner

scanner = CommentScanner()

result = scanner.scan_via_notifications(
    note_id=None,
    verbose=True,
    num_notifications=20,
)

result = scanner.scan_note(
    note_id,
    xsec_token="",
    include_sub_comments=False,
    force_refresh=False,
)

results = scanner.scan_all_notes(
    include_sub_comments=False,
    force_refresh=False,
    max_pages=None,
)
```

### `Replier`

```python
from lib.replier import Replier

replier = Replier()

reply_text = replier.generate_reply(comment, strategy="generic")

drafts = replier.generate_drafts_from_mapping(
    comments,
    reply_map,
    note_id=note_id,
    note_title=note_title,
)

stats = replier.send_drafts(
    drafts,
    state_file=".cache/workflows/<笔记ID>/drafts.json",
)
```

`resume`参数仅为旧调用兼容和提示文案保留；安全的终态与排除过滤始终执行。

### `CommentAnalyzer`

```python
from lib.analyzer import CommentAnalyzer

result = CommentAnalyzer.analyze(
    note_id,
    xsec_token="",
    note_title="",
    force_refresh=False,
)

CommentAnalyzer.print_report(result)
```

### `poster`

```python
from lib.poster import publish

publish(
    title="标题",
    body="正文",
    images=["/绝对路径/图片.jpg"],
    topics=["标签1", "标签2"],
    private=False,
    dry_run=True,
)
```
