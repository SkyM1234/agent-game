# 风险加权反事实推演与收益矩阵说明

本文说明风险加权反事实推演 Agent 如何生成收益矩阵、每个评分参数的含义，以及 DeepSeek 实际收到的 `counterfactual_analysis` 数据。页面中的策略名称为“反事实推演”，完整算法名称为“风险加权反事实推演”（Risk-Weighted Counterfactual Rollout）。

纯代码策略 ID 为 `counterfactual`，结合模型的策略 ID 为 `counterfactual_deepseek`。风险加权结果统一使用 `risk_weighted_score`，代码推荐动作使用 `rollout_recommendation`。

当前实现位于 [backend/agents/planning.py](../backend/agents/planning.py)。战斗事实由 [backend/game/engine.py](../backend/game/engine.py) 的权威规则引擎计算；LLM 不生成伤害、距离、命中结果或收益分数。

## 1. 设计目标

反事实推演不预测“对手下一步最可能使用什么技能”，而是回答另一个问题：

> 如果己方选择动作 A，对手在当前所有合法动作中分别选择 B1、B2、...，各分支会产生什么结果？在不知道对手选择概率时，哪个己方动作对最坏情况更稳健，同时平均表现也不差？

规划器承担以下职责：

1. 从当前战场状态获取双方合法动作。
2. 为每个“己方动作 × 对手动作”组合克隆独立战场。
3. 使用真实引擎推进分支，结算移动、弹道、伤害、护盾、资源和道具。
4. 从分支截止状态提取指标并计算代码收益。
5. 汇总每个己方动作的最坏、平均和最好收益。
6. 向反事实推演 Agent 提供最终动作，或向 LLM 提供压缩后的客观分析。

LLM 仍负责人物风格、战术意图和最终工具选择，但不负责填写矩阵数值。

## 2. 收益矩阵如何构造

设当前完整战场状态为 `S`：

- 己方当前合法动作集合为 `A = {a1, a2, ..., am}`。
- 对手当前合法动作集合为 `B = {b1, b2, ..., bn}`。
- 一个矩阵单元 `M(i, j)` 表示从 `S` 克隆出的分支同时提交 `ai` 和 `bj` 后，推进到规划截止点得到的代码收益。

```text
M(i, j) = Score(Rollout(Clone(S), ai, bj))
```

矩阵不会直接完整发送给 LLM。完整矩阵按回合绘制在 `data/logs/<replay_id>/turn-<回合号>.log` 的 `PAYOFF MATRIX` 表格中，不会写入数据库或最终 JSONL；同一日志分别展示实际发送给模型的相对偏好，以及仅供诊断的完整内部引擎评估。代码先按每一行汇总：

```text
worst(ai) = min_j M(i, j)
mean(ai)  = average_j M(i, j)
best(ai)  = max_j M(i, j)
```

如果对手在当前决策点仍被动作锁定，对手动作集合不是重新猜测一个技能，而是 `[continue]`。该分支继续模拟对手已经在场上的动作和效果。

## 3. 分支推进与 500ms 决策边界

每个分支至少调用一次 `Arena.advance()`，每次最多推进一个 `decision_ms`。当前默认值为 500ms，内部按 `step_ms = 50ms` 结算。

第一次推进后，如果双方都不能决策，规划器继续按完整回合推进，直到满足任一条件：

- 任意一方到达新的决策点；
- 对局结束；
- 分支推进时间达到 `max_rollout_ms`，默认 5000ms。

动作锁定时间为：

```text
lock_ms = windup_ms + recovery_ms
```

`active_ms` 不加入锁定时间，但决策只能发生在离散的 500ms 回合边界：

| 锁定时间 | 再次可决策的时间 |
| --- | --- |
| 150ms | 500ms |
| 500ms | 500ms |
| 600ms | 1000ms |

规划截止点是“任意一方的下一次决策事件”，不一定是己方再次可决策。例如己方锁定 600ms、对手锁定 150ms 时，分支在 500ms 截止，此时对手有决策权，己方仍被锁定。

如果技能的生效期长于锁定期，新动作开始时旧动作会进入 `lingering`，继续参与移动、弹道、命中和防御结算。克隆会保留：

- 当前 `active` 动作；
- 所有 `lingering` 动作；
- 弹道起点、朝向、已飞行时间和是否已经命中；
- 双方位置、资源、护盾、冷却和道具库存；
- 当前回合、模拟时间和比赛结果。

## 4. 合法动作如何枚举

规划器读取 `Arena.available_tools(player)`，因此人物、武器、资源、冷却和道具变化会自动反映在候选集中。

| 工具类型 | 生成方式 |
| --- | --- |
| 无参数技能 | 生成一个动作，例如攻击、格挡、休息 |
| 方向技能 | 为工具允许的每个 `direction` 生成动作，例如 `forward`、`backward` |
| 主动道具 | 为每个当前可用的 `item_id` 生成 `use_item` 动作 |
| 传送 | 从连续合法区间中生成最多 5 个有战术意义的落点 |

传送候选会考虑：

- 合法区间的左右边界和中点；
- 当前坐标；
- 根据己方各攻击射程生成的接战位置；
- 越过对手后、刚好不发生重叠的位置。

候选经过合法性校验、去重，并按离当前位置的距离排序，最终最多保留 5 个。因此传送不是穷举连续空间中的每一个浮点坐标。规划器会完整评估这些落点，再按与最终选招相同的风险加权规则只保留得分最高的落点。纯反事实 Agent 和发送给 LLM 的候选中都只出现这一个传送动作。

## 5. 分支指标 `BranchMetrics`

以下指标均从分支开始前和截止后的真实快照计算。

| 字段 | 计算或含义 |
| --- | --- |
| `damage_dealt` | `对手开始生命 - 对手截止生命`；对手恢复生命时可能为负数 |
| `damage_received` | `己方开始生命 - 己方截止生命`；己方恢复生命时可能为负数 |
| `stamina_delta` | `己方截止体力 - 己方开始体力` |
| `mana_delta` | `己方截止魔力 - 己方开始魔力` |
| `shield_delta` | `己方截止护盾 - 己方开始护盾` |
| `distance_before` | 分支开始时双方中心距离，仅用于说明，不直接进入收益公式 |
| `distance_after` | 分支截止时双方中心距离，仅用于说明，不直接进入收益公式 |
| `position_value_delta` | 己方位置价值的变化 |
| `cooldown_burden_delta` | 己方剩余冷却负担的变化 |
| `pending_effect_delta` | 己方相对未结算攻击压力的变化 |
| `initiative` | 截止点的决策权差值，取 `-1`、`0` 或 `1` |
| `elapsed_ms` | 该分支实际推进的模拟时间 |
| `ready_players` | 截止点能够提交新决策的角色 |
| `ongoing_actions` | 双方 `active + lingering` 动作数量 |
| `items_spent` | 分支内己方消耗的道具及数量 |
| `opponent_items_spent` | 分支内对手消耗的道具及数量 |
| `rejected_actions` | 分支内己方动作被引擎拒绝的次数 |

### 5.1 位置价值

当前实现将己方所有能造成生命伤害的攻击中最大射程记为 `preferred_range`：

```text
position_value = -abs(current_distance - preferred_range)
position_value_delta = position_value_after - position_value_before
```

越接近最大攻击射程，位置价值越高。该值是终点评分启发式，不会改变引擎的真实命中判定。

### 5.2 冷却负担

```text
cooldown_burden = sum(max(0, cooldown_until_turn - current_turn_index))
cooldown_burden_delta = burden_after - burden_before
```

使用长冷却技能会增加负担；已有冷却随回合减少则会降低负担。

### 5.3 未结算攻击压力

规划器遍历角色所有尚未结算命中的 `active` 和 `lingering` 攻击。单个攻击的压力为：

```text
attack_pressure = health_damage * phase_factor * spatial_factor
```

阶段因子：

| 阶段 | `phase_factor` |
| --- | ---: |
| 前摇 `windup` | 0.35 |
| 生效 `active` | 0.70 |
| 后摇 `recovery` | 0 |

空间因子使用攻击朝向、当前中心距离和射程：

```text
signed_distance < 0: spatial_factor = 0
signed_distance <= reach: spatial_factor = 1
reach < signed_distance < 2 * reach: 从 1 线性下降到 0
signed_distance >= 2 * reach: spatial_factor = 0
```

相对压力为：

```text
pending_balance = own_pending_pressure - opponent_pending_pressure
pending_effect_delta = balance_after - balance_before
```

真实命中仍由引擎的固定步模拟决定。该压力只估计在规划截止时还没结算完的未来威胁，尤其不等同于命中概率。

### 5.4 下次决策权

```text
initiative = is_own_ready - is_opponent_ready
```

| 截止状态 | `initiative` |
| --- | ---: |
| 只有己方可决策 | 1 |
| 双方都可决策 | 0 |
| 双方都不可决策 | 0 |
| 只有对手可决策 | -1 |

通常分支会在任意一方可决策时停止，因此“双方都不可决策”主要可能出现在达到最大规划时长的情况。

## 6. 单个矩阵分支的收益公式

默认权重定义在 `UtilityWeights`：

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| `damage_dealt` | 1.00 | 奖励对手生命下降 |
| `damage_received` | 1.20 | 惩罚己方生命下降；生命安全略高于等量输出 |
| `stamina` | 0.06 | 奖励己方体力净增加 |
| `mana` | 0.09 | 奖励己方魔力净增加 |
| `shield` | 0.65 | 奖励己方护盾净增加 |
| `position` | 0.45 | 奖励接近期望攻击距离 |
| `cooldown` | 0.80 | 惩罚新增冷却负担 |
| `pending_effect` | 0.55 | 奖励己方相对未结算攻击压力 |
| `initiative` | 1.50 | 奖励在截止点先获得决策权 |
| `item_reserve` | 0.25 | 保留一次性道具价值的比例 |
| `rejection` | 20.00 | 惩罚己方动作被拒绝或回退 |
| `risk_aversion` | 0.70 | 汇总动作时最坏收益所占比例，不直接进入单分支收益 |

不考虑终局和道具时，单分支基础收益为：

```text
score =
    damage_dealt             * 1.00
  - damage_received          * 1.20
  + stamina_delta            * 0.06
  + mana_delta               * 0.09
  + shield_delta             * 0.65
  + position_value_delta     * 0.45
  - cooldown_burden_delta    * 0.80
  + pending_effect_delta     * 0.55
  + initiative               * 1.50
  - rejected_actions         * 20.00
```

由于 `damage_received` 在治疗后可以为负数，`- damage_received * 1.2` 会自然奖励己方生命恢复。对手恢复生命时，`damage_dealt` 为负数，会自然降低收益。

### 6.1 道具机会成本

单件道具的保留价值为：

```text
item_value =
    health_restore
  + stamina_restore              * 0.06
  + mana_restore                 * 0.09
  + shield                       * 0.65
  + cooldown_reduction_turns     * 5.00
  + backward_move                * 1.50
```

分支收益再进行以下调整：

```text
score -= own_items_spent_value     * item_reserve
score += opponent_items_spent_value * item_reserve
```

这意味着使用道具既获得分支内的即时效果，也付出失去未来库存的机会成本；对手消耗有限道具则成为己方的长期收益。

### 6.2 终局收益

| 分支结果 | 调整 |
| --- | ---: |
| 己方获胜 | `+10000` |
| 己方失败 | `-10000` |
| 平局或分支未结束 | `0` |

终局值远大于普通资源变化，确保能直接获胜的动作优先，能避免直接失败的动作不会因局部资源优势被错误覆盖。

## 7. 从矩阵选择动作

对每个己方动作 `a`：

```text
risk_weighted_score(a) = risk_aversion * worst(a)
                       + (1 - risk_aversion) * mean(a)
```

默认 `risk_aversion = 0.7`：

```text
risk_weighted_score(a) = 0.7 * worst(a) + 0.3 * mean(a)
```

传送的多个采样落点先使用同一排序规则折叠为一个最佳传送动作，随后再与攻击、格挡、移动、休息和道具等动作比较。最终选择 `risk_weighted_score` 最大的动作。分数相同时依次使用平均收益和确定性的动作键排序，保证同一状态能稳定复现。

这里没有给对手动作分配概率。`mean_score` 是所有当前合法应对的简单算术平均，不代表模型预测的对手策略分布；`worst_score` 则表示当前动作面对最不利合法应对时的结果。

## 8. 发送给 LLM 的结果

完整动作组合矩阵会在 `analyze()` 期间计算。当前 `RolloutAnalysis.evaluations` 保留每个非位置动作和最佳传送落点的行汇总、最坏应对和最坏分支指标；其他传送落点与矩阵单元在本次分析结束后不会写入回放。

完整行汇总供纯代码策略、日志和决策元数据使用，包含推荐动作、原始风险加权收益、第二名分差与终局状态。发送给 LLM 前会生成另一份去锚定摘要：删除推荐动作和原始效用，把候选转换为当前集合内的 0～100 `preference`，再按动作名而非收益排序。只有引擎排名前三的动作在 `detailed_worst_cases` 中带精简的最坏分支指标。下面是模型实际收到的结构节选：

```json
{
  "generated_by": "engine_counterfactual_rollout",
  "horizon": "next_decision_event",
  "higher_preference_is_better": true,
  "preference_is_relative_to_current_candidates": true,
  "risk_aversion": 0.7,
  "candidates": [
    {
      "action": {
        "skill": "blade_walk",
        "direction": "backward"
      },
      "preference": 73.4,
      "worst_response": {
        "skill": "ice_bolt"
      },
      "terminal_status": "none"
    }
  ],
  "detailed_worst_cases": [
    {
      "action": {
        "skill": "blade_walk",
        "direction": "backward"
      },
      "metrics": {
        "damage_dealt": 0.0,
        "damage_received": 0.0,
        "distance_after": 7.0,
        "position_value_delta": -1.0,
        "pending_effect_delta": -5.687
      }
    }
  ]
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `generated_by` | 固定为 `engine_counterfactual_rollout`，表明数值来自代码模拟 |
| `horizon` | 固定为 `next_decision_event` |
| `higher_preference_is_better` | 固定为 `true`；偏好值越高越优先 |
| `preference_is_relative_to_current_candidates` | 固定为 `true`；偏好值不能跨回合比较，也不是胜率 |
| `risk_aversion` | 最坏收益在稳健分数中的权重 |
| `candidates` | 折叠最佳传送落点后的全部己方动作汇总 |
| `action` | 当前候选己方动作 |
| `preference` | 当前候选集合内归一化后的 0～100 相对偏好 |
| `worst_response` | 代码评估中产生最差分支的对手动作 |
| `terminal_status` | 当前候选所有应对分支的终局概况 |
| `terminal_outcomes` | 存在终局分支时，各类终局与未结束分支的数量 |
| `detailed_worst_cases` | 排名前三动作的精简最坏分支指标；零值字段通常省略 |

在人设配置存在时，`model_observation()` 会在发送请求前转换技能名：

- 己方 `candidates[].action` 和 `detailed_worst_cases[].action` 使用己方公开工具名，例如 `blade_walk`。
- `worst_response` 使用对手公开工具名，例如 `ice_bolt`。
- `use_item` 保持通用工具名。
- 对手当前不能决策时使用 `{ "skill": "continue" }`。

双方人物、武器修正后的有效技能、资源上限和道具定义位于稳定的 system `match_context`；每回合 user JSON 发送动态状态、尚未结算的来袭攻击、反事实偏好和最近 4 个决策周期的增量。窗口之前的记录增量汇总到 `opponent_history`：`action_counts`、`direction_counts`、`item_counts`、`outcome_counts` 和 `health_damage_dealt` 保存对手的历史频率，不保留事件顺序，也不作为下一步动作的确定预测。历史战术摘要与不可用工具列表不会回填。

若最高收益动作面对所有合法应对都产生相同的击倒终局，`counterfactual_deepseek` 会直接执行该动作，不再调用模型。时间到判定和普通分差不会跳过模型。

## 9. 反事实推演 Agent 与 DeepSeek 的区别

| 路径 | 最终动作由谁选择 | 是否调用 LLM |
| --- | --- | --- |
| `counterfactual` | 直接选择代码中 `risk_weighted_score` 最大的动作 | 否 |
| `counterfactual_deepseek` | LLM 阅读代码分析后调用一个合法人物工具 | 是 |

两条路径使用同一个 `CounterfactualRollout` 和同一套默认权重。回放的 `DecisionInfo` 会记录：

- `rollout_recommendation`：代码推荐动作；
- `risk_weighted_score`：推荐动作的风险加权收益；
- `selected_tool`：实际提交的公开工具名；
- `source`：`counterfactual`、`llm` 或失败时的 `fallback`。

## 10. 当前限制与调参注意事项

当前实现有意保持为短视、可解释的风险加权反事实推演，存在以下边界：

1. 规划视野只到下一次任意角色决策事件。更长期影响通过冷却、剩余道具和未结算压力近似表达，没有继续展开多层博弈树。
2. `mean_score` 对所有合法应对等权平均，不是根据历史学习的对手策略概率。
3. 传送连续坐标最多采样 5 个战术落点并只保留其中最高分者，可能漏掉更优的中间坐标。
4. 位置价值只以最大攻击射程为目标，没有根据当前资源、冷却或人物打法动态选择理想距离。
5. 未结算压力是终点评分启发式。真实弹道命中由引擎模拟，但截止点之后的弹道风险没有继续做完整未来展开。
6. 当前权重是静态常数，没有按剩余生命、比赛剩余时间或人物职业自动缩放。
7. 候选数为 `己方动作数 × 对手动作数`。新增大量参数化动作会增加每回合计算量。

自定义代码权重示例：

```python
from backend.agents.planning import CounterfactualRollout, UtilityWeights

rollout = CounterfactualRollout(
    UtilityWeights(
        damage_received=1.5,
        position=0.7,
        item_reserve=0.4,
        risk_aversion=0.8,
    ),
    max_rollout_ms=5000,
)
```

调整权重后应使用固定对局种子或固定状态集比较胜率、动作分布、道具使用时机和单回合计算耗时。不同指标单位没有自动归一化，不能仅根据权重数字大小判断实际影响。

## 11. 代码与测试位置

| 内容 | 位置 |
| --- | --- |
| 矩阵生成、分支评分和风险加权选择 | [backend/agents/planning.py](../backend/agents/planning.py) |
| 战场克隆与权威结算 | [backend/game/engine.py](../backend/game/engine.py) |
| DeepSeek 提示和决策元数据 | [backend/agents/deepseek.py](../backend/agents/deepseek.py) |
| 人物公开工具名转换 | [backend/agents/character_tools.py](../backend/agents/character_tools.py) |
| 实时比赛接入 | [backend/matches/live.py](../backend/matches/live.py) |
| 距离、弹道、道具、时序和 LLM 输入测试 | [tests/test_planning.py](../tests/test_planning.py) |

战斗时间和锁定规则的完整说明见 [战斗回合与时间说明](combat-turns-and-time.md)。
