# AI 执行指南

- 系统名称：`小红书AI智能运营系统`

本项目面向中文用户；AI负责理解与生成内容，程序负责登录、读取、在线核验、
保存状态和发送。

## 首次进入

```bash
python3 main.py doctor --json
python3 main.py ai-help --summary
```

- 应用版本：`python3 main.py --version`
- 当前共有14个子命令，没有快捷别名；准确清单以`ai-help --summary`为准。
- 需要某条命令的精确参数、副作用和输出时运行
  `python3 main.py ai-help --command <命令>`，不要手工抄写参数表。
- 需要当前测试名称时运行`python3 main.py ai-help --tests`。它只描述当前
  快照；没有旧快照或版本控制差异时，不得声称哪些测试或实现“本次新增”。
- 测试数量以实际运行结果为准，不在说明文档中硬编码。
- 生产源码看`source_inventory`，完整相关文件看`project_inventory`，不手工
  统计文件数或行数。
- 所有命令从项目根目录运行。
- 机器交互只读取JSON，不解析终端表格或emoji。
- 需要全部字段、状态规则和发布协议时再运行`python3 main.py ai-help`。
- `login`会更新本地认证状态，不得描述为只读命令。
- 在线核验是按候选和动作条件执行的阶段式核验，不得笼统写成三个动作
  无条件调用`verify_candidates_online`。
- `scan.json`候选只能写成“进入后续资格判断”，不得同时称为“可回复”。

## 默认范围

- `articles`默认显示最新10篇。
- `comments`默认读取最新20条评论通知。
- 上述两个命令的`--limit`均可省略；只在用户要求其他数量时添加。
- 回复默认只处理最新20条通知中的一级评论和楼中楼。
- 只有用户明确要求全部历史评论时才使用`--full-scan`。
- `ai-reply prepare --full-scan`会忽略评论TTL缓存并拉取完整楼中楼。
- 全量评论读取和批量回复都复用单一登录会话；禁止退回每页或每条重启
  `xhs`进程的低效调用。遇到限流、验证码或登录失效时暂停剩余批次。
- `ai-reply`的大列表最多内联20行；看到`*_truncated=true`时从对应
  `*_source`固定状态文件读取完整数据，不得把内联片段当作全部结果。

## 查看文章

```bash
python3 main.py articles --json
```

向用户按返回的`columns`显示Markdown表格，必须显示标题和笔记ID。
必须逐列遵守`column_fields`，其中“笔记ID”对应`note_id`，不得省略。
请求超过20篇时程序会自动写入`.cache/articles.json`并分页返回；持续执行
`pagination.next_command`，直到`has_more=false`，再按序合并展示。不得依赖
一条超长终端输出，也不得只补报“缺失区间”。

## 查看评论

```bash
python3 main.py comments --json
```

- 按`groups[].note_index`分别展示每篇文章。
- 分组标题显示文章标题和笔记ID。
- 表格固定列：`序号｜时间｜用户｜评论｜状态`。
- 不向用户显示`comment_id`。
- 用户只要求查看时，到此停止，不运行回复流程。

## AI回复流程

### 1. 准备候选

```bash
python3 main.py ai-reply --note-id <笔记ID> --action prepare
```

读取返回的`candidates`和`paths`。若`count=0`，报告没有可回复评论并停止。
默认模式会在在线核验前排除本地终态，并在一个登录会话内连续读取评论分页
及补全候选楼层；不要把命令拆成多条底层`xhs comments`或
`xhs sub-comments`调用，否则会重复启动进程和读取Cookie。
若返回`deferred_count>0`，表示深层楼中楼超过默认6页快速定位预算，已安全
排除在本批之外；不要为它生成回复。用户明确要求处理全部评论时改用
`--full-scan`。

### 2. 写回复映射

将内容写入返回的`paths.reply_map`：

```json
{
  "<comment_id>": {
    "reply": "回复内容",
    "action": "send"
  }
}
```

`action`只能是`send`、`skip`或`archive`。
`action=send`时`reply`必须是非空字符串；程序会拒绝错误映射并返回`details`。
值也可直接写成非空回复字符串，等价于`action=send`；本次候选没有映射时
默认`skip`，不会发送。
`skip`和`archive`的`reply`都可以为空。
`skip`只跳过本批；`archive`要在用户确认并执行`send`动作后才写入
`skipped.json`。二者都不会向平台回复。
`draft`会在联网前先检查映射文件是否存在、JSON语法和顶层对象类型；
同时校验本次候选的映射语义。其他AI仍应使用合法JSON写入，中文引号可
直接使用，英文双引号必须转义。文件中的其他批次旧键允许保留，不参与
本次校验或发送。
同一用户、相同正文的多条候选最多保留一条`send`，其余必须设为`skip`；
否则`draft`返回`duplicate_send_mapping`且不会发起在线复核。
只有辱骂、贴标签且没有实质观点的评论默认`skip`。回复不得编造数据、来源
或无法核实的绝对结论，也不得升级冲突。

### 3. 生成并展示草稿

```bash
python3 main.py ai-reply --note-id <笔记ID> --action draft
```

使用Markdown表格向用户展示返回的`preview`，然后等待明确确认。
同时保存返回的`batch_id`和`preview_hash`；它们绑定了用户实际审核的
草稿内容，发送时必须原样提交。
`comment_id`只供AI写映射和程序定位，不得显示在面向用户的评论、草稿或
执行流程表格中。
`draft`开始时也会停用旧`active_comment_ids`，但不会删除历史草稿和终态；
成功后才把本次候选设为新的活动批次。
历史合并会保留全部旧条目：同ID终态不覆盖，同ID非终态可更新，未再次
进入新批的旧条目仍保留，但由`active_comment_ids`隔离，不会误发。
新批次必须依次完成prepare、写映射、draft、用户确认和send；但CLI依靠
状态文件校验，已有合法活动批次可以稍后继续send，不能描述成命令在物理上
绝对无法跳转。

### 4. 确认后发送

```bash
python3 main.py ai-reply \
  --note-id <笔记ID> \
  --action send \
  --confirmed \
  --batch-id <draft返回的batch_id> \
  --preview-hash <draft返回的preview_hash>
```

没有用户明确确认时禁止添加`--confirmed`。禁止从当前文件重新推测或替换
批次号和指纹；若返回`stale_preview`，必须重新展示新草稿并取得确认。
发送前程序会再次在线核验。
`prepare`会停用上一批草稿，`draft`写入`active_comment_ids`，`send`只发送
本次活动批次。

## 固定状态文件

```text
.cache/workflows/<笔记ID>/
├── scan.json
├── reply_map.json
└── drafts.json
.cache/skipped.json
.cache/xsec_index.json
```

必须复用这些文件，不为同一任务创建其他临时存档。
通知中的`xsec_token`必须先安全写入权限为0600的`xsec_index.json`，再从
`scan.json`输出中移除；这样后续`draft`和`send`既不泄露令牌，也不会退化
为无令牌楼中楼请求。

状态优先级：

1. `drafts.send_status=sent`
2. `drafts.send_status=failed`
3. `drafts.send_status=archived`或存在于`skipped.json`
4. `drafts.send_status=sending`：平台结果不确定，禁止自动重发
5. 平台显示已回复或评论已删除
6. 本次扫描候选（仅进入后续资格判断，不代表可以回复）

`sent`、`failed`、`archived`均为默认终态，不得重新发送。
`failed`重试需要用户明确授权，同时移出跳过列表并重置草稿中的失败终态；
只运行`skipped --remove`不够。
发送前在线排除只写本地`send_status=archived`；只有用户选择
`action=archive`并确认send时，才写入全局`skipped.json`。

## 安全边界

- 默认`prepare`调用`scan_via_notifications`；`--full-scan`调用
  `scan_note`读取整篇笔记，不是通知扫描，也不调用
  `verify_candidates_online`。
- 统一称为“条件式多阶段在线核验”，不得概括成三个动作固定三次调用
  `verify_candidates_online`。prepare结果中的`scan_method`和
  `verification_mode`是本次实际执行方式。
- 在线楼中楼数据不完整、需要验证码或网络核验失败时立即停止。
- `error_type=verification_required`表示需要用户操作，不是普通冷却重试：
  AI必须停止自动重试。若错误同时包含`type=unknown`和`uuid=unknown`，
  这是没有可见挑战信息的API接口风控，浏览器页面正常也可能发生；先重新
  导入Firefox Cookie并重跑当前action，仍失败则等待风控解除。
- 候选已定位时只严格补全候选所在楼层；该楼层补拉后数量仍少于平台
  `sub_comment_count`时硬停止。
- 候选尚未定位时才按需搜索其他不完整楼层。候选最终在完整楼层中找到后，
  确定无关楼层的拉取失败不阻断整批；仍未找到且存在不完整数据时硬停止。
- 通知中的`target_comment_id`会保留为内部楼层定位线索，不在用户表格显示。
- 带令牌楼中楼helper普通故障时才回退原生`sub-comments`；验证码或
  `verification_required`必须立即停止，不用第二种传输重复请求。严格核验
  会保留真实错误，不再静默返回空列表。
- 不得使用内联数据猜测回复状态。
- `scan.json`候选不等于可回复，必须继续通过本地状态和在线复核。
- 在线请求或完整性核验失败时硬停止且不归档；只有明确判定为平台已回复
  或不存在的候选，才在发送阶段标记`archived`。
- 默认`prepare`达到6页快速定位预算不是请求失败；未定位深层楼中楼只计入
  `deferred_count`并排除在本批之外。
- “硬停止不归档”不等于完全不写本地状态：`prepare`和`draft`开始时仍会
  清空旧`active_comment_ids`，防止误发旧预览。
- 没有持续运行日志统计时，不得根据一两次执行声称某类错误“最常见”或
  推断各种失败的发生比例。
- 所有发送失败的评论自动加入排除列表。
- 同一笔记的`ai-reply`动作由跨进程锁串行执行；`workflow_busy`表示已有
  进程正在处理，必须等待，不能并行启动第二个命令。
- 每条平台写请求前先保存`send_status=sending`。进程中断后程序先在线
  对账；返回`uncertain_send_state`时必须交给用户处理，禁止自动重发。
- `--confirmed`还必须与当前`batch_id`和`preview_hash`同时匹配，防止
  其他AI在用户确认后替换草稿。
- 不输出Cookie、`xsec_token`或其他登录凭据。
- `.cache/xsec_index.json`是敏感令牌缓存，不是回复工作流状态。
- 浏览器可正常使用、账号身份检查成功，不代表楼中楼API没有独立风控。
- 发布文章和发送回复前都必须先预览并取得确认。
- `articles`对平台是只读操作，但可能更新本地0600敏感`xsec_index.json`。
- 传统`send --resume`只改变提示文案；终态和排除过滤无论是否添加该参数
  都始终执行。

## 验证程序修改

```bash
python3 -m unittest discover -s tests
python3 -m py_compile main.py lib/*.py
python3 main.py ai-help | python3 -m json.tool
```

修改命令、JSON字段、默认范围或状态规则后，同步更新`AGENTS.md`、`README.md`、
`SKILL.md`和`references/commands.md`。
