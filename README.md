# AI 格斗竞技场

## 策略对照评测

前端顶部的「评测结果」展示后端生成的报告，可比较胜率、得分率、完成率、工具调用成功/修正/保底率、决策延迟、Token 与估算费用，并打开每一场完成对局的回放。刷新报告只读取已有结果，不会触发模型请求。

「战斗行为分析」支持策略汇总和按人物分组，展示角色战绩、双方交手结果、平均模拟时长、击倒/超时场次、模型决策次数、后退/恢复占比、命中/落空事件，以及推演推荐偏离率。双方交手结果按“策略 + 人物”对“对手策略 + 对手人物”分别统计，合并镜像站位与重复场次。偏离率只统计由模型选择且有推演推荐的决策，比较完整动作参数；同时提供按决策加权和逐场等权平均两种口径，偏离并不代表错误。命中/落空是事件数，不作为全部攻击的命中率。

可选择历史报告对比战绩、得分率、偏离率、时长、动作占比和 Token；默认对照上一份非运行中报告。页面核对战斗配置、模型参数、规则版本、后端代码摘要、参评策略与重复次数，并显示人物提示词版本。配置一致仍不代表变化具有统计显著性。

新评测将逐场行为计数和聚合分析保存在 `report.json`；旧报告在读取详情或下载时从已有回放补算，原文件保持不变，无需重跑对局。解析结果按回放文件状态缓存。缺失或损坏的回放只影响对应行为指标的覆盖场次，不影响已有战绩，也不会补成零。更新后需重启后端并构建前端。

点击「删除报告」并确认后，会永久删除当前实验报告及全部关联回放，其他实验与普通对局记录不受影响。生成中的报告不可删除。删除接口为 `DELETE /api/evaluations/{run_id}`，成功返回 204，生成中返回 409。

在项目根目录使用已有 `llm` 环境执行：

```powershell
# 无模型费用：规则机器人 vs 反事实推演，单一距离 × 交换角色 × 镜像位置 = 4 场
python -m backend.evaluation

# 自定义初始距离（支持小数），本次所有对局只使用该距离
python -m backend.evaluation --distance 3.5

# 四组对照：额外加入纯 DeepSeek、反事实推演 + DeepSeek；会产生真实 API 用量
# 1 个局面 × 6 个策略组合 × 4 个角色/位置组合 × 3 次重复 = 72 场
python -m backend.evaluation --distance 6 --include-llm --repeats 3 --time-limit-ms 60000

# 复用已保存报告中的完整配置；替换 <run_id> 为命令输出的实验 ID
python -m backend.evaluation --suite data/evaluations/<run_id>/report.json
```

每次评测只使用一个局面。`--distance` 指定双方的初始中心距离，默认 `6`，允许范围为 `0.8`～`11.2`（含端点），双方围绕擂台中心对称出生；对局开始后仍可正常移动。交换角色、镜像位置与重复评测均保持相同初始距离。`--distance` 与 `--suite` 互斥；`--suite` 只接受单局面配置或报告。比较不同距离时分别运行命令，生成独立报告。

默认每局上限 20 秒模拟时间，用于快速验证流程；正式比较建议使用 60 秒和更丰富的局面。只有加入 `--include-llm` 才能启用真实模型，密钥仍从根目录 `.env` 读取。可用 `--strategies heuristic deepseek --include-llm` 等指定策略；不指定策略时，`--include-llm` 默认选择全部四组。`--repeats` 重复相同条件，本地确定性策略的重复不应视作新的独立样本。

费用默认不估算。需要费用对比时，同时传入 `--input-price`、`--output-price`（每百万 Token 单价）与 `--currency CNY`，使用你实际账户的价格；估算未区分缓存命中优惠，不是账单。模型没有报告的 Token、失败对局的调用费用不计入完成场次均值。

输出位于 `data/evaluations/<run_id>/`，包括原子更新的 `report.json` 及独立 `replays/`；不进入普通对局历史。`ARENA_DATA_DIR` 可同时调整服务和评测的数据根目录。自定义 `--output` 必须指向服务数据目录下的 `evaluations` 才能在该服务显示。服务端新增接口需要使用者手动重启服务；前端修改后执行 `npm --prefix frontend run build`。

报告记录完整局面/角色/装备/道具/提示词配置、模型参数、规则版本、Git 提交、后端源码摘要、原始逐场指标及结果。`--suite` 可读取旧报告的配置快照或 `{ "name": "实验名称", "scenarios": [{ "name": "局面名称", "config": { ...GameConfig } }] }` 格式文件。复跑旧报告时，策略、重复数、模型设置和代码仍取本次参数/环境，比较时应核对这些元数据；远端模型输出无法保证逐次一致。

规则机器人按资源、射程和伤害/锁定时长选择动作，无推演、无模型调用。纯 LLM 和混合策略共享动态合法工具、历史窗口、提示词及错误处理；纯 LLM 不接收推演结果，不使用必然击倒捷径。混合策略保留现有必然击倒时跳过模型的行为，因此比较的是完整策略方案。

胜率按「胜场 / 完成场次」计算，得分率给平局计半分。失败和未执行场次单独显示；鉴权、余额或请求配置错误会停止实验，已完成结果仍保留。性能和用量指标仅统计完成场次，保底率分母是实际请求过模型的决策数；P50/P95 使用逐决策样本的线性插值。报告仅描述当前局面集，小样本不用于证明通用胜率。

读取接口：`GET /api/evaluations`、`GET /api/evaluations/{run_id}`、`GET /api/evaluations/{run_id}/export`、`GET /api/evaluations/{run_id}/replays/{replay_id}`（末尾加 `/export` 下载回放）。没有启动评测的网页接口。

验证：`python -m pytest tests/test_evaluation.py`；完成前端构建后运行 `python -m tools.check_evaluations_ui`，使用临时数据和进程内 API 验证空状态、报告、导出、回放跳转、手机布局及错误提示，不启动服务、不调用模型。截图位于 `artifacts/evaluations/`。

两个 agent 通过技能函数控制二次元像素人物战斗。可分别选择剑士「绯刃」和法师「霜铃」，人物拥有不同的招式、数值、冷却和特效。技能受体力、魔力和回合冷却约束，agent 需要在攻击、移动、防守和恢复资源之间选择。

回合流程、模拟时间与现实时间的区别、动作锁定和冷却示例，见 [战斗回合与时间说明](docs/combat-turns-and-time.md)。

## 人物与像素素材

- 绯刃：连斩、拔刀斩、绯月斩、架刀、疾步、步行、调息。
- 霜铃：冰弹、霜爆、冰棘、冰盾、传送、步行、冥想。冰弹具有实际飞行时间和射程判定，传送使用指定落点。
- 每名人物包含 11 类动作、44 帧透明像素图集、头像和命中特效图集。动画与特效由对局时间驱动，支持暂停、倍速和回放跳转。
- 技能特效使用与人物一致的粗像素颗粒：三种刀光、冰弹拖尾、霜爆碎晶、递进冰棘、架刀与冰盾、传送粒子和恢复光点；剑击与冰击使用不同命中图集，短攻击带渐隐残光。
- 场景为「樱庭 · 月下试炼」：樱花、月色远山、神社与石灯笼庭院。人物采用大头短身的粗颗粒 Q 版像素风，在 80 × 64 网格上绘制，再以 4 倍最近邻放大为 320 × 256 帧；使用简洁五官、大块配色和易辨识的发型与武器。
- 人物规则、Agent 工具与提示词位于 `configs/characters/<character_id>/`；前端像素素材位于 `frontend/public/assets/characters/<character_id>/`，两者通过 `character_id` 关联。
- 前端素材目录中的 `manifest.json` 和各人物的 `animations.json` 只描述图集、头像、特效与动作帧。
- 绘制源脚本与下载参考图位于 `assets-source/characters/`。参考来源为用户指定的 `https://moegirl.icu/` 中的刻晴、甘雨页面，完整来源记录见 `references/sources.json`。未使用生图 API，参考原图不随前端发布。
- 模型使用人物专属工具名，例如剑士的 `blade_flurry`、`draw_slash`，法师的 `ice_bolt`、`ice_shield`。后端将经过校验的调用映射到引擎动作，使用人物对应的动画与特效。冷却按双方决策回合结算；决策锁定时长为前摇与后摇之和，生效时间不增加锁定。
- 对局配置保存双方人物、完整技能数值、人物提示词和工具定义，回放使用创建对局时的配置快照。

### 人物工具与提示词

每个人物的权威 UTF-8 配置位于：

```text
configs/characters/frost_bell/
  character.json   人物信息、版本（version）和技能数值
  tools.json       可调用工具、参数与引擎动作映射
  prompt_neutral.md     原版战斗提示词（无行为偏好，默认使用）
  prompt_aggressive.md  激进型战斗提示词

frontend/public/assets/characters/frost_bell/
  animations.json  动作帧配置
  sprites/         动作图集
  effects/         特效图集
  portrait.png     头像
```

创建新对局时，后端读取双方人物包并校验配置，再根据每个决策回合的体力、魔力、冷却和行动状态筛选工具。修改配置文件后创建新对局即可生效，不需要重启服务；已开始的对局和已保存回放使用原有配置快照。首次更新到这版代码仍需手动重启后端。

两个人物均保留原版提示词，并提供独立的激进型版本：绯刃偏向主动近身、追击和有利换血，霜铃偏向持续施压、争取施法位置和爆发机会。激进型与原版共用技能数值和工具，仅提示词及其版本标识不同。人物基础版本由 `character.json` 的 `version` 指定，加载后写入 `agent.version`，用于对局快照与提示词版本记录；激进型版本在基础版本号后附加 `-aggressive-v1`，修改激进型提示词时应同步更新 `backend/game/characters.py` 中的版本后缀。

对照评测可分别指定双方的提示词，例如先让 P1 绯刃使用激进型、P2 霜铃使用中性版，再交换提示词：

```powershell
conda run -n llm python -m backend.evaluation --include-llm --strategies deepseek counterfactual_deepseek --distance 6 --repeats 3 --p1-prompt aggressive --p2-prompt neutral --output data/evaluations
conda run -n llm python -m backend.evaluation --include-llm --strategies deepseek counterfactual_deepseek --distance 6 --repeats 3 --p1-prompt neutral --p2-prompt aggressive --output data/evaluations
```

这两条命令会调用模型并产生 API 用量。`--p1-prompt` 和 `--p2-prompt` 分别选择双方提示词，允许只指定一方；未指定的一方使用 `--prompt-variant` 给出的公共默认值，公共默认值省略时为 `neutral`。双方参数优先于公共默认值。提示词绑定 P1 绯刃和 P2 霜铃，在评测交换策略与镜像出生位置时保持各自的选择。规则策略 `heuristic`、`counterfactual` 不读取提示词，评估提示词行为差异需要使用模型策略。

每次运行生成独立报告，保存双方完整提示词快照及版本；使用 `--suite` 复用报告时沿用其中的快照，不能同时指定上述三个提示词参数。Python 调用可通过 `with_characters(config, choices, prompt_variants={"p1": "aggressive", "p2": "neutral"})` 分别选择双方版本，也可通过 `load_character(character_id, prompt_variant="aggressive")` 加载单个人物。

网页「对战准备」中可为双方分别选择「人物提示词」：中立（默认）或激进，普通对局与实时对局均会保存所选提示词快照。提示词只影响纯 DeepSeek 和反事实推演 + DeepSeek 策略。读取回放会显示保存的选择，继续未完成对局仍沿用创建时的提示词。接口可在选择 `characters` 时传入 `prompt_variants={"p1": "aggressive", "p2": "neutral"}`，未指定的一方默认使用 `neutral`。

`tools.json` 的 `name` 是模型实际调用的函数名；`action` 是内部动作类型，用于复用已实现的判定和动画。当前支持七个引擎动作，每个动作配置一个工具；添加全新的行为类型还需扩展引擎。步行和疾步的 `parameters` 声明合法方向，传送声明 `{"position":{"type":"number"}}`，其余工具使用空对象。提示词及参数文件内不要放 API 密钥。

示例：

```json
{"name":"ice_bolt","action":"jab","description":"发射有飞行时间的冰弹。","parameters":{}}
```

移动和恢复数值直接在各人物的 `character.json` 中调整，例如 `configs/characters/crimson_blade/character.json`：

| 配置位置 | 含义 | 绯刃当前值 |
| --- | --- | --- |
| `skills.dash.speed` / `active_ms` | 移动速度、生效时长；距离 = 速度 × 毫秒 / 1000 | 7 / 250，移动 1.75 |
| `skills.move.speed` / `active_ms` | 普通移动使用同一计算方式 | 2 / 500，移动 1 |
| `skills.rest.stamina_restore` | 完整调息一次恢复的体力 | 15 |
| `skills.rest.mana_restore` | 完整调息一次恢复的魔力 | 20 |

恢复量在技能生效期间逐步结算；前摇和后摇不恢复资源。显式设置 0 表示不恢复该资源，省略或设为 `null` 时沿用全局每秒恢复率。点击技能展开详情可查看完整施放的数值，再次点击收起；实际移动会受碰撞和边界限制，恢复受资源上限限制。修改后创建新对局生效，已保存回放保留原值。

霜铃的传送配置在 `configs/characters/frost_bell/character.json` 的 `skills.dash`：`teleport: true`，默认消耗 10 体力和 8 魔力、冷却 1 回合。模型调用 `frost_teleport(position)`，例如 `position=1.5` 表示横向绝对坐标 1.5。默认场地落点范围为 0.4～11.6，可越过对手，但必须与对手中心相距至少 0.8。可选范围随场地和对手位置动态生成。

传送不沿路径移动，移动速度必须为 0；`windup_ms=0` 时在动作提交后立即生效，设为符合模拟步长的正整数时在前摇结束后生效。决策锁定仍按前摇与后摇之和计算。双方同时传送且落点重叠时，两者均取消并休息，不扣除传送费用、不进入冷却；前摇期间落点被占用时，本次传送会失败。前端点击详情显示落点范围，画面和回放展示起点/终点特效及实际落点。

### 武器配置

武器系统独立于人物包。权威配置位于 `configs/weapons/*.json`，前端像素图位于 `frontend/public/assets/weapons/`；人物配置只声明 `profession`（`swordsman` 或 `mage`），创建对局时由组合层校验职业并装备武器。当前剑士可选石中剑、湖中剑，法师可选巨龙之怒、暮光权杖。

`resource_modifiers` 和 `skill_modifiers` 都使用增量：正数增加，负数减少。可修改生命、体力、魔力上限，以及技能的伤害、射程、速度、消耗、冷却、三个阶段时长和恢复量。最终资源上限必须大于 0，技能数值必须满足原有规则；时长增量仍需使最终值对齐 `step_ms`。

```json
{
  "weapon_id": "stone_sword",
  "name": "石中剑",
  "profession": "swordsman",
  "description": "均衡的仪式长剑。",
  "asset": "stone_sword.png",
  "resource_modifiers": { "max_health": 15, "max_stamina": -5, "max_mana": 0 },
  "skill_modifiers": {
    "jab": { "health_damage": 2 },
    "guard": { "stamina_cost": -1 }
  }
}
```

修改 JSON 后创建新对局即可生效。Agent 的观察包含双方武器名称、职业、应用武器修正后的资源上限与有效技能数值，不重复发送原始修正值。已开始的对局与回放继续使用创建时保存的武器快照。

### 主动消耗品

道具系统独立于人物和武器。权威配置位于 `configs/items/*.json`，像素图位于 `frontend/public/assets/items/`。每名玩家最多选择两个不同道具，每件按 JSON 中的 `charges` 限制使用次数；使用道具占用一次行动，并遵循自己的前摇、生效和恢复时间。

当前包含樱露（生命 +25）、活力饮料（体力 +35）、魔力结晶（魔力 +35）、护身符（20 点护盾，持续 2 回合）、时之砂（全部剩余技能冷却 -1 回合）和烟雾弹（向后移动 1.5，无无敌）。恢复不会超过资源上限；道具耗尽、资源已满、护盾生效中或无冷却可减少时，`use_item` 会从 Agent 的可用工具中移除，并给出具体原因。

Agent 通过通用工具 `use_item(item_id)` 使用道具。system `match_context` 包含双方道具效果，动态观察包含自身剩余次数；当前可用道具只通过 `use_item` 的参数枚举暴露，不重复发送不可用原因。JSON 修改会在新对局和阵容预览中重新加载，已开始的对局与回放保留配置快照。

重新绘制本地素材：

```powershell
conda run -n llm python assets-source/characters/draw_characters.py
conda run -n llm python assets-source/weapons/draw_weapons.py
conda run -n llm python assets-source/items/draw_items.py
conda run -n llm python tools/draw_arena.py
npm --prefix frontend run build
```

人物接口为 `GET /api/characters`，武器接口为 `GET /api/weapons`，道具接口为 `GET /api/items`。创建对局或启动实时对局时传入 `characters`、`weapons` 和双方道具列表：

```json
{"p1":"test","p2":"test","characters":{"p1":"crimson_blade","p2":"frost_bell"},"weapons":{"p1":"stone_sword","p2":"dragonwrath_staff"},"items":{"p1":["healing_potion","stamina_drink"],"p2":["mana_crystal","guardian_charm"]}}
```

启动服务后运行人物验收：`conda run -n llm python tools/check_characters_ui.py --url http://127.0.0.1:8002`。

当前已实现阶段一至阶段三的功能：规则引擎、脚本策略、反事实推演 + DeepSeek 工具调用、实时观战和持久化回放。反事实推演 + DeepSeek 对局需要有效的服务端密钥。

## 运行环境

使用已有 Conda 环境 `llm`，Python 3.11 或以上。当前环境已具备后端与浏览器测试所需依赖；前端依赖已安装在 `frontend/`，版本由 `package-lock.json` 固定。

## 像素素材生成

当前项目素材由本地代码直接绘制像素并导出，未调用图像生成模型或生图 API，无需配置生图服务密钥。

人物像素由 `assets-source/characters/pixel_renderer.py` 绘制，通过 `draw_characters.py` 打包为图集；场景背景由 `tools/draw_arena.py` 绘制。素材在低分辨率网格上绘制后，使用最近邻缩放保留像素边缘。详细说明见 [人物素材说明](assets-source/characters/README.md)。

## 浏览器观战

构建前端，然后在项目根目录启动服务：

```powershell
npm --prefix frontend run build
conda run -n llm python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。如果端口已被使用，可以改用 `--port 8001` 并访问对应地址。

- 首次打开且没有历史记录时，自动生成一场默认对局，停在初始状态。
- 可以选择双方策略并创建新对局，查看动作、命中反馈、生命值、体力和魔力。
- 技能显示剩余冷却回合、魔力不足、体力不足或行动锁定；所有技能均点击展开详情，查看效果、消耗、配置冷却和动作时长，支持键盘与手机操作。
- 支持播放、暂停、重新播放、0.5/1/2 倍速、前后单回合和时间轴拖动。
- 时间轴上的损坏标记与事件时间均可点击跳转，便于检查受伤前后状态。
- 历史记录可切换，支持下载 JSONL 回放文件和导入回放。

纯脚本对局先生成完整记录再播放；包含反事实推演或 DeepSeek 的页面对局通过 WebSocket 按周期实时推进。读取已保存的回放不会重新运行 agent 或游戏引擎。

### 反事实推演 Agent

页面或命令行可选择 `counterfactual`（反事实推演）。它不预测对手下一步，而是从当前完整战场状态克隆独立分支，枚举双方当前所有合法动作组合，并调用同一套权威规则引擎推进到下一次决策事件。分支会保留仍在生效的技能、弹道、移动轨迹、护盾、冷却和未使用道具；传送则枚举边界、攻击距离和越过对手等有战术意义的合法落点，完整评估后只保留风险加权收益最高的落点参与最终选招和 LLM 上下文。

每个动作按最坏分支与平均分支的加权收益排序。收益包含双方生命、资源、护盾、位置、冷却、场上未结算威胁、下次决策权和双方道具机会成本。决策点仍固定为每 500ms 一次：锁定时间不超过 500ms 时在下个回合决策，锁定 600ms 时在 1000ms 的下下个回合决策；`active_ms` 不计入锁定时间，但超出锁定期的效果会继续留在场上结算。

反事实推演 + DeepSeek 对局会把这份由代码生成的聚合分析和最坏应对交给模型。模型负责结合人物战术选择动作，不负责编造收益数值，也不会把某个分支当作对手行为预测。

完整计算公式、默认权重、字段含义与 LLM 输入示例见 [风险加权反事实推演与收益矩阵说明](docs/counterfactual-planning.md)。

前端开发时，可以保留 8000 端口上的后端服务，另开终端运行：

```powershell
npm --prefix frontend run dev
```

Vite 默认使用 <http://127.0.0.1:5173>，并将 `/api` 代理到 8000 端口。

## DeepSeek 配置

配置模板见 [.env.example](.env.example)。DeepSeek 配置只读取项目根目录的 `.env`，不读取系统或进程环境变量，也不展开 `${变量名}`。未填写的配置项使用下表默认值；密钥缺失时无法启动反事实推演 + DeepSeek 对局。修改后创建新对局即可生效，已开始的对局继续使用原配置。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | 服务端密钥，不发送给浏览器或保存到对局文件 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 基础地址，程序追加 `/chat/completions` |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 请求中的模型 ID |
| `DEEPSEEK_TIMEOUT_SECONDS` | `20` | 每次决策的总墙钟时间预算，包含格式修正 |
| `DEEPSEEK_MAX_TOKENS` | `512` | 每次模型请求的输出上限 |
| `DEEPSEEK_TEMPERATURE` | `0.5` | 采样温度 |
| `DEEPSEEK_MAX_CONSECUTIVE_FAILURES` | `3` | 同一 agent 连续失败达到该值时停止对局 |

根据 2026-09-10 查阅的 [DeepSeek 官方模型文档](https://api-docs.deepseek.com/quick_start/pricing)，`deepseek-v4-flash` 仍被接受，但请求会路由到 V4.1-Flash。项目保留该请求 ID，同时记录响应中的 `model` 字段；响应名称是否反映底层版本由服务端决定。

适配器使用 [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)，关闭 thinking 模式，要求每次恰好返回一个当前可用技能调用。每个工具增加最多 60 字的 `decision_summary` 参数供模型返回简短中文战术摘要，执行前移除该参数并由规则引擎校验实际动作；摘要缺失或格式错误不会使合法动作失效。思维链和原始响应正文不写入日志或回放。

工具调用校验失败时允许在同一超时预算内重试一次。重试提示会回显被拒绝的调用，并具体指出缺失或多余参数、合法枚举、数值上下界和传送可落点区间。动态上下文保留最近 4 个决策周期的逐回合增量；更早的对手行为累计为无顺序的动作、方向、道具、命中结果和伤害统计，避免长局中历史模式完全丢失，同时保持上下文大小有界。

配置密钥后，页面双方策略下拉框中可选择“反事实推演 + DeepSeek”。选项是否启用只表示服务端已配置密钥，不代表已通过远程鉴权。支持反事实推演 + DeepSeek 对脚本，以及两个反事实推演 + DeepSeek Agent 对战。

先执行一次真实接口检查：

```powershell
conda run -n llm python -m tools.check_deepseek
```

默认检查剑士的独立工具。加 `--player p2` 检查法师；`--p1-character`、`--p2-character` 可切换人物。检查命令不会启动或关闭服务。

运行一场 6 秒游戏时间的双模型对局并保存到回放库：

```powershell
conda run -n llm python -m tools.check_deepseek --match-ms 6000 --save
```

上述检查会调用真实模型并使用账户额度。测试脚本只输出动作、用量和错误代码，不输出密钥。真实服务失败时不要用可控响应测试结果代替真实联调结果。

## 实时对局

- 空闲角色以及已度过 `windup_ms + recovery_ms` 锁定时间的角色会请求决策；新动作立即开始，未结束的旧效果继续并行结算。
- 双方观察来自同一个世界状态，两个模型请求并发发送，互相看不到尚未提交的动作。
- 等待模型期间，模拟时间与体力、位置、生命值保持不变。
- 每周期的动作、事件和快照到达浏览器后播放；浏览器确认该周期播放完毕，服务端才开始下一周期。
- 暂停会停止播放与后续周期请求；已经开始的模型请求可以完成并缓存在浏览器中。
- 取消或断开实时连接会取消进行中的请求，未完成的对局不保存为完整回放。
- 实时对局期间不能跳转时间轴；结束后恢复所有回放控制。

格式不合法或调用了不可用技能时，在同一决策超时预算内最多修正一次。仍失败、超时、连接错误、限流或暂时服务错误会使用休息保底。修正次数、错误代码和保底来源分别记录，保底不计作模型成功决策。

鉴权失败、余额不足、模型不存在和请求配置错误会立即停止对局并显示原因；连续服务故障也会停止，避免把故障期间的休息序列保存成正常比赛。

每个决策记录包含可用工具名称、实际选择的工具、人物提示词版本、战术摘要、延迟、请求次数、token 用量、请求模型与响应模型。对局头部保存全局 system prompt 快照、提示词版本、模型参数与配置摘要，恢复未完成对局时继续使用创建时的提示词。

保底和停局会显示实际原因：`connection_error` 表示后端无法连接模型服务（包括代理、网络或运行环境的沙箱限制），`request_timeout` 表示超时，`tool_unavailable` 表示模型调用了不可用工具，`response_truncated` 表示输出被长度限制截断。连接失败不代表工具 schema 有错。服务由用户在自己的终端启停；若服务是在受网络限制的环境中启动，应关闭旧进程并在能访问模型服务的终端重新启动。

## 安装依赖

在其他机器上使用 Python 3.11+、Node.js 20.19+ 或 22.12+，并准备 `llm` 环境：

```powershell
conda run -n llm python -m pip install -e ".[web,dev,browser]"
npm --prefix frontend ci
```

浏览器测试还需要安装 Chromium；已有 Playwright Chromium 的环境可跳过：

```powershell
conda run -n llm python -m playwright install chromium
```

## 命令行对战

以下命令在项目根目录执行：

```powershell
conda run -n llm python -m backend.matches
```

也可以先激活环境：

```powershell
conda activate llm
python -m backend.matches
```

默认双方均使用 `test` 测试脚本：

```powershell
conda run -n llm python -m backend.matches --p1 test --p2 test
```

- `test`：循环调用当前人物的全部技能与已装备道具，用于检查技能、资源、冷却和道具效果。
- 某项暂时不可用时会继续检查后续项目，并在下一轮循环中再次尝试。
- 测试脚本只读取观察与当前可用工具，不能直接访问或修改规则引擎。

命令行也支持 DeepSeek，默认仍使用完整的 60 秒游戏时限：

```powershell
conda run -n llm python -m backend.matches --p1 counterfactual_deepseek --p2 test --json
conda run -n llm python -m backend.matches --p1 counterfactual_deepseek --p2 counterfactual_deepseek --trace
```

查看战斗事件、输出机器可读结果、加载配置：

```powershell
conda run -n llm python -m backend.matches --trace
conda run -n llm python -m backend.matches --json
conda run -n llm python -m backend.matches --config configs/default.json
```

`--trace` 输出逐行 JSON 事件，最后一行是对局结果；`--json` 只输出结果，两者不能同时使用。该命令行事件输出用于调试，完整回放文件通过观战页面导出。

## 已实现规则

默认生命、体力、魔力上限均为 100。技能开始时扣除体力与魔力，空挥仍消耗资源。当前绯刃的调息恢复 15 体力、10 魔力，霜铃的冥想恢复 10 体力、12 魔力，武器还可修改这些数值；恢复在 0.5 秒生效期间逐步结算且不会超过上限。两人的普通移动消耗 5 体力，休息无消耗，二者均无冷却。模型始终以观察中的有效技能数值为准。

每次双方同步决策及结算为一个回合。`cooldown_turns=N` 表示施放后的 N 个回合不可再次使用：第 1 回合施放、冷却 2 回合，则第 2、3 回合不可用，第 4 回合可用。角色处于行动锁定时，回合仍正常推进；模型耗时、暂停和播放速度均不影响冷却。动画时长继续使用毫秒。

| 人物 | 技能 | 魔力消耗 | 冷却回合 |
| --- | --- | --- | --- |
| 绯刃 | 连斩 / 拔刀斩 / 绯月斩 | 0 / 10 / 15 | 0 / 3 / 2 |
| 绯刃 | 架刀 / 疾步 | 0 / 0 | 1 / 1 |
| 霜铃 | 冰弹 / 霜爆 / 冰棘 | 16 / 28 / 20 | 1 / 3 / 2 |
| 霜铃 | 冰盾 / 传送 | 12 / 8 | 2 / 1 |

- 攻击自动以对手为目标，攻击工具无需参数；步行和疾步传 `direction`，传送传 `position`。
- 方向：`forward`、`backward`，在动作开始时根据对手位置固定。
- 决策锁定时间为前摇与恢复时间之和；有效期不增加锁定，新动作不会取消或截断仍在生效的旧效果。
- 命中按角色中心距离判定；有效期内可以等待目标进入范围，单次攻击最多命中一次，整个有效期都未命中才算空挥。
- 格挡减免正面生命伤害；冲刺不提供无敌效果。
- 普通移动不会穿越对手、推动静止对手或越过擂台边界；传送可越过对手但不能落在其占位范围。
- 同一步内先计算双方命中，再统一应用伤害；双方同时倒下为平局。
- 生命值归零优先结算击倒；到达时间上限后比较生命值，相同则平局。
- 缺失或非法动作产生拒绝事件，并以休息保底；对锁定角色的新指令只拒绝，原动作继续。

## 时间与配置

默认模拟步长为 50 毫秒，决策间隔为 500 毫秒，游戏时间上限为 60 秒。无界面对局直接推进模拟时间，不按墙钟时间等待。

每个模拟步先移动，再计算命中和伤害，最后推进动作计时并判断胜负。动作阶段在该步开始时判断，事件时间记录该步结束时间。因此默认刺拳前摇为 100 毫秒，首个可命中模拟步的事件时间为 150 毫秒。

`configs/default.json` 包含场地、资源、伤害系数和七种技能的数值。时间参数必须是模拟步长的整数倍。

自定义 JSON 可省略顶层字段，省略字段使用默认值。如果提供 `skills`，则必须完整定义七种技能；移动、休息必须保持零体力消耗、零魔力消耗和零回合冷却。

## Python 接口

```python
from backend.agents import TestAgent
from backend.game import Arena, GameConfig
from backend.matches import run_match

arena = Arena(GameConfig())
summary = run_match(arena, {
    "p1": TestAgent(),
    "p2": TestAgent(),
})
print(summary["result"])
```

单独使用引擎时，通过 `arena.observe("p1")` 获取独立观察副本，通过 `arena.available_tools("p1")` 获取动态工具 schema，通过 `arena.advance({"p1": action1, "p2": action2})` 同步提交并推进一个决策周期。锁定角色可从提交字典中省略。

新增策略只需实现 `decide(observation)`，返回 `Action` 或符合格式的字典，例如 `{"skill": "jab"}`。后续 LLM 适配层可以复用同一接口。

## 测试

```powershell
conda run -n llm python -m pytest
```

完整测试套件覆盖规则、人物技能与冷却、独立提示词和工具加载、飞行命中、存档、HTTP 接口和实时模型对局，包括并发、格式修正、超时、取消、保底、可配置的移动与恢复效果、回合冷却边界、传送坐标校验及同时落点冲突。自动测试使用可控模型响应，不消耗真实 API 额度。

启动观战服务后运行浏览器验收：

```powershell
conda run -n llm python tools/check_ui.py
```

该检查会创建测试对局、执行导入导出，并检查播放、暂停、单步、受伤跳转和 1440/768/390/320 像素宽度布局。截图与导出的样例保存在 `artifacts/`，不会提交到版本库。

验证实时页面的等待、暂停、继续、取消、结束和错误状态：

```powershell
conda run -n llm python -m tools.check_live_ui
```

此检查使用可控 WebSocket 与模型响应，不调用外部模型，也不会把测试中的虚拟对局保存进正式回放库。

## 对局存档

服务端在每个完整决策回合结束后提交一次数据库事务。事务包含恢复战斗所需的双方动作、事件、该回合全部帧和权威状态；收益矩阵与 LLM 日志不写入数据库。进程退出、WebSocket 断开或模型服务暂时失败后，已经提交的回合不会丢失。

默认使用项目根目录的 `data/index.sqlite3`，可通过 `ARENA_DATA_DIR` 指定其他数据目录。若要使用本地 MySQL，在项目 `.env` 或进程环境中设置（进程环境优先）：

```dotenv
ARENA_DATABASE_URL=mysql+pymysql://arena:password@127.0.0.1:3306/agent_arena
```

密码中的特殊字符需要 URL 编码。启动时会先执行 `CREATE DATABASE IF NOT EXISTS` 自动创建 URL 指定的数据库，因此该 MySQL 用户需要建库权限；随后自动创建所需数据表。MySQL 和 SQLite 使用相同的 `combat_matches`、`combat_turns` 与 `replays` 数据结构；`combat_turns` 每回合一行，`combat_matches.checkpoint_json` 保存最后一个已提交回合的恢复点。

存档文件包括：

- `index.sqlite3`：未配置 MySQL 时的逐回合战斗状态、恢复点和对局索引。
- `<replay_id>.jsonl`：仅在对局完成后生成的便携回放，包含格式版本、配置、逐回合决策、事件、每个模拟步的状态和最终结果。
- `logs/<replay_id>/turn-000001.log`：单个回合的收益矩阵、脱敏后的模型决策上下文与最终结果，不进入数据库或 JSONL。

每个 `.log` 都是围绕“为什么选择这个动作”组织的 UTF-8 文本报告：调用模型时先列出实际发送的动态状态、来袭攻击、相对偏好、对手历史聚合、可用动作、最近战况及参数级重试指令，再单独绘制完整收益矩阵和内部引擎评估，最后记录动作来源和 `decision_summary`。确定击倒终局由引擎直接执行时不生成模型上下文或模型用量。重复的稳定比赛配置、完整工具 Schema、鉴权信息和模型内部推理文本不会逐回合写入日志；可从对局头部的配置与 system prompt 快照还原稳定上下文。

每条 JSONL 记录使用 `type` 和 `data` 字段；类型依次为 `header`、`frame`、`decision`、`event`、`summary`。同一时间可有多帧，分别表示当前回合末状态、下一回合冷却结算后状态和新动作提交后的状态；`turn` 保存回合编号。

回放读取会校验格式版本、配置摘要、时间顺序、关键字段和最终结果。导入会分配新的对局 ID，保留原始配置与记录；不能导入超过 12 MB 的文件。当前仅支持格式 4，战斗规则版本为 0.7.0，包含风险加权反事实推演、职业武器、主动道具、魔力、回合编号、回合冷却和并行生效效果。

本地 API：

| 请求 | 用途 |
| --- | --- |
| `POST /api/matches` | 创建对局；传入 `resume_id` 时继续未完成对局 |
| `GET /api/matches/incomplete` | 列出可继续运行的未完成对局 |
| `GET /api/replays` | 最近 100 场对局 |
| `GET /api/replays/{id}` | 读取完整记录 |
| `DELETE /api/replays/{id}` | 删除对局记录及对应的 JSONL 文件，成功返回 204 |
| `GET /api/replays/{id}/export` | 下载 JSONL 存档 |
| `POST /api/replays/import` | 将 JSONL 文件内容作为请求体导入 |
| `GET /api/agents` | 可选策略及模型配置状态，不包含密钥 |
| `GET /api/weapons` | 武器目录、职业限制和数值修正 |
| `WS /api/live` | 按周期推进实时对局 |

WebSocket 连接后先发送与 `POST /api/matches` 相同的请求。新对局发送策略与配置；继续对局发送 `{"resume_id":"<replay_id>"}`。服务端依次发送 `started`、`waiting`、`cycle`，最终发送 `finished`，异常时发送 `error`。`cycle` 包含本周期的帧、事件和决策；客户端播放完成后发送 `{"type":"next"}`。发送 `{"type":"cancel"}` 会停止当前运行并保留最后一个完整回合，之后可用同一 ID 继续。

网页对局最多 2400 个模拟步，避免单次录制产生过大的文件。命令行引擎不受此网页限制。

## 美术素材

人物由 `assets-source/characters/pixel_renderer.py` 绘制，`draw_characters.py` 打包像素图集。粗像素造型保留绯刃的双马尾、花饰和剑，以及霜铃的蓝发、弯角、铃铛和冰晶法杖，不追求真实比例和细碎纹样。PixiJS 按动作阶段播放图集，并叠加刀光、冰晶、护盾、残影与伤害数字。樱花庭院背景由 `tools/draw_arena.py` 绘制，`tools/generate_assets.py` 同时保留历史机器人素材的生成入口。

## 目录

```text
backend/game/       规则引擎、数据模型、技能函数
backend/agents/     脚本策略、DeepSeek 适配器和配置
backend/matches/    同步及异步决策协调与命令行入口
backend/replay/     版本化回放、恢复状态、独立诊断日志和数据库适配
backend/api/        FastAPI 观战服务
frontend/          React、TypeScript、PixiJS 观战页面
configs/           游戏参数
tests/             规则、整局、回放与接口测试
tools/             位图生成与浏览器验收
data/              本地对局存档（不提交）
DEVELOPMENT.md     总体设计与后续阶段
```

无需启动服务的魔力与回合冷却界面检查（先完成前端构建）：

```powershell
conda run -n llm python -m tools.check_turns_ui
```

该检查使用临时目录与进程内 API，截图写入 `artifacts/turns/`，不会写入现有回放库。服务启动、关闭和本次代码更新后的重启均由使用者手动执行。
