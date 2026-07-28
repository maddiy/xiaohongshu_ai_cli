---
name: xhs-auto-reply
description: >
  通过本项目的中文命令行工具管理小红书账号，包括浏览器 Cookie 登录、查看笔记、
  扫描最新评论或全部评论、生成和审核回复草稿、批量发送回复、分析评论以及发布图文笔记。
  当用户要求登录小红书、查看或发布笔记、扫描或回复评论、分析评论，或者要求 AI
  通过本项目操作小红书时使用此技能。
---

# 小红书命令行助手

使用本项目的 `main.py` 管理小红书笔记和评论。面向用户使用中文输出，面向 AI 提供稳定的 JSON 接口。

## 基本规则

1. 在项目根目录运行命令，首先执行 `python3 main.py doctor --json` 检查环境。
2. 查询、扫描和预览等只读操作可以直接执行。
3. 发送回复或发布笔记前必须先预览。
4. 除非用户明确要求自动发送，否则先展示草稿并等待确认。
5. AI 和脚本应优先使用 `--json` 与 `--output`，不要解析表格或 emoji。
6. 默认使用通知快速扫描；只有用户明确要求完整检查时才使用 `--full-scan`。
7. 不回复已删除或已加入跳过列表的评论。
8. 批量回复时保留配置的请求间隔，避免触发平台限流。
9. 不向用户展示 Cookie、`xsec_token` 或其他登录凭据。
10. 向用户展示笔记列表、评论列表、回复草稿或发送结果明细时，统一使用 Markdown 表格。
11. 表格只用于面向用户展示；AI 与命令行之间仍使用 JSON 文件或 `--json` 输出。
12. 使用 `--output` 后优先读取生成的文件，不要求命令在终端重复输出完整数据。
13. 评论分析默认使用摘要；只有用户明确需要全部明细时才使用 `analyze --json --details`。
14. 所有 AI 必须复用 `.cache/workflows/<笔记ID>/`，不得为同一批任务另建临时存档。
15. 生成任何回复内容前，必须先检查该评论是否已经被作者回复。
16. 只有扫描结果中 `reply_status_verified` 为 `true` 时，才生成回复草稿。
17. `scan.json` 中的未回复数组只是候选；`drafts.json` 的历史发送状态优先级更高。
18. 同一 `comment_id` 已标记为 `sent`、`failed` 或 `archived` 时，不得重新生成或发送。
19. 本地没有发送记录不等于平台没有回复；运行 `drafts` 时必须再次在线读取评论树，并按作者回复的 `target_comment.id` 核验一级评论和楼中楼。
20. 在线核验失败、需要验证码或候选评论在平台不可见时，停止生成该评论的草稿，不得用本地状态推断为未回复。

## 固定工作目录

每篇笔记使用唯一目录：

```text
.cache/workflows/<笔记ID>/
├── scan.json
├── reply_map.json
└── drafts.json
```

发布笔记统一使用：

```text
.cache/workflows/post/note.json
```

开始任务时先查询路径和已有文件：

```bash
python3 main.py paths --note-id <note_id>
```

约定：

- `scan.json` 保存最近一次扫描结果。
- `reply_map.json` 保存 AI 生成并可继续修改的回复映射。
- `drafts.json` 保存已组装、待预览或待发送的草稿。
- 文件存在时先读取并复用，不重复扫描或另存为带时间戳、批次号的副本。
- 只有用户要求保留多个版本时，才创建额外文件。
- `.cache` 已被 Git 忽略，不提交账号工作数据。
- 发送状态会逐条写回 `drafts.json`；先读取 `send_status`，不得重复发送已标记为 `sent` 的项目。

## 回复评论判定算法

所有 AI 必须逐条按 `comment_id` 执行以下算法，不得仅凭 `scan.json` 生成回复：

```text
1. 读取 scan.json、drafts.json、reply_map.json 和 .cache/skipped.json。
2. 如果 scan.json.reply_status_verified != true：停止，不生成回复。
3. 对 scan.json 的每个候选评论：
   a. drafts.json 中 send_status == sent：排除，状态为“已回复”。
   b. drafts.json 中 send_status == archived：排除，状态为“已归档”。
   c. drafts.json 中 send_status == failed：排除，状态为“发送失败”；程序应已将其加入排除列表。
   d. comment_id 在 .cache/skipped.json：排除，状态为“已跳过”。
   e. 平台确认评论已删除或作者已回复：排除。
   f. 以上均不成立：加入本次可回复清单。
4. 可回复清单为空：报告“没有可回复评论”，不得使用旧 reply_map.json。
5. 只为可回复清单创建或更新 reply_map.json。
6. 生成 drafts.json 后，历史 sent、failed、archived 状态必须保留。
7. 先 dry-run 展示，用户确认后再发送。
```

关键解释：

- 通知扫描存在延迟，已经发送成功的评论可能再次出现在 `unreplied_level1` 中。
- `unreplied_level1` 和 `unreplied_subs` 的含义是“本次平台候选”，不是“最终允许发送”。
- 本地 `send_status` 是防止不同 AI 重复发送的最终依据。
- `failed` 在当前程序中属于终态，所有失败评论都会自动加入 `.cache/skipped.json`；如需重试，必须先展示失败原因、获得明确授权并移出排除列表。

## 表格展示规范

所有使用本技能的 AI 必须遵守以下展示格式：

- 笔记列表默认且固定使用：`序号｜发布时间｜评论数｜标题｜笔记 ID`
- 评论列表默认且固定使用：`序号｜时间｜用户｜评论｜状态`
- 回复草稿使用：`序号｜用户｜原评论｜拟回复｜操作`
- 发送结果使用：`序号｜用户｜回复摘要｜结果｜失败原因`

展示时遵守：

- 内容较长时可以截断，并使用省略号标明。
- 没有标题时显示“无标题”，不得留空造成歧义。
- 已删除、已跳过、已回复和发送失败等状态必须明确标出。
- 不在表格中显示 Cookie、`xsec_token` 或其他凭据。
- 只有单条数据时也使用表格，保持所有 AI 的输出一致。
- 评论列表必须保持上述列名和顺序；除非用户明确要求，否则不得增删、改名或调整顺序。
- 笔记列表必须保持上述列名和顺序；除非用户明确要求，否则不得增删、改名或调整顺序。
- “状态”列统一使用“正常、回复了你的评论、已回复、已删除、已跳过、发送成功、发送失败”等明确中文状态。
- 同时展示多篇笔记的评论时，必须先按笔记分组，每篇笔记分别使用一张评论表。
- 每组标题使用 `序号. 笔记标题（笔记 ID）`；无标题时使用 `序号. 无标题（笔记 ID）`。
- 笔记标题和笔记 ID 只显示在分组标题中，不在表格行内重复展示。
- 多篇笔记扫描结果以 `per_note[].note_index` 作为文章序号，所有 AI 必须保持该序号和顺序。

## 环境检查与登录

```bash
python3 main.py doctor --json
python3 main.py login
```

登录默认读取 `config.py` 中 `LOGIN_COOKIE_SOURCE` 指定的浏览器，目前默认为 Firefox。登录失败时，提醒用户先在该浏览器中登录小红书。

查看供 AI 使用的调用协议：

```bash
python3 main.py ai-help
```

## 查看笔记

用户可读输出：

```bash
python3 main.py articles
python3 main.py articles --limit 20
```

AI 或脚本使用：

```bash
python3 main.py articles --limit 20 --json
```

使用返回的 `id` 作为后续命令的 `<note_id>`。不得向用户展示结果中的 `xsec_token`。

## 扫描评论

默认从最新评论通知快速扫描：

```bash
python3 main.py scan --note-id <note_id> --json
```

扫描所有笔记的近期通知：

```bash
python3 main.py scan --json
```

只有用户明确要求检查全部历史评论时，才执行全量扫描：

```bash
python3 main.py scan --note-id <note_id> \
  --full-scan --with-subs --json
```

扫描不会向小红书写入数据。结果中的 `unreplied_level1` 和 `unreplied_subs` 是待回复候选评论。
命令终端只返回固定文件路径和数量摘要；按需读取该笔记目录中的 `scan.json`。

生成回复前检查：

- 确认 `scan.json` 的 `reply_status_verified` 为 `true`。
- 同时读取 `drafts.json`，按 `comment_id` 排除 `sent`、`failed` 和 `archived`。
- 同时读取 `.cache/skipped.json`，排除已跳过评论。
- 已回复、已发送、已归档、已跳过或已删除的评论不得进入回复映射。
- 核验失败时停止生成回复，并向用户说明原因。
- `--allow-unverified` 仅用于用户明确接受重复回复风险的特殊情况，不得默认使用。

## 生成回复草稿

读取 `.cache/workflows/<笔记ID>/scan.json`，为适合回复的评论生成简洁、自然、有针对性的中文回复。

回复要求：

- 针对评论内容作答，避免机械套话。
- 面对质疑或攻击时保持克制，不升级冲突。
- 不使用歧视、侮辱或骚扰性表达。
- 不泄露个人信息或登录凭据。
- 不虚构文章中不存在的事实、数据或承诺。
- 无意义、已删除或不适合回复的评论可以跳过或归档。

创建或更新 `.cache/workflows/<笔记ID>/reply_map.json`：

```json
{
  "<comment_id_1>": "针对该评论的回复",
  "<comment_id_2>": {
    "reply": "",
    "action": "archive"
  }
}
```

`action` 可取：

- `send`：发送回复
- `skip`：本次跳过
- `archive`：永久跳过并加入跳过列表

将回复映射转换成草稿：

```bash
python3 main.py drafts --note-id <note_id> \
  --from-scan .cache/workflows/<note_id>/scan.json \
  --batch .cache/workflows/<note_id>/reply_map.json
```

## 预览和发送回复

必须先预览：

```bash
python3 main.py send --file .cache/workflows/<note_id>/drafts.json --dry-run
```

使用“回复草稿”表格向用户展示原评论和对应回复。获得用户明确确认后再发送：

```bash
python3 main.py send --file .cache/workflows/<note_id>/drafts.json
```

发送中断后可以续发：

```bash
python3 main.py send --file .cache/workflows/<note_id>/drafts.json --resume
```

完成后先给出成功、失败和跳过数量，再使用“发送结果”表格展示明细，不得把失败描述成成功。

## 发布笔记

AI 负责生成标题、正文和话题，本工具负责校验和发布。小红书图文笔记至少需要一张本地图片。

创建或更新 `.cache/workflows/post/note.json`：

```json
{
  "title": "简洁标题",
  "body": "笔记正文",
  "images": ["/图片的绝对路径/封面.jpg"],
  "topics": ["话题1", "话题2"],
  "private": false
}
```

先校验和预览：

```bash
python3 main.py post --input .cache/workflows/post/note.json --dry-run
```

向用户展示标题、正文摘要、图片数量、话题和可见范围。获得明确确认后发布：

```bash
python3 main.py post --input .cache/workflows/post/note.json
```

没有明确授权时不得发布，也不得把 `--dry-run` 的结果描述成已经发布。

## 评论分析

```bash
python3 main.py analyze --note-id <note_id>
python3 main.py analyze --note-id <note_id> --json
```

情感分析基于关键词，只能作为粗略参考，不得将结果描述成可靠的人格、立场或心理判断。

## 管理跳过列表

```bash
python3 main.py skipped
python3 main.py skipped --remove <comment_id>
python3 main.py skipped --clear
```

清空跳过列表会改变本地持久状态，执行前必须获得确认。

## 错误处理

- `comment_deleted`：加入排除列表且不重试，不把评论删除描述成发送成功。
- `rate_limited`：停止发送或延迟重试，不连续快速提交。
- `content_rejected`：修改措辞并重新预览，不原样反复提交。
- `unknown_error`：保留错误信息并向用户准确汇报。
- 登录错误：重新运行 `login`，确认配置的浏览器已登录小红书。
- 验证码或平台验证：停止自动操作，由用户亲自完成验证。

## 详细命令

需要查看完整参数或 Python API 时，读取 [references/commands.md](references/commands.md)。
