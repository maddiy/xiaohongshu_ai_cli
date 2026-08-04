# 小红书AI智能运营系统：快速上手

所有命令在项目根目录执行。首次使用先检查环境并读取当前协议：

```bash
python3 main.py doctor --json
python3 main.py ai-help --summary
```

登录默认读取 Firefox Cookie：

```bash
python3 main.py login
```

查看最新文章和评论：

```bash
python3 main.py articles --json
python3 main.py comments --json
```

手动开启新评论监控或本地Web控制台：

```bash
# 默认只发现新评论；首次运行只建立基线，Ctrl+C停止
python3 main.py watch

# 指定文章或指定用户
python3 main.py watch --note-id <笔记ID>
python3 main.py watch --user <精确昵称或用户ID>

# 本地网页：http://127.0.0.1:8765
python3 main.py web
```

自动回复必须由用户明确授权，同时使用`--auto-reply --confirmed`。可通过
`--reply-text`指定固定回复；省略时使用通用话术。watch不调用外部大模型，
需要逐条分析和针对性回复时继续使用下面的AI工作流。

AI 回复一篇笔记的标准流程：

```bash
# 1. 准备候选；默认只看最新20条评论通知
python3 main.py ai-reply --note-id <笔记ID> --action prepare

# 2. 按prepare返回的candidate_index逐条写入审查和回复
python3 main.py ai-reply --note-id <笔记ID> --action map \
  --candidate-index <候选序号> --decision send --reply-text '<回复>' \
  --logic-verdict partly_sound --logic-reason '<逻辑依据>' \
  --fact-verdict unverifiable --fact-reason '<核查依据>' \
  --boast-verdict none --boast-reason '<判定依据>'

# 3. 生成审查表和草稿表
python3 main.py ai-reply --note-id <笔记ID> --action draft

# 4. 仅在用户明确确认展示的草稿后发送
python3 main.py ai-reply --note-id <笔记ID> --action send \
  --confirmed --batch-id <draft返回值> --preview-hash <draft返回值>
```

新 AI 接续已有任务时，先读取不含发送确认值的状态摘要：

```bash
python3 main.py ai-reply --note-id <笔记ID> --action status
```

失败评论默认不重试。只有用户明确授权后，才可重置单条失败终态：

```bash
python3 main.py ai-reply --note-id <笔记ID> --action retry \
  --comment-id <评论ID> --retry-authorized
```

重置后仍须重新完成prepare、map、draft、用户确认和send。完整参数以
`python3 main.py ai-help --command ai-reply`为准；常见错误见
`TROUBLESHOOTING.md`。
