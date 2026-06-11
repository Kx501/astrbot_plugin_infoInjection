# InfoInjection

AstrBot 插件：在 **LLM 请求前**（`on_llm_request`）按 `rules.json` 注入上下文。

## 用户消息格式

每次 LLM 请求会将本轮 `prompt` 包装为：

```xml
<msg user="老王" id="22323xxx">忘崽你会画画吗</msg>
```

- `user`：发送者昵称（`event.get_sender_name()`）
- `id`：发送者 ID（`event.get_sender_id()`）
- 正文：原始用户输入；属性与正文中的 `&` `<` `>` `"` 会转义
- 已是该格式时不会重复包装（避免工具循环重入时叠层）

规则条件 `when` 仍按**包装前**的原文匹配；`{{user_message}}` 等占位符也用原文。

## 日更注入

每个会话（`session_id`，否则回退 `umo`）**每天只注入一次**：

1. 读取 AstrBot KV 中该会话的上次注入日期
2. 若与今日（按 AstrBot 全局 `timezone`）相同 → 跳过，不修改 LLM 请求
3. 若日期已更新 → 执行规则注入，并写入今日日期

同日后续轮次本插件不再修改请求体，有利于 provider 侧 prompt 缓存。

建议关闭 AstrBot 全局「每轮附带当前时间」类选项（如 `datetime_system_prompt`），避免每轮 system 变动破坏缓存。

### 插入位置与缓存

| `position` | 写入位置 | 缓存友好度 | 说明 |
|------------|----------|------------|------|
| `system_end` | `system_prompt` 末尾 | **高** | 人格等长前缀不变，变动集中在 system 尾部（**默认推荐**） |
| `message_start` | 用户 `prompt` 开头 | **高** | system 整段保持稳定，变动在用户消息块最前 |
| `system_start` | `system_prompt` 开头 | 低 | 改动靠前，后续 system 前缀难以命中缓存 |
| `message_end` | `extra_user_content_parts` | 中 | 在用户正文之后追加；支持 `ephemeral` / `mark_as_temp()` |

`position` 必填，且只能是上述四个值之一。

## 安装

将本目录放入 AstrBot 的 `data/plugins/` 并启用。首次运行会在插件数据目录（`data/plugin_data/astrbot_plugin_info_injection/`）生成 `rules.json`。

## 规则文件

编辑插件数据目录下的 `rules.json`，保存后按文件 mtime 热加载。无插件配置项；时区跟随 AstrBot 全局设置。

### 示例

```json
{
  "id": "daily_date",
  "enabled": true,
  "priority": 10,
  "when": { "chat": "any" },
  "inject": {
    "position": "system_end",
    "ephemeral": false,
    "template": "<dynamic_context>\n今日日期：{{date}}\n星期：{{weekday}}\n</dynamic_context>"
  }
}
```

### `when` 条件

| 字段 | 说明 |
|------|------|
| `chat` | `any` / `private` / `group` |
| `user_ids` | 用户白名单，空=不限制 |
| `group_ids` | 群白名单，空=不限制 |
| `message_regex` | 用户本轮输入正则 |
| `message_contains` | 用户输入需包含的子串 |

### `inject` 注入

| 字段 | 说明 |
|------|------|
| `position` | `system_start` / `system_end` / `message_start` / `message_end` |
| `ephemeral` | 仅 `message_end` 有效；为真时不写入会话历史 |
| `template` | `{{date}}` `{{weekday}}` `{{user_name}}` `{{user_id}}` `{{group_id}}` `{{user_message}}` 等 |

日更 + 需当天后续轮次仍记得注入内容：优先 `message_start` 或 `message_end` 且 `ephemeral: false`（写入会话历史）。`system_end` 仅影响当日首条请求的 system，后续轮次 system 不再携带该段。

## 持久化

注入日期记录通过 AstrBot KV 存储，键名 `daily_inject_dates`（`{session_key: "YYYY-MM-DD"}`）。

## 与 MsgProcessor 联动

注入后写入 `event.extra["_ii_injected"]`，含 `date`、`session_key`、`rule_ids` 等，供出站处理插件读取。

## 本地调试

```bash
cd infoInjection
python -c "from engine import *; from pathlib import Path; print(evaluate_rules(load_rules_from_path(Path('sample_rules.json')), EvalContext()))"
```
