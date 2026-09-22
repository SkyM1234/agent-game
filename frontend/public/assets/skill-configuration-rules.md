# 技能配置限制

此文档对应 `backend/game/models.py` 中的 `SkillSpec`、`CharacterTool` 和 `GameConfig` 校验，以及引擎中的实际执行规则。修改 `configs/characters/*/character.json` 或 `tools.json` 后，可按这里核对。

## 所有技能

- `skills` 必须包含且仅包含 `jab`、`heavy_punch`、`kick`、`guard`、`dash`、`move`、`rest` 七项。
- `active_ms` 必须是正整数；`windup_ms`、`recovery_ms` 必须是非负整数。这三个时长都必须是 `step_ms` 的整数倍，默认步长为 50 ms。
- 决策锁定时长为 `windup_ms + recovery_ms`，不包含 `active_ms`。技能效果从前摇结束后持续 `active_ms`；锁定结束后可以启动新动作，尚未结束的旧效果继续并行结算。
- `cooldown_turns` 必须是非负整数；`stamina_cost`、`mana_cost`、`reach`、`health_damage`、`projectile_speed`、`speed` 必须是非负数。
- `stamina_restore` 和 `mana_restore` 只能用于 `rest`，可为 `null` 或非负数；其他技能必须保持 `null`。
- 每项技能只能使用 `SkillSpec` 定义的字段，不能附加未知键；数值不能为 `NaN` 或无穷大。`teleport` 是布尔值，`display_name` 可为文本或 `null`。

## 攻击技能

| 技能键 | 类型限制 |
| --- | --- |
| `jab`、`heavy_punch`、`kick` | 仅这三种技能可以设置正数 `projectile_speed`。 |
| 弹道攻击 | `projectile_speed × active_ms / 1000 ≥ reach`。有效阶段内必须有足够时间飞满配置射程。 |
| 近战攻击 | 生效时，对手必须位于面向方向，且双方中心距离不超过 `reach`。 |

冰弹若保持 `projectile_speed=8`、`active_ms=600`，最大合法 `reach` 是 4.8。要设为 5，可把 `active_ms` 调到 650（符合默认 50 ms 步长），或在保持 600 ms 时把速度调到至少 8.334。配置合法也不保证命中：目标在前摇或有效阶段移动，仍可能躲开攻击。

## 移动、传送与恢复

- 只有 `dash` 可以设置 `teleport=true`；传送时 `speed=0`，`windup_ms` 可设为 0（立即传送）或符合步长的非负整数（前摇结束后传送），其工具参数必须为数值型 `position`。普通 `dash` 和 `move` 使用 `direction`。
- 传送落点必须位于场地内，且与对手中心至少相隔 `2 × fighter_radius`。默认场地宽 12、角色半径 0.4，因此横坐标范围为 0.4 至 11.6，不能落在对手中心左右 0.8 的重叠区。双方传送落点互相重叠时，两次动作都会退回 `rest`。
- `move` 和 `rest` 必须保持 `stamina_cost=0`、`mana_cost=0`。`move` 必须保持 `cooldown_turns=0`；`rest` 可以设置非负整数冷却回合数。
- `rest` 的 `stamina_restore`、`mana_restore` 表示整个有效阶段的恢复总量；为 `null` 时使用全局每秒恢复速度乘以 `active_ms / 1000`。恢复不会超过角色资源上限。

## `tools.json` 对应关系

- `tools` 必须为七项引擎技能各定义一个工具；工具名与对应的 `action` 都不能重复。工具名以小写英文字母开头，其余字符只能是小写字母、数字或下划线，总长度不超过 64。
- `move` 和普通 `dash` 的参数必须是 `direction`，可用值是 `forward`、`backward` 的非空子集；传送 `dash` 的参数必须是数值型 `position`。其余技能不能添加参数。
- 工具 `description` 长度为 1 至 1000 字符；角色工具配置的 `version` 长度为 1 至 80 字符，`prompt.md` 内容长度为 1 至 8000 字符。

## 角色级配置

- `character_id` 必须与所在目录的角色 ID 一致。角色的 `max_health`、`max_stamina`、`max_mana` 若有设置，必须大于 0；未设置时使用对局的全局资源上限。
- 对局配置中的 `decision_ms` 和 `time_limit_ms` 也必须是 `step_ms` 的整数倍，否则整个配置无法加载。

## 对战中的可用性

上述限制用于加载配置。对战过程中，配置合法的技能仍会因体力或魔力不足、冷却尚未结束、动作锁定尚未解除而暂时不可用。技能开始时扣除资源，冷却按双方同时决策的回合计算；`windup_ms + recovery_ms` 经过后即可开始新动作，`active_ms` 不阻止决策。
