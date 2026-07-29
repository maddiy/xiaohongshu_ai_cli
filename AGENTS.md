# AI 执行指南

- 系统名称：`小红书AI智能运营系统`
- 命令行程序：`小红书AI智能运营系统`

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

## 查看文章

```bash
python3 main.py articles --json
```

向用户按返回的`columns`显示Markdown表格，必须显示标题。

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

### 3. 生成并展示草稿

```bash
python3 main.py ai-reply --note-id <笔记ID> --action draft
```

使用Markdown表格向用户展示返回的`preview`，然后等待明确确认。
`draft`开始时也会停用旧`active_comment_ids`，但不会删除历史草稿和终态；
成功后才把本次候选设为新的活动批次。

### 4. 确认后发送

```bash
python3 main.py ai-reply \
  --note-id <笔记ID> \
  --action send \
  --confirmed
```

没有用户明确确认时禁止添加`--confirmed`。发送前程序会再次在线核验。
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

状态优先级：

1. `drafts.send_status=sent`
2. `drafts.send_status=failed`
3. `drafts.send_status=archived`或存在于`skipped.json`
4. 平台显示已回复或评论已删除
5. 本次扫描候选（仅进入后续资格判断，不代表可以回复）

`sent`、`failed`、`archived`均为默认终态，不得重新发送。
`failed`重试需要用户明确授权，同时移出跳过列表并重置草稿中的失败终态；
只运行`skipped --remove`不够。

## 安全边界

- 在线楼中楼数据不完整、需要验证码或网络核验失败时立即停止。
- 补拉楼中楼后数量仍少于平台`sub_comment_count`时抛异常硬停止，不使用
  部分数据继续判断。
- 在线核验会展开本次查询返回的所有不完整楼层，因为内联数据不足时无法
  预先定位候选或作者回复所在楼层；这不表示回复关系可以跨楼层。
- 不得使用内联数据猜测回复状态。
- `scan.json`候选不等于可回复，必须继续通过本地状态和在线复核。
- 在线请求或完整性核验失败时硬停止且不归档；只有明确判定为平台已回复
  或不存在的候选，才在发送阶段标记`archived`。
- 所有发送失败的评论自动加入排除列表。
- 不输出Cookie、`xsec_token`或其他登录凭据。
- `.cache/xsec_index.json`是敏感令牌缓存，不是回复工作流状态。
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
