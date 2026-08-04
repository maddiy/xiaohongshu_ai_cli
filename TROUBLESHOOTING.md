# 小红书AI智能运营系统：常见错误排查

先运行环境检查和状态摘要：

```bash
python3 main.py doctor --json
python3 main.py ai-reply --note-id <笔记ID> --action status
```

| 错误类型 | 含义 | 正确处理 |
|---|---|---|
| `workflow_busy` | 同一笔记已有动作运行 | 等当前进程结束后重试，不要并行启动 |
| `verification_required` | 楼中楼接口触发验证码或独立风控 | 停止自动重试；重新导入Firefox Cookie，仍失败则等待风控解除 |
| `invalid_reply_map_json` | 兼容映射文件不是合法JSON | 优先改用`--action map`；不要全文件替换英文双引号 |
| `invalid_reply_map_mapping` | 动作、回复或三项审查结构不合法 | 按返回的`details`修复，联网前即可完成 |
| `duplicate_send_mapping` | 同一用户相同正文被设为多条send | 只保留一条send，其余设为skip |
| `stale_preview` | 提交的批次号或指纹不是当前预览 | 重新运行draft、展示预览并取得确认 |
| `preview_content_changed` | 用户确认后草稿实际内容变化 | 重新draft并重新确认，不能沿用旧确认 |
| `uncertain_send_state` | 进程中断后平台结果无法确定 | 人工在线核对；禁止自动重发或用retry重置 |
| `retry_confirmation_required` | 重试失败评论但没有用户授权 | 先向用户说明失败原因并取得明确授权 |
| `audit_unavailable` | 开始事件无法安全写入 | 修复本地状态权限或损坏后再执行；业务动作未运行 |
| `session_error` | 持久helper异常结束或响应超时 | helper已被终止；检查脱敏诊断，修复后从安全状态接续 |
| `watch_already_running` | 相同过滤条件已有监控进程 | 使用现有监控或先手动停止，不要启动重复进程 |
| `watch_error` | 监控参数、通知读取或本地状态异常 | 按错误修复后重新手动启动；程序不会后台重试 |

其他注意事项：

- 浏览器网页正常不代表楼中楼 API 没有独立风控。
- `scan.json`候选不等于可以回复；draft和send仍会按条件在线核验。
- `status`不会返回可直接发送的确认绑定。活动批次要继续时，重新运行draft
  展示预览；内容完全相同会自动复用原绑定。
- `failed`只能用显式授权的retry重置；`sending`必须先人工对账。
- SQLite当前DB schema为v2；主库、WAL和SHM都应为0600。高版本数据库必须
  使用相应新版程序打开，不得手工下调版本号。
- watch首次运行只建立基线；检查点存在不代表监控仍在运行。自动回复必须
  同时提供`--auto-reply --confirmed`，验证码、限流或结果不确定时停止。
- Web页面只允许通过`127.0.0.1`访问。页面令牌无效时刷新页面，不要关闭
  CSRF校验或将服务改成公网监听。
- 修改程序后运行`python3 scripts/verify.py`；成功时会输出紧凑摘要。
