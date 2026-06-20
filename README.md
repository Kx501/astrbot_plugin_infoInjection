# InfoInjection

AstrBot 插件：在 **LLM 请求前**（`on_llm_request`）按 `rules.json` 注入或改写上下文。

## 执行频率 `schedule`

| 值 | 说明 |
|----|------|
| `always` | 每次 LLM 请求都执行（如包装用户消息） |
| `daily` | 每个会话每天仅执行一次（默认，用于日期等日更信息） |

同一请求内先跑 `always` 规则，再视情况跑 `daily` 规则。

## 日更注入

`schedule: daily` 的规则在每个会话（`session_id`，否则回退 `umo`）**每天只执行一次**：

1. 读取 KV 中该会话的上次注入日期
2. 若与今日相同 → 跳过该规则
3. 跨日 → 执行并写入今日日期

`schedule: always` 不受此限制（例如每条用户消息包装为 `<msg>`）。

建议关闭 AstrBot 全局 `datetime_system_prompt`，避免每轮 system 变动破坏缓存。

### 插入位置

| `position` | 写入位置 | 说明 |
|------------|----------|------|
| `message_replace` | 替换整段 `req.prompt` | 用于 `<msg user="…" id="…">…</msg>` 等格式 |
| `system_end` | `system_prompt` 末尾 | 日更信息推荐 |
| `message_start` | 用户 `prompt` 开头 | system 前缀稳定 |
| `system_start` | `system_prompt` 开头 | |
| `message_end` | `extra_user_content_parts` | 支持 `ephemeral` |

## 安装

将本目录放入 AstrBot 的 `data/plugins/` 并启用。首次运行会在插件数据目录生成 `rules.json`。

## 规则示例

### 每条用户消息包装为 `<msg>`

```json
{
  "id": "wrap_user_msg",
  "enabled": true,
  "schedule": "always",
  "priority": 100,
  "when": { "chat": "any" },
  "inject": {
    "position": "message_replace",
    "template": "<msg user=\"{{user}}\" id=\"{{id}}\">{{content}}</msg>"
  }
}
```

效果：`忘崽你会画画吗` → `<msg user="老王" id="22323xxx">忘崽你会画画吗</msg>`

- `{{user}}` / `{{id}}` / `{{content}}` 已做 XML 转义，可直接用于属性与正文
- `{{user_nickname}}` / `{{user_id}}` / `{{user_message}}` 为原始值，需自行转义

### 日更日期

```json
{
  "id": "daily_date",
  "schedule": "daily",
  "inject": {
    "position": "system_end",
    "template": "<dynamic_context>\n今日日期：{{date}}\n星期：{{weekday}}\n</dynamic_context>"
  }
}
```

### `when` 条件

| 字段 | 说明 |
|------|------|
| `chat` | `any` / `private` / `group` |
| `user_ids` | 用户白名单 |
| `group_ids` | 群白名单 |
| `message_regex` | 用户本轮输入正则 |
| `message_contains` | 用户输入需包含的子串 |

### `inject`

| 字段 | 说明 |
|------|------|
| `position` | 见上表，必填 |
| `ephemeral` | 仅 `message_end` 有效 |
| `template` | 支持 `{{date}}` `{{weekday}}` `{{user}}` `{{id}}` `{{content}}` 等 |

## 持久化

日更记录：KV 键 `daily_inject_dates`（`{session_key: "YYYY-MM-DD"}`）。

## 与 MsgDebugger 联动

注入后按 MsgDebugger 插件 README「插件集成：Injection Trace 约定」写入 `event.extra["_md_injection"]`（含 `source`、`rule_ids`、`blocks` 等）。

## 本地调试

```bash
cd infoInjection
python -c "from engine import *; from pathlib import Path; ctx=EvalContext(user_message='忘崽你会画画吗', user_id='22323xxx', user_nickname='老王'); print(evaluate_rules(load_rules_from_path(Path('sample_rules.json')), ctx, schedule='always'))"
```
