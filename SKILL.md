---
name: xhs-auto-reply
description: >
  通过本系统管理小红书账号，包括浏览器 Cookie 登录、查看笔记、
  扫描最新评论或全部评论、生成和审核回复草稿、批量发送回复、分析评论以及发布图文笔记。
  当用户要求登录小红书、查看或发布笔记、扫描或回复评论、分析评论，或者要求 AI
  通过本项目操作小红书时使用此技能。
---

# 小红书AI智能运营系统

其他AI进入项目后优先读取根目录`AGENTS.md`；该文件是精简执行协议。
本SKILL保留更完整的操作规则和说明。

使用本项目的 `main.py` 管理小红书笔记和评论。面向用户使用中文输出，面向 AI 提供稳定的 JSON 接口。

## 基本规则

1. 在项目根目录运行命令，首先执行 `python3 main.py doctor --json` 检查环境。
   当前发布为`5.2.0`、AI协议schema为`10`；以`ai-help --summary`运行时输出为准。
2. 查询、扫描和预览等只读操作可以直接执行。
3. 发送回复或发布笔记前必须先预览。
4. 除非用户明确要求自动发送，否则先展示草稿并等待确认。
5. AI 和脚本应优先使用 `--json` 与 `--output`，不要解析表格或 emoji。
6. 用户只要求查看评论时使用 `comments`；只有要求回复时才使用 `scan`。
7. 回复任务默认使用通知快速扫描；只有用户明确要求完整检查时才使用 `--full-scan`。
8. 在线楼中楼数据不完整时必须停止；这是防止重复回复的安全规则，不得降级猜测。
9. 不回复已删除或已加入跳过列表的评论。
10. 批量回复时保留配置的请求间隔，避免触发平台限流。
11. 不向用户展示 Cookie、`xsec_token` 或其他登录凭据。
12. 向用户展示笔记列表、评论列表、回复草稿或发送结果明细时，统一使用 Markdown 表格。
13. 表格只用于面向用户展示；AI 与命令行之间仍使用 JSON 文件或 `--json` 输出。
14. 使用 `--output` 后优先读取生成的文件，不要求命令在终端重复输出完整数据。
15. 评论分析默认使用摘要；只有用户明确需要全部明细时才使用 `analyze --json --details`。
16. 所有 AI 必须复用 `.cache/workflows/<笔记ID>/`，不得为同一批任务另建临时存档。
17. `comments`已读取的完整评论正文累计保存在`.cache/comments.json`；
    必须用JSON解析器读取，不手工处理文件中的引号转义。
18. 生成任何回复内容前，必须先检查该评论是否已经被作者回复。
19. 只有扫描结果中 `reply_status_verified` 为 `true` 时，才生成回复草稿。
20. `scan.json` 中的未回复数组只是候选；`drafts.json` 的历史发送状态优先级更高。
21. 同一 `comment_id` 已标记为 `sent`、`failed`、`archived`或`sending`时，不得重新生成或自动发送；`sending`必须先在线对账。
22. 本地没有发送记录不等于平台没有回复；运行 `drafts` 时必须再次在线读取评论树，并按作者回复的 `target_comment.id` 核验一级评论和楼中楼。
23. 在线核验失败、需要验证码或候选评论在平台不可见时，停止生成该评论的草稿，不得用本地状态推断为未回复。
24. `.cache/state.sqlite3`是0600权限的权威状态库；JSON文件仅作为AI交换
    入口或兼容快照。账号ID首次运行自动识别，禁止写入仓库配置。
25. 读取`ai-help --summary.release_consistency`；版本、公开账号隐私或Git发布
    快照不一致时必须明确报告。`working_tree_not_published`不是GitHub缓存。

## 固定工作目录

每篇笔记使用唯一目录：

```text
.cache/state.sqlite3
.cache/workflows/<笔记ID>/
├── scan.json
├── reply_map.json
├── drafts.json
└── audit.json
.cache/comments.json
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

- `state.sqlite3`统一保存身份、工作流、评论归档、排除列表、缓存和令牌；
  首次`doctor`幂等导入旧JSON；SQLite哈希核对成功后删除历史迁移源，
  当前AI交换快照保留。
- `scan.json` 保存最近一次扫描结果。
- `reply_map.json` 保存 AI 生成并可继续修改的回复映射。
- `drafts.json` 保存已组装、待预览或待发送的草稿。
- `audit.json`以0600权限保存`ai-reply`命令的开始、结束、参数摘要、结果和
  错误，不保存评论正文、回复正文或凭据；最多保留500条事件。
- `.cache/comments.json`是0600权限的兼容快照，累计保存通知接口已返回且程序未截断的
  原始评论正文，不替换引号；这不代表已核对平台完整评论树。
- 文件存在时先读取并复用，不重复扫描或另存为带时间戳、批次号的副本。
- 只有用户要求保留多个版本时，才创建额外文件。
- `.cache` 已被 Git 忽略，不提交账号工作数据。
- 发送状态会逐条写回 `drafts.json`；先读取 `send_status`，不得重复发送已标记为 `sent` 的项目。
- 状态来源按职责固定：`reply_map.json`以AI编辑快照为准并同步入SQLite；
  `scan.json`、`drafts.json`和`audit.json`以SQLite为准，程序自动修复失配快照。
- `preview_hash`和SQLite文档哈希共用规范化JSON编码；对象键顺序无关，列表
  顺序仍参与指纹。
- 打印执行流程时先运行`paths --note-id <ID> --audit-limit 20`，先按
  `workflow_id`选择同一轮prepare、draft重试和send，再按相同`command_id`
  配对审计事件。新prepare生成新编号；旧事件无编号时不得跨prepare拼接。
  普通`paths`默认不内联事件。没有事件
  只能报告当前状态，不得根据最终草稿倒推出不存在的失败批次或执行时间。
- 通知中的`xsec_token`先写入权限为0600的SQLite索引，再从
  `scan.json`移除；后续步骤复用索引，不得因脱敏而退回无令牌请求。

## 回复评论判定算法

所有 AI 必须逐条按 `comment_id` 执行以下算法，不得仅凭 `scan.json` 生成回复：

```text
1. 读取 scan.json、drafts.json、reply_map.json 和 .cache/skipped.json。
2. 如果 scan.json.reply_status_verified != true：停止，不生成回复。
3. 对 scan.json 的每个候选评论：
   a. drafts.json 中 send_status == sent：排除，状态为“已回复”。
   b. drafts.json 中 send_status == failed：排除，状态为“发送失败”；程序应已将其加入排除列表。
   c. drafts.json 中 send_status == archived：排除，状态为“已归档”。
   d. comment_id 在 .cache/skipped.json：排除，状态为“已跳过”。
   e. 平台确认评论已删除或作者已回复：排除。
   f. 以上均不成立：加入本次可回复清单。
4. 可回复清单为空：报告“没有可回复评论”，不得使用旧 reply_map.json。
5. 只为可回复清单创建或更新 reply_map.json。
6. 生成 drafts.json 后，历史 sent、failed、archived和sending状态必须保留。
7. 先 dry-run 展示，用户确认后再发送。
```

关键解释：

- 通知扫描存在延迟，已经发送成功的评论可能再次出现在 `unreplied_level1` 中。
- `unreplied_level1` 和 `unreplied_subs` 的含义是“本次平台候选”，不是“最终允许发送”。
- 本地 `send_status` 是防止不同 AI 重复发送的最终依据。
- `failed` 在当前程序中属于终态，所有失败评论都会自动加入
  `.cache/skipped.json`；如需重试，必须先展示失败原因、获得明确授权，
  同时移出排除列表并重置 `drafts.json` 中的失败终态。

## 表格展示规范

所有使用本技能的 AI 必须遵守以下展示格式：

- 笔记列表默认且固定使用：`序号｜发布时间｜评论数｜标题｜笔记 ID`
- 评论列表默认且固定使用：`序号｜时间｜用户｜评论｜状态`
- `comment_id` 仅供内部处理，不得显示在面向用户的评论表格中。
- 回复草稿使用：`序号｜用户｜原评论｜拟回复｜操作`
- 发送结果使用：`序号｜用户｜回复摘要｜结果｜失败原因`

展示时遵守：

- 评论列表和回复草稿中的原评论必须完整显示，不得截断、
  摘要、改写或使用省略号代替。
- `comments --json`返回的`groups`已经安全转义并插入必要的`<br>`和
  `<wbr>`；按`display.column_fields`直接展示，不得再次转义或插入标记。
  未经展示处理的原文读取`archive.path`。
- 五列按`comments --json`的`display.columns`自适应：序号紧凑不换行；
  时间、用户名和长状态的展示断点已由程序生成；评论列是唯一主伸缩列。
  不得用固定截断代替软换行。
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
python3 main.py ai-help --summary
python3 main.py ai-help --command <命令>
python3 main.py ai-help --tests
python3 main.py ai-help
```

首次理解项目优先使用`--summary`；核对单条命令时使用`--command`，其参数、
默认值、副作用和输出约定由程序自动生成；只有需要完整状态规则和发布协议时
才读取完整输出。
`--tests`只列出当前快照的测试。没有旧快照或版本控制差异时，禁止推断哪些
测试或实现是本轮新增、删除或修改。

## 查看笔记

用户可读输出：

```bash
python3 main.py articles
python3 main.py articles --limit 10
```

AI 或脚本使用：

```bash
python3 main.py articles --json
```

`--limit`可省略，默认10篇。`articles`对平台只读，但可能更新本地权限为
0600的敏感`xsec_index.json`令牌索引。
严格按`columns`和`column_fields`展示所有列，“笔记ID”对应`note_id`，
不得省略。超过20篇时按`pagination.next_command`读取`.cache/articles.json`
后续页，直到`has_more=false`；不得依赖可能被截断的一条超长输出。
使用返回的`note_id`作为后续命令的`<note_id>`。不得向用户展示结果中的
`xsec_token`。

## 查看评论

查看最新评论使用独立的只读命令，不得用 `scan` 代替：

```bash
python3 main.py comments --json
python3 main.py comments --note-id <note_id> --limit 50 --json
```

`--limit`可省略，默认20条。
`comments` 返回按文章分组的 `groups`，包含文章序号、评论时间、用户、正文和状态；它不会生成回复候选或修改草稿。
面向用户显示时必须使用`content`全文，不得为节省token而截断。
`groups`已经是可直接展示的安全值，按`display.column_fields`生成表格，
不要二次转义或再次插入`<wbr>`。通知载荷中的原始正文会累计写入返回的
`archive.path`；`content_complete_scope=notification_payload`不代表已读取
或核验平台完整评论树。
若`archive.ok=false`，仍可展示本次`groups`，但必须报告`warnings`且不得
覆盖无法解析的旧归档。

## 扫描待回复评论

只有用户要求回复评论时才运行 `scan`。默认从通知提取候选，先过滤删除、跳过和本地终态，再在线核验：

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
- 同时读取 `drafts.json`，按 `comment_id` 排除 `sent`、`failed`、`archived`
  和`sending`。
- 同时读取 `.cache/skipped.json`，排除已跳过评论。
- 已回复、已发送、已归档、已跳过或已删除的评论不得进入回复映射。
- 核验失败时停止生成回复，并向用户说明原因。
- `--allow-unverified` 仅用于兼容缺少核验标记的旧扫描文件，不得默认使用；
  即使启用，生成草稿前仍会强制在线核验。

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
  "<comment_id>": {
    "reply": "针对该评论的回复",
    "action": "send",
    "review": {
      "logic": {
        "verdict": "partly_sound",
        "reason": "观点有可讨论部分，但论据不足"
      },
      "fact_check": {
        "verdict": "unverifiable",
        "reason": "个人经历无法独立核实",
        "sources": []
      },
      "boast_check": {
        "verdict": "none",
        "reason": "没有自我夸大或成就宣称"
      }
    }
  }
}
```

`action` 可取：

- `send`：发送回复
- `skip`：本次跳过
- `archive`：用户确认并执行`send`动作后永久跳过并加入跳过列表，不向平台回复

`skip`和`archive`的`reply`可以为空；只有`action=send`要求非空回复。
`action=send` 时 `reply` 必须是非空字符串；映射格式错误时程序返回
`ok=false` 和 `details`，不会生成可发送草稿。
AI流程要求本次每条候选都有对象映射和`review`；字符串简写和缺少映射都会
被拒绝，不得由AI自动补写通用回复。
`draft`在联网前先验证映射文件的JSON语法、顶层对象类型和本次候选的
`action`、`reply`、`review`语义。AI生成的回复和审查正文默认使用中文引号
`“”`或`「」`；JSON结构所需的英文双引号必须保留，禁止全文件替换。正文
确需英文双引号时必须转义，优先由标准JSON写入器完成。不得修改评论原文。
程序只会自动修复独立`reply/reason`文本行中成对、未转义的英文引号，且
修复后必须整文件解析成功；成功返回`reply_map_repaired`与替换数量。其他
JSON错误按`error_location`人工修复，不得全局替换或猜测结构。
只校验本次scan候选对应的映射；其他批次旧键允许保留，不参与本次发送。
同一用户、相同正文的多条候选最多一条可设为`send`；否则`draft`返回
`duplicate_send_mapping`，其余重复项改为`skip`后重试。

`review`必须先于回复决策完成：

- `logic.verdict`：`sound|partly_sound|weak|fallacious|non_argument|unclear`。
- `fact_check.verdict`：`supported|mixed|contradicted|unverifiable|not_applicable`。
  前三种必须提供至少一个带HTTP(S) URL的来源；个人经历无法独立核实时用
  `unverifiable`，不得直接判假。
- `boast_check.verdict`：`none|possible|likely|unverifiable|not_applicable`。
  语气强硬、逻辑错误或没有附来源本身不等于吹牛。
- 三个字段都要有非空`reason`。程序只验证结构和来源格式，AI必须实际完成
  逻辑分析、必要的联网事实核查和审慎的吹牛判定。
- `draft.reviews`会为枚举附带中文`label`；审查表显示`label + reason`，
  `fact_check.sources`显示为可点击链接。

将回复映射转换成草稿：

```bash
python3 main.py drafts --note-id <note_id> \
  --from-scan .cache/workflows/<note_id>/scan.json \
  --batch .cache/workflows/<note_id>/reply_map.json
```

## 预览和发送回复

### AI 优先使用的低 token 流程

```bash
python3 main.py ai-reply --note-id <note_id> --action prepare
# 逐条完成逻辑分析、事实核查和吹牛判定，再写入 paths.reply_map
python3 main.py ai-reply --note-id <note_id> --action draft
# 先展示 reviews 审查表，再展示 preview 草稿表并取得明确确认
python3 main.py ai-reply --note-id <note_id> --action send --confirmed \
  --batch-id <draft返回的batch_id> \
  --preview-hash <draft返回的preview_hash>
```

每个动作只输出一个紧凑 JSON。`send` 会再次在线核验；未提供
`--confirmed`、`--batch-id`或`--preview-hash`时不得发送。除非需要兼容旧脚本，AI 不再组合调用
`paths → scan → drafts → send --dry-run`。
`comment_id`只供内部映射和定位，不得出现在面向用户的评论、草稿或执行
说明表格。纯辱骂、贴标签且没有实质观点的评论默认`skip`；回复不得编造
数据或来源，不得使用无法核实的绝对结论或升级冲突。

`prepare` 会停用上一批草稿，`draft` 将当前候选写入
`drafts.json.active_comment_ids`，`send` 只发送本次活动批次，历史草稿
不会混入本次发送。
`draft`同时返回并保存`batch_id、revision、preview_hash`；AI必须把用户确认
绑定到这份预览，发送时原样提交批次号和指纹。任何不匹配都应重新预览。
`draft`开始时也会先停用旧活动批次，但保留全部历史草稿和终态；只有成功
生成后才写入新的`active_comment_ids`。
历史合并保留全部旧条目：同ID终态不覆盖，同ID非终态可更新，未进入新批
的旧条目仍保留但由`active_comment_ids`隔离。
新批次必须按prepare、写映射、draft、用户确认、send执行；CLI依赖状态
文件校验，已有合法活动批次允许稍后继续send，不能称为物理上无法跳转。
同一笔记的`ai-reply`由跨进程锁串行化；`workflow_busy`时等待当前进程
完成，不得并行启动第二个命令。
平台写请求前程序先保存`send_status=sending`。进程中断后必须在线对账；
`uncertain_send_state`表示结果仍不确定，禁止自动重发。

在线请求、验证码、分页或完整性核验失败时必须硬停止且不归档候选；只有
平台成功返回并确认评论已回复或不存在时，发送阶段才标记`archived`。
默认`prepare`达到6页快速定位预算不是平台失败：无法定位的深层楼中楼只计入
`deferred_count`并排除在本批之外，其余已核验候选可以继续。
这种在线排除只写本地`send_status=archived`；只有映射中的
`action=archive`会在确认send后写入全局`skipped.json`。
候选已定位时只补全候选所在楼层，该楼层补拉后仍少于平台
`sub_comment_count`时硬停止。候选尚未定位时才按需搜索其他不完整楼层；
找到候选后无关楼层失败不阻断整批，仍未找到且存在不完整数据时硬停止。
通知楼中楼候选保留内部`target_comment_id`用于定位，不向用户显示。带令牌
helper普通故障时才回退原生`sub-comments`；验证码或
`verification_required`立即停止，禁止用第二种传输重复请求。严格核验保留
真实错误。

`prepare` 默认只处理最新20条评论通知中的一级评论和楼中楼。只有用户
明确要求全部历史评论时，才追加 `--full-scan`；使用 `--limit` 可以
调整最新评论通知数量。
默认通知模式最多使用6页快速定位预算；无法定位的深层楼中楼计入
`deferred_count`且不进入本批草稿。只有用户明确要求处理这些延后项时才用
`--full-scan`。
默认入口是`scan_via_notifications`。`--full-scan`改用`scan_note`读取
整篇笔记，不是通知扫描，也不调用`verify_candidates_online`。
prepare输出中的`scan_method`和`verification_mode`给出本次真实执行方式。
统一称为“条件式多阶段在线核验”，不得描述为固定三次在线核验。
全量`prepare`固定使用`force_refresh=true`绕过评论TTL缓存，并读取完整
楼中楼。

必须先预览：

```bash
python3 main.py send --file .cache/workflows/<note_id>/drafts.json --dry-run
```

使用“回复草稿”表格向用户展示原评论和对应回复。获得用户明确确认后再发送：
其中“原评论”列必须完整显示，不得截断。

```bash
python3 main.py send --file .cache/workflows/<note_id>/drafts.json
```

传统 `send` 同样只处理 `active_comment_ids` 指定的本批项目，并在实际发送前
再次在线核验；平台数据不完整或草稿缺少 `note_id` 时停止。
平台明确判定为已回复或不存在的项目会标记`archived`并写回草稿。

发送中断后可以续发：

```bash
python3 main.py send --file .cache/workflows/<note_id>/drafts.json --resume
```

`--resume`仅改变续发提示文案；终态和全局排除过滤始终生效。
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

失败类型以`XHSClient.REPLY_ERROR_TYPES`为准，当前处理规则如下：

- `comment_deleted`：加入排除列表且不重试，不把评论删除描述成发送成功。
- `rate_limited`：停止发送或延迟重试，不连续快速提交。
- `content_rejected`：修改措辞并重新预览，不原样反复提交。
- `permission_denied`：对方设置不允许评论，加入排除列表且不重试。
- `unknown_error`：保留错误信息并向用户准确汇报。
- `workflow_busy`：同一笔记已有AI工作流运行，等待后重试，不启动并行进程。
- `audit_unavailable`：审计开始事件无法安全落盘，业务动作尚未执行；先修复
  `audit.json`或目录权限，禁止绕过审计直接发送。
- `stale_preview`或`preview_content_changed`：重新运行draft、展示新预览并
  取得确认，禁止沿用旧批次号或旧指纹。
  `stale_preview.mismatch`分别标明批次号和预览指纹是否不一致，并提供当前
  修订号与状态；它不返回新的有效确认值，也不能单独证明是其他AI修改。
- `uncertain_send_state`：平台写结果不确定，先由用户在线核对，禁止自动重发。
- 登录错误：重新运行 `login`，确认配置的浏览器已登录小红书。
- 验证码或平台验证：`ai-reply`返回
  `error_type=verification_required`、`automatic_retry=false`和
  `requires_user_action=true`。停止自动重试。若`type/uuid`均为`unknown`，
  这是无可见挑战信息的API风控，浏览器正常也可能发生；先重新导入Firefox
  Cookie并重跑当前action，仍失败则等待。只有出现可见挑战时才请用户在
  Firefox完成验证；不得尝试绕过验证。
- 大批量回复复用一个登录会话并逐条保存结果；出现`rate_limited`、
  `verification_required`、`not_authenticated`或`session_error`时立即暂停
  剩余评论，不把尚未请求的评论误记为失败。
- 硬停止不会把候选标记为`archived`或`failed`，但`prepare`和`draft`
  开始时仍会停用旧`active_comment_ids`；不得描述为完全不写本地状态。

## 详细命令

需要查看完整参数或 Python API 时，读取 [references/commands.md](references/commands.md)。
