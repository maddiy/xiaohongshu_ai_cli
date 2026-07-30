# 小红书AI智能运营系统 v4.0.0

- 系统名称：`小红书AI智能运营系统`

这是一个面向中文用户和 AI 助手的小红书运营工具。

它负责连接小红书、读取笔记与评论、保存回复草稿以及执行发布；内容创作和回复文案可以由任意 AI 编程助手完成，不依赖特定模型或厂商。

**AI 一句话安装使用：** 在任意支持终端和文件操作的 AI 编程助手中打开本项目，然后说“安装并检查本项目，使用浏览器 Cookie 登录小红书，按统一工作目录读取笔记和评论、生成并预览回复或笔记，获得我的确认后再发送或发布”即可。

**其他AI首先读取：** [`AGENTS.md`](AGENTS.md)。该文件是最短、最稳定的
执行入口；`README.md`用于人工说明，`references/commands.md`用于参数查询。

## 可以做什么

| 功能 | 命令 | 说明 |
|---|---|---|
| 环境检查 | `doctor` | 检查 Python、`xhs`、账号和浏览器配置 |
| 登录 | `login` | 从指定浏览器读取小红书 Cookie |
| 查看笔记 | `articles` | 查看账号最近发布的笔记 |
| 查看评论 | `comments` | 查看最新评论及状态，不生成回复候选 |
| 扫描待回复 | `scan` | 在线核验并查找尚未回复的评论 |
| 生成草稿 | `drafts` | 交互输入或批量导入 AI 回复 |
| 发送回复 | `send` | 预览并发送审核后的草稿 |
| 兼容回复 | `reply` | 旧入口；发送前仍强制执行本地过滤和平台在线核验 |
| 评论分析 | `analyze` | 统计评论、互动和粗略情感倾向 |
| 跳过列表 | `skipped` | 管理不再处理的评论 |
| 发布笔记 | `post` | 发布 AI 或人工准备好的图文笔记 |
| AI 协议 | `ai-help` | 输出机器可读的标准调用流程 |
| 工作路径 | `paths` | 返回跨 AI 共用的固定状态文件路径 |
| AI 回复 | `ai-reply` | 用紧凑 JSON 完成扫描、草稿预览和发送 |

### 代码结构

| 文件 | 职责 |
|---|---|
| `AGENTS.md` | 其他 AI 首先读取的最短执行协议 |
| `main.py` | `COMMAND_HANDLERS`命令分发及传统回复工作流编排 |
| `config.py` | 应用名称、账号、浏览器、延迟和工作目录配置 |
| `lib/cli_parser.py` | 唯一命令清单和命令行参数定义 |
| `lib/cli_view.py` | 登录及文章、评论查看；其中登录会更新认证状态 |
| `lib/cli_admin.py` | 发布、分析、环境检查和 AI 协议 |
| `lib/cli_ai.py` | AI 专用 `prepare → draft → send` 工作流 |
| `lib/cli_support.py` | 原子存储、状态合并和精简输出 |
| `lib/state_io.py` | 跨进程文件锁和原子JSON写入 |
| `lib/scanner.py` | 最新/全量扫描和在线回复核验 |
| `lib/replier.py` | 草稿生成、发送和失败排除 |
| `lib/analyzer.py` | 评论统计与摘要分析 |
| `lib/poster.py` | 图文笔记校验、预览和发布 |
| `lib/xhs_client.py` | `xhs` CLI 封装及私有令牌索引 |
| `lib/xhs_subcomments_helper.py` | `xiaohongshu-cli 0.6.4` 楼中楼令牌兼容层 |
| `tests/test_compact_output.py` | 自动化回归测试；数量以实际运行结果为准 |

当前共有14个子命令，没有快捷别名。`schema_version: 4` 表示 AI 输出协议
版本，应用版本单独由 `python3 main.py --version` 查看。

项目概况优先读取 `python3 main.py ai-help --summary`；需要完整协议时再运行
`python3 main.py ai-help`。`main.py` 不只是分发
入口，还保留了 `scan`、`drafts`、`send`、`reply` 四个传统工作流的编排；
AI 推荐使用 `ai-reply`，传统 `drafts --batch` 也支持非交互导入，不是每条都
必须使用 `input()`。

`COMMAND_NAMES`校验参数解析器中的命令，`main.COMMAND_HANDLERS`单独校验
实际处理器；两项检查共同保证14个命令没有漏定义或漏分发。

需要核对单条命令时运行
`python3 main.py ai-help --command <命令>`。参数、默认值、副作用和输出约定
由当前参数定义自动生成，其他AI不应手工推测或复制旧参数表。

需要当前测试清单时运行`python3 main.py ai-help --tests`。该输出只能证明
当前工作区有哪些测试；没有明确旧版本或版本控制差异时，不得把现有测试、
安全机制或辅助模块描述成“相比上次新增”。

生产源码数量和清单以`ai-help --summary`返回的`source_inventory`为准，
完整相关文件看`project_inventory`；不要在文档中固定测试文件行数。
`login`会导入浏览器Cookie并更新本地认证状态，因此不能归入只读命令。
`articles`对平台是只读操作，但读取笔记时可能更新本地权限为0600的敏感
`xsec_index.json`令牌索引。

## 设计目标

- **中文优先**：命令帮助、提示、错误信息和文档以中文为主。
- **AI 无关**：Cursor、Codex、Claude Code、Copilot、CodeBuddy 等都能通过文件和命令调用。
- **人机分工**：AI 负责理解、创作和生成回复，CLI 负责读取、校验、缓存和执行。
- **先审后发**：回复和笔记都支持预览，默认工作流要求用户确认后再执行。
- **先查后写**：生成回复前先核验评论是否已经回复，避免重复生成和发送。
- **不完整即停止**：楼中楼在线数据不完整时拒绝形成草稿，不以内联数据猜测。
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
- 评论 ID 仅供程序内部核验和回复定位使用，不在面向用户的评论表格中显示。
- 除非用户明确要求，否则所有 AI 不得增删、改名或调整评论列表的列。
- 笔记列表默认固定使用 `序号、发布时间、评论数、标题、笔记 ID`，并保持这一顺序。
- 除非用户明确要求，否则所有 AI 不得增删、改名或调整笔记列表的列。
- 表格用于向用户展示；AI 与 CLI 之间仍使用 JSON。
- 不得在表格中显示 Cookie、`xsec_token` 或其他账号凭据。
- 多篇笔记的评论必须按笔记分组，每篇笔记分别显示一张表。
- 分组标题必须带文章序号，使用 `序号. 笔记标题（笔记 ID）`；无标题时使用 `序号. 无标题（笔记 ID）`。
- 笔记标题和笔记 ID 只显示在分组标题中，不在评论表内重复展示。
- `scan --json` 的 `per_note[].note_index` 是文章分组序号，AI 必须优先使用该字段，不得自行重新排序。

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
- `ai-reply` 会在 `drafts.json` 中保存 `active_comment_ids`；发送动作只处理
  本次活动批次，不会把历史未完成草稿混入本次发送。
- `active_batch`还保存`batch_id、revision、preview_hash`；用户确认必须
  与这三个字段对应的预览一致。
- `.cache/xsec_index.json` 是敏感的本地令牌索引，不是业务状态文件；不得展示、
  复制或提交。
- 通知中的`xsec_token`会先保存到该0600索引，再从`scan.json`中移除。
  后续`draft`和`send`因此可以安全复用令牌，不会退化为无令牌楼中楼请求。

## 回复评论的唯一判定逻辑

`scan.json` 中的 `unreplied_level1` 和 `unreplied_subs` **只是平台扫描候选，不是最终待回复清单**。通知可能延迟，已经发送成功的评论仍可能再次出现在扫描结果中。所有 AI 必须同时读取同一笔记目录中的 `scan.json`、`drafts.json`、`reply_map.json` 以及全局 `.cache/skipped.json`，再按以下顺序判定。

状态优先级从高到低：

1. `drafts.json` 中 `send_status: "sent"`：已经发送成功，禁止再次生成、预览或发送。
2. `drafts.json` 中 `send_status: "failed"`：本轮已经尝试失败，程序会自动加入 `.cache/skipped.json`，禁止自动重试，并保留失败原因。
3. `drafts.json` 中 `send_status: "archived"`，或评论 ID 已在 `.cache/skipped.json`：已经归档，禁止回复。
4. `drafts.json` 中 `send_status: "sending"`：平台结果可能不确定，禁止自动重发，必须在线对账。
5. 平台扫描确认已回复或评论已删除：禁止生成回复。
6. 本次扫描候选只取得继续核验的资格；只有不属于以上状态、
   `reply_status_verified: true`，并通过草稿前及发送前在线复核的评论，
   才能生成并发送回复。

可回复条件必须全部成立：

```text
comment_id 出现在 scan.json 的 unreplied_level1 或 unreplied_subs
AND scan.json.reply_status_verified == true
AND drafts.json 中不存在 send_status 为 sent、failed、archived 或 sending 的同一 comment_id
AND comment_id 不在 .cache/skipped.json
AND 评论未删除
```

标准执行顺序：

1. 运行 `paths`，读取已有 `drafts.json` 和跳过列表。
2. 重新运行 `scan`，得到本次平台候选。
3. 生成草稿前必须再次在线读取平台评论树，以 `target_comment.id` 核验一级评论和楼中楼是否已被作者直接回复。
4. 以 `comment_id` 为唯一键，再用本地终态过滤扫描候选。
5. 如果过滤后为零，直接报告“没有可回复评论”，不得复用旧 `reply_map.json` 重新生成草稿。
6. 只为过滤后剩余的评论更新 `reply_map.json`。
7. 运行 `drafts` 后再次确认没有覆盖历史 `sent`、`failed`、`archived` 状态。
8. 执行 `send --dry-run` 并用表格展示草稿。
9. 获得用户明确确认后才执行实际发送。

禁止行为：

- 不得因为评论再次出现在 `scan.json` 中，就把它视为新的未回复评论。
- 不得覆盖或删除历史 `send_status` 来绕过去重。
- 本地数据库没有发送记录，不代表平台上没有回复；`drafts` 会强制在线核验，在线核验失败时停止生成草稿。
- 所有 `failed` 评论都会自动加入排除列表。需要重试时必须先说明失败原因、
  取得用户明确授权，同时从跳过列表移除并重置 `drafts.json` 中的 `failed`
  终态；仅执行 `skipped --remove` 不会自动重置草稿。
- 不得在没有新候选时复用旧 `reply_map.json` 批量重建草稿。

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
python3 main.py articles --limit 10
```

机器可读输出：

```bash
python3 main.py articles --json
```

`--limit`可省略，默认显示10篇；仅在用户要求其他数量时添加。

### 3. 查看最新评论

查看操作只读取通知，不创建或覆盖回复草稿：

```bash
python3 main.py comments
python3 main.py comments --note-id <笔记ID>
python3 main.py comments --limit 50 --json
```

`--limit`可省略，默认读取20条通知。
结果按文章分组，并结合平台删除状态、本地发送终态和排除列表显示状态。

### 4. 扫描可回复评论

`scan` 专门用于回复准备，会过滤已删除、已跳过和本地终态，并在线核验平台回复：

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

### 5. 生成回复草稿

未提供扫描文件时，`drafts` 默认只读取最新20条评论通知。只有明确需要
处理全部历史评论和楼中楼时才使用 `--full-scan`。

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

### 6. 预览并发送

先预览，不会发送：

```bash
python3 main.py send --file .cache/workflows/<笔记ID>/drafts.json --dry-run
```

确认内容无误后发送：

```bash
python3 main.py send --file .cache/workflows/<笔记ID>/drafts.json
```

传统 `send` 也会遵守 `active_comment_ids`，并在实际发送前再次在线核验；
如果草稿缺少 `note_id`、平台数据不完整或核验失败，程序停止发送。
平台明确判定为`online_replied`或`online_missing`的项目会标记为
`archived`并立即写回草稿文件。

如果发送中断：

```bash
python3 main.py send --file .cache/workflows/<笔记ID>/drafts.json --resume
```

`--resume`是兼容选项，只改变续发提示文案；无论是否使用该参数，程序都会
过滤`sent`、`failed`、`archived`终态、`sending`待对账状态和全局排除列表。

## AI 标准工作流

其他 AI 优先使用 `ai-reply`，每个动作只输出一个紧凑 JSON，不需要
另外执行 `paths`、读取 `scan.json`、调用 `drafts` 或运行 `--dry-run`：

```bash
# 1. 默认只扫描最新20条评论通知，同时识别一级评论和楼中楼
python3 main.py ai-reply --note-id <note_id> --action prepare

# 2. AI 将回复映射写入返回的 paths.reply_map

# 3. 再次在线核验并直接返回 preview
python3 main.py ai-reply --note-id <note_id> --action draft

# 4. 展示 preview，获得用户明确确认后发送
python3 main.py ai-reply \
  --note-id <note_id> \
  --action send \
  --confirmed \
  --batch-id <draft返回的batch_id> \
  --preview-hash <draft返回的preview_hash>
```

`prepare`和`draft`开始时都会停用旧`active_comment_ids`，但不会删除历史
草稿或终态；`draft`成功后写入本次新的活动批次，并返回`batch_id`、
`revision`和`preview_hash`。发送必须原样提交批次号和指纹；草稿被其他AI
修改后旧确认立即失效。
默认`prepare`调用`scan_via_notifications`读取最新20条评论通知；使用
`--full-scan`时改为调用`scan_note`读取整篇笔记、绕过评论缓存并拉取完整
楼中楼。全量模式不是通知扫描，也不调用`verify_candidates_online`。
prepare结果通过`scan_method`和`verification_mode`明确本次执行方式；
在线核验应称为“条件式多阶段核验”，不能描述成三个动作固定核验三次。
通知中的楼中楼候选会保留内部`target_comment_id`，用于直接定位相关楼层，
但该ID不在面向用户的评论表格中显示。
`draft`在联网前先检查`reply_map.json`是否存在、JSON语法和顶层对象类型，
并校验本次候选映射中的`action`和`reply`，避免格式错误浪费在线核验请求。
`reply_map.json`中其他批次的旧键允许保留，不参与本次校验或发送。
同一用户、标准化后正文相同的多条候选最多保留一条`send`，否则`draft`
返回`duplicate_send_mapping`；其余重复项应改为`skip`后再运行。
如果返回`error_type=invalid_reply_map_json`，应先修复文件再重跑`draft`；
JSON字符串中的英文半角双引号必须写成`\"`，也可改用中文引号`“”`或`「」`。
`comment_id`是机器内部主键，允许存在于JSON和映射文件，但不得显示在
面向用户的评论、草稿或流程追踪表格中。纯辱骂或贴标签且没有实质观点的
评论默认`skip`；回复不得编造数据、来源或绝对化结论。

`send` 动作还会在实际发送前进行最后一次在线核验。缺少`--confirmed`、
`--batch-id`或`--preview-hash`时程序拒绝发送。

同一笔记的`ai-reply`由跨进程锁串行执行；已有进程运行时返回
`workflow_busy`。每条平台写请求前先保存`send_status=sending`，如果进程
在请求期间中断，后续只能在线对账，不能自动重发；无法确定时返回
`uncertain_send_state`。

在线请求失败、验证码或数据不完整会直接停止且不会归档候选；只有平台明确
确认候选已经回复或不存在时，发送阶段才将其标记为`archived`。
硬停止仍可能清空旧`active_comment_ids`，因此“不会归档候选”不能表述为
“完全不写入任何本地状态”。
候选已定位时，程序只严格补全候选所在楼层；该楼层补拉后仍少于平台
`sub_comment_count`时硬停止。候选尚未定位时，程序才按需搜索其他不完整
楼层；候选最终在完整楼层中找到后，确定无关楼层的拉取失败不再阻断整批。
如果仍未找到候选且存在不完整楼层，则继续硬停止，禁止猜测评论已删除。
程序优先使用通知中的`target_comment_id`定位楼层。带令牌helper发生普通
故障时才回退原生`xhs sub-comments`；遇到验证码或
`verification_required`会立即停止，避免换一种传输重复请求。严格核验会
返回请求失败的真实原因。
合并草稿历史时会保留全部旧条目：同ID终态保持不变，同ID非终态可被本次
草稿更新，未进入本批的旧条目仍保留但受`active_comment_ids`隔离。
新批次必须遵循prepare、写映射、draft、用户确认、send；CLI依赖状态文件
校验，因此已有合法活动批次可以稍后继续send，不能理解为命令物理上绝对
无法跳转。
在线核验确认`online_replied`或`online_missing`时只写本地
`send_status=archived`；用户映射中的`action=archive`才会在确认send后
写入全局`skipped.json`。

只有用户明确要求处理全部历史评论时才使用：

```bash
python3 main.py ai-reply \
  --note-id <note_id> \
  --action prepare \
  --full-scan
```

该全量动作固定使用`force_refresh=true`绕过评论TTL缓存，并拉取完整楼中楼。
可用 `--limit <数量>` 调整默认读取的最新评论通知数量。

查看完整的机器调用协议：

```bash
python3 main.py ai-help --command ai-reply
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

`drafts` 默认只接受已经核验回复状态的扫描文件。核验失败时应重新扫描。
`--allow-unverified` 仅用于兼容缺少核验标记的旧扫描文件；即使使用该参数，
生成草稿前仍会强制执行在线核验。

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
- `archive`：在用户确认并执行发送动作后永久跳过，写入
  `.cache/skipped.json`，但不向平台回复

`skip`和`archive`的`reply`都可以为空；只有`send`要求非空回复。
本次候选没有出现在映射中时，程序将其视为`skip`，不会发送。其他AI不得
因为映射缺少某个候选而自动补写通用回复。

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

当前失败类型以`XHSClient.REPLY_ERROR_TYPES`为唯一清单，识别条件以
`XHSClient.REPLY_ERROR_MARKERS`为准，不从其他接口的错误码推测：

| 错误类型 | 建议处理 |
|---|---|
| `comment_deleted` | 评论已删除，自动加入排除列表，不再重试 |
| `rate_limited` | 停止或延迟发送，避免连续请求 |
| `content_rejected` | 修改措辞，重新预览后再发送 |
| `unknown_error` | 保存错误信息，检查登录和平台状态 |

无论错误类型是什么，回复失败后都会写入 `.cache/skipped.json`。如需重试，
必须先获得用户明确授权，再移出排除列表并重置 `drafts.json` 中对应评论的
`failed` 终态。

如果出现验证码或平台验证，AI必须停止自动操作，不得把它当作普通冷却错误
连续重试。`ai-reply`会返回`error_type=verification_required`、
`automatic_retry=false`和`requires_user_action=true`。若同时返回
`type=unknown`和`uuid=unknown`，表示接口没有提供可见挑战信息，浏览器页面
正常也可能发生；先重新导入Firefox Cookie并重跑失败的当前action，仍失败
则等待平台风控解除。只有确实出现可见挑战时，才需要用户在Firefox中完成
验证码。已有`scan.json`和`reply_map.json`无需重写。

## 安全建议

- 不要提交或公开 Cookie、`xsec_token` 和账号凭据。
- 不要在未经审核的情况下批量发送 AI 生成内容。
- 发布使用 `--dry-run` 预览；AI 回复使用 `ai-reply --action draft` 预览。
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
