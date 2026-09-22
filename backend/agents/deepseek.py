from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from pydantic import Field, SecretStr, ValidationError, model_validator

from backend.game.models import Action, Schema, parameter_matches
from backend.game.skills import rest
from .character_tools import available_bindings, model_observation, profile_for

PROMPT_VERSION = "combat-tools-v16"
SUMMARY_MAX_CHARS = 60
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
SYSTEM_PROMPT = """请在确定性竞技场中为你的角色选择一个动作。将对手生命值降至零即可获胜；
若比赛超时，则剩余生命值较高者获胜。双方同时决策，决策期间游戏时间暂停。只能依据当前观察决策。

规则：
- 距离和射程均按角色中心点计算。forward 表示朝向对手。普通移动不能穿过或推动角色。
  移动距离为 speed * active_ms / 1000。
- 技能在启动时扣除体力和魔力，且至多命中一次。弹道需要飞行时间，离开其路径或射程可躲避伤害。
  防御只能减免来自正面的伤害。
- 决策锁定时间为 windup_ms + recovery_ms，不包含 active_ms。若效果持续时间超过锁定时间，
  它会保留在 ongoing_actions 中继续生效，同时角色可开始新动作。冲刺和传送均不提供无敌效果。
- 传送必须从工具给出的合法范围中选择绝对坐标。传送可以越过对手，但不能与对手重叠；
  双方同时传送且落点冲突时，两次传送都会回退。
- 冷却按双方同步决策的回合计算。消耗品在启动时消耗一次使用次数。护盾会在配置的回合数内吸收伤害。
  所有动作的消耗与恢复均以 match_context.rules.skills 中的当前有效数值为准。

观察：
- 技能数值已包含武器修正。省略的数值或布尔技能字段分别表示 0 或 false。
  直接使用 can_decide、lock_remaining_ms、effect_remaining_ms 和 time_remaining_ms，
  不要根据动作持续时间自行推算。
- recent_turns 包含精简后的动作、结果和状态变化量。
- opponent_history 聚合 recent_turns 之前的对手行为，只表示历史频率，不保留顺序，
  也不能据此假定对手下一步必然重复。
- incoming_threats 只列出对手尚未结算的攻击；优先使用其中的路径与最早命中时间，
  不要自行从动画阶段重复推算。
- counterfactual_analysis 来自权威规则引擎的反事实推演。preference 越高表示当前候选间的相对偏好越高，
  但它不是胜率，也不能跨回合比较。
  每个动作都会与对手的所有合法应对组合测试；最坏应对只是条件分支，不是对对手行为的预测。
  

必须使用工具的准确名称和合法参数，且只调用一个已提供的工具。decision_summary 应为一句不超过
60 个字符的简短中文，用于说明动作目的。除工具调用外，不要输出任何推理内容。
"""

ERROR_MESSAGES = {
    "invalid_settings": "DeepSeek .env 配置无效，请检查模型、地址与数值参数",
    "missing_api_key": "未配置 DEEPSEEK_API_KEY",
    "authentication_failed": "DeepSeek 鉴权失败，请检查密钥与账号权限",
    "model_unavailable": "DeepSeek 模型或接口地址不可用，请检查配置",
    "invalid_provider_request": "DeepSeek 拒绝了请求参数，请检查接口兼容性",
    "insufficient_balance": "DeepSeek 账户余额不足",
    "provider_unavailable": "模型连续请求失败，对局已停止",
}

DECISION_ERRORS = {
    "connection_error": "无法连接模型服务，请检查运行后端的网络、代理或沙箱权限",
    "request_timeout": "模型请求超时",
    "rate_limited": "模型服务限流",
    "provider_error": "模型服务暂时异常",
    "exactly_one_tool_required": "模型未返回恰好一个工具调用",
    "invalid_tool_type": "模型返回的工具类型无效",
    "tool_unavailable": "模型调用了当前人物不可用的技能",
    "invalid_arguments": "模型返回的技能参数无效",
    "invalid_summary": "模型未返回有效的战术摘要",
    "invalid_target_or_direction": "模型返回了不可用的技能参数或移动方向",
    "invalid_tool_response": "模型返回的工具调用格式无效",
    "response_truncated": "模型输出达到长度限制，工具参数可能不完整",
}


class ProviderError(Exception):
    def __init__(self, code: str, *, reason: str | None = None, player: str | None = None):
        self.code = code
        self.reason = reason if reason in DECISION_ERRORS else None
        self.player = player if player in ("p1", "p2") else None
        message = ERROR_MESSAGES.get(code, "模型服务不可用")
        if self.reason:
            message += "：" + DECISION_ERRORS[self.reason]
        super().__init__(message)


class DeepSeekSettings(Schema):
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.deepseek.com"
    model: str = Field(default="deepseek-v4-flash", min_length=1, max_length=128)
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_tokens: int = Field(default=512, ge=128, le=2048)
    temperature: float = Field(default=0.5, ge=0, le=2)
    max_consecutive_failures: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_endpoint(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("base_url must be an HTTP(S) API root without credentials or query")
        return self

    @classmethod
    def from_env(cls) -> DeepSeekSettings:
        # Disable interpolation so ${...} cannot import process environment values.
        values = dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig")
        fields = {
            "api_key": "DEEPSEEK_API_KEY", "base_url": "DEEPSEEK_BASE_URL",
            "model": "DEEPSEEK_MODEL", "timeout_seconds": "DEEPSEEK_TIMEOUT_SECONDS",
            "max_tokens": "DEEPSEEK_MAX_TOKENS", "temperature": "DEEPSEEK_TEMPERATURE",
            "max_consecutive_failures": "DEEPSEEK_MAX_CONSECUTIVE_FAILURES",
        }
        try:
            return cls(**{field: values[name] for field, name in fields.items() if values.get(name) is not None})
        except ValidationError as error:
            raise ProviderError("invalid_settings") from None

    @property
    def ready(self) -> bool:
        return bool(self.api_key.get_secret_value().strip())

    def public_metadata(self) -> dict:
        return {"provider": "deepseek", "model": self.model, "prompt_version": PROMPT_VERSION,
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "timeout_seconds": self.timeout_seconds, "thinking": "disabled"}


class DecisionInfo(Schema):
    source: str = "script"
    summary: str = Field(default="", max_length=SUMMARY_MAX_CHARS)
    attempts: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    decision_latency_ms: float = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    requested_model: str | None = None
    returned_model: str | None = None
    errors: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    selected_tool: str | None = None
    character_id: str | None = None
    character_prompt_version: str | None = None
    finish_reason: str | None = None
    rollout_recommendation: Action | None = None
    risk_weighted_score: float | None = None
    # Accepted for compatibility with short-lived format-4 recordings that
    # embedded diagnostics. New recordings keep these in dedicated .log files.
    counterfactual_analysis: dict | None = Field(default=None, exclude=True)
    llm_requests: list[dict] = Field(default_factory=list, exclude=True)
    llm_responses: list[dict] = Field(default_factory=list, exclude=True)


class AgentDecision(Schema):
    action: Action
    info: DecisionInfo
    trace: dict = Field(default_factory=dict, exclude=True)


def build_tools(observation: dict) -> list[dict]:
    tools = []
    for name, tool in available_bindings(observation).items():
        parameters = deepcopy(tool["parameters"])
        parameters["properties"]["decision_summary"] = {
            "type": "string", "maxLength": SUMMARY_MAX_CHARS,
            "description": "一句中文战术摘要，最多60字，不输出思维链。",
        }
        parameters["required"].append("decision_summary")
        tools.append({"type": "function", "function": {
            "name": name,
            "description": tool["description"],
            "parameters": parameters,
        }})
    return tools


def parse_tool_response(data: dict, observation: dict) -> tuple[Action, str]:
    try:
        if data["choices"][0].get("finish_reason") == "length":
            raise ValueError("response_truncated")
        calls = data["choices"][0]["message"].get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError("exactly_one_tool_required")
        call = calls[0]
        if call.get("type") != "function":
            raise ValueError("invalid_tool_type")
        name = call["function"]["name"]
        bindings = available_bindings(observation)
        if name not in bindings:
            raise ValueError("tool_unavailable")
        arguments = json.loads(call["function"]["arguments"])
        if not isinstance(arguments, dict):
            raise ValueError("invalid_arguments")
        summary = arguments.pop("decision_summary", "")
        # A valid game action should not be discarded solely because its optional
        # human-facing explanation is missing or malformed.
        summary = summary.strip()[:SUMMARY_MAX_CHARS] if isinstance(summary, str) else ""
        parameters = bindings[name]["parameters"]
        if set(arguments) != set(parameters["properties"]):
            raise ValueError("invalid_arguments")
        if any(not parameter_matches(arguments[key], schema)
               for key, schema in parameters["properties"].items()):
            raise ValueError("invalid_target_or_direction")
        action = Action.model_validate({"skill": bindings[name]["action"], **arguments})
        return action, summary.strip()
    except (AttributeError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError("invalid_tool_response") from error


def _constraint_description(schema: dict) -> str:
    if "enum" in schema:
        return "one of " + json.dumps(schema["enum"], ensure_ascii=False, separators=(",", ":"))
    parts = []
    if schema.get("type") == "number":
        parts.append("a finite number")
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if minimum is not None and maximum is not None:
        parts.append(f"in [{minimum}, {maximum}]")
    elif minimum is not None:
        parts.append(f">= {minimum}")
    elif maximum is not None:
        parts.append(f"<= {maximum}")
    intervals = []
    for option in schema.get("anyOf", []):
        low, high = option.get("minimum"), option.get("maximum")
        if low is not None and high is not None:
            intervals.append(f"[{low}, {high}]")
        elif low is not None:
            intervals.append(f">= {low}")
        elif high is not None:
            intervals.append(f"<= {high}")
    if intervals:
        parts.append("in one allowed interval: " + " or ".join(intervals))
    return "; ".join(parts) or "a value matching the current tool schema"


def _parameter_feedback(function: dict, bindings: dict[str, dict]) -> str | None:
    name = function.get("name")
    if not isinstance(name, str) or name not in bindings:
        return f"tool {name!r} is not currently available"
    raw_arguments = function.get("arguments")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError:
        return f"tool {name!r} arguments must be a valid JSON object"
    if not isinstance(arguments, dict):
        return f"tool {name!r} arguments must be a JSON object"
    arguments = {key: value for key, value in arguments.items() if key != "decision_summary"}
    properties = bindings[name]["parameters"]["properties"]
    missing = sorted(set(properties) - set(arguments))
    unexpected = sorted(set(arguments) - set(properties))
    issues = []
    if missing:
        issues.append("missing " + ", ".join(missing))
    if unexpected:
        issues.append("unexpected " + ", ".join(unexpected))
    for key in sorted(set(arguments) & set(properties)):
        schema = properties[key]
        if not parameter_matches(arguments[key], schema):
            value = json.dumps(arguments[key], ensure_ascii=False, separators=(",", ":"))
            issues.append(f"{key}={value} is invalid; expected {_constraint_description(schema)}")
    return f"tool {name!r}: " + "; ".join(issues) if issues else None


def retry_instruction(data: dict, code: str, observation: dict) -> str:
    bindings = available_bindings(observation)
    rejected = []
    feedback = []
    choices = data.get("choices") if isinstance(data, dict) else None
    message = (choices[0].get("message")
               if isinstance(choices, list) and choices and isinstance(choices[0], dict)
               else None)
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if isinstance(calls, list):
        for call in calls[:2]:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            rejected.append({
                "name": function.get("name"),
                "arguments": arguments[:1000] if isinstance(arguments, str) else arguments,
            })
            issue = _parameter_feedback(function, bindings)
            if issue:
                feedback.append(issue)
    parts = [f"Previous response invalid: {code}."]
    if rejected:
        parts.append("Rejected tool calls: " + json.dumps(
            rejected, ensure_ascii=False, separators=(",", ":"),
        ) + ".")
    if feedback:
        parts.append("Parameter errors: " + " | ".join(feedback) + ".")
    parts.extend([
        f"Available tools: {', '.join(bindings)}.",
        "Submit exactly one valid current tool with decision_summary.",
    ])
    return " ".join(parts)


def prompt_messages(observation: dict, system_prompt: str = SYSTEM_PROMPT) -> list[dict]:
    profile = profile_for(observation)
    dynamic = model_observation(observation)
    static = {"rules": dynamic.pop("rules"), "fighters": {}}
    dynamic.pop("tools", None)
    for side in ("self", "opponent"):
        fighter = dynamic[side]
        static["fighters"][side] = {
            key: fighter.pop(key)
            for key in (
                "character", "weapon",
                "max_health", "max_stamina", "max_mana",
            )
            if key in fighter
        }
    analysis = dynamic.get("counterfactual_analysis")
    if analysis:
        for key in (
            "higher_score_is_better", "recommended_action",
            "recommended_risk_weighted_score", "recommended_score_gap",
            "recommended_terminal_status",
        ):
            analysis.pop(key, None)
    character_prompt = "\n\n" + profile["prompt"] if profile else ""
    match_context = json.dumps(static, ensure_ascii=False, separators=(",", ":"))
    return [
        {"role": "system", "content": system_prompt + character_prompt
         + "\n\n本场固定配置 match_context（整场不变）：\n" + match_context},
        {"role": "user", "content": json.dumps(
            dynamic, ensure_ascii=False, separators=(",", ":"),
        )},
    ]


class DeepSeekAgent:
    def __init__(self, settings: DeepSeekSettings, client: httpx.AsyncClient,
                 *, system_prompt: str = SYSTEM_PROMPT):
        if not settings.ready:
            raise ProviderError("missing_api_key")
        self.settings = settings
        self.client = client
        self.system_prompt = system_prompt

    async def decide(self, observation: dict) -> AgentDecision:
        started = time.perf_counter()
        bindings = available_bindings(observation)
        profile = profile_for(observation)
        details = {"source": "llm", "attempts": 0, "errors": [],
                   "requested_model": self.settings.model,
                   "available_tools": list(bindings),
                   "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        trace = {"input": [], "output": []}
        analysis = observation.get("counterfactual_analysis")
        if analysis and "recommended_action" in analysis:
            details["rollout_recommendation"] = analysis["recommended_action"]
            details["risk_weighted_score"] = analysis["recommended_risk_weighted_score"]
        if profile:
            details["character_id"] = observation["rules"]["characters"][observation["self"]["fighter_id"]]["character_id"]
            details["character_prompt_version"] = profile["version"]
        messages = prompt_messages(observation, self.system_prompt)
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                for attempt in range(2):
                    details["attempts"] += 1
                    request_body = {
                        "model": self.settings.model, "messages": deepcopy(messages),
                        "tools": build_tools(observation), "tool_choice": "required",
                        "thinking": {"type": "disabled"}, "stream": False,
                        "max_tokens": self.settings.max_tokens,
                        "temperature": self.settings.temperature,
                    }
                    trace["input"].append(request_body)
                    response = await self.client.post(
                        self.settings.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.settings.api_key.get_secret_value()},
                        json=request_body,
                        timeout=self.settings.timeout_seconds,
                    )
                    try:
                        response_body = response.json()
                    except ValueError:
                        response_body = {"text": response.text}
                    logged_response = {"status_code": response.status_code}
                    if isinstance(response_body, dict) and "text" not in response_body:
                        logged_response["body"] = {
                            key: deepcopy(response_body[key])
                            for key in ("id", "model", "usage") if key in response_body
                        }
                        choices = response_body.get("choices")
                        if isinstance(choices, list):
                            logged_choices = []
                            for choice in choices:
                                logged_choice = {}
                                if isinstance(choice, dict) and "finish_reason" in choice:
                                    logged_choice["finish_reason"] = choice["finish_reason"]
                                message = choice.get("message") if isinstance(choice, dict) else None
                                if isinstance(message, dict):
                                    logged_message = {}
                                    if message.get("content") is not None:
                                        logged_message["content"] = deepcopy(message["content"])
                                    if message.get("tool_calls") is not None:
                                        logged_message["tool_calls"] = deepcopy(message["tool_calls"])
                                    if logged_message:
                                        logged_choice["message"] = logged_message
                                logged_choices.append(logged_choice)
                            logged_response["body"]["choices"] = logged_choices
                    else:
                        logged_response["non_json_body"] = True
                    trace["output"].append(logged_response)
                    fatal = {400: "invalid_provider_request", 401: "authentication_failed",
                             402: "insufficient_balance", 403: "authentication_failed", 404: "model_unavailable"}
                    if response.status_code in fatal:
                        raise ProviderError(fatal[response.status_code])
                    if response.status_code >= 400:
                        details["errors"].append("rate_limited" if response.status_code == 429 else "provider_error")
                        break
                    try:
                        data = response_body
                        if not isinstance(data, dict):
                            raise ValueError("invalid_tool_response")
                        usage = data.get("usage") or {}
                        if isinstance(usage, dict):
                            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                value = usage.get(field, 0)
                                if isinstance(value, int) and value >= 0:
                                    details[field] += value
                        returned_model = data.get("model")
                        if isinstance(returned_model, str):
                            details["returned_model"] = returned_model[:128]
                        choices = data.get("choices")
                        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                            finish = choices[0].get("finish_reason")
                            if finish in ("tool_calls", "stop", "length", "content_filter", "function_call"):
                                details["finish_reason"] = finish
                        action, summary = parse_tool_response(data, observation)
                        details["selected_tool"] = next(name for name, tool in bindings.items() if tool["action"] == action.skill)
                        details.update(summary=summary, latency_ms=(time.perf_counter() - started) * 1000)
                        return AgentDecision(action=action, info=DecisionInfo(**details), trace=trace)
                    except ValueError as error:
                        code = str(error) if str(error) in {
                            "exactly_one_tool_required", "invalid_tool_type", "tool_unavailable",
                            "invalid_arguments", "invalid_summary", "invalid_target_or_direction",
                            "invalid_tool_response", "response_truncated",
                        } else "invalid_tool_response"
                        details["errors"].append(code)
                        if attempt == 0:
                            messages.append({
                                "role": "user",
                                "content": retry_instruction(data, code, observation),
                            })
        except (TimeoutError, httpx.TimeoutException):
            details["errors"].append("request_timeout")
        except httpx.RequestError:
            details["errors"].append("connection_error")
        reason = DECISION_ERRORS.get(details["errors"][-1], "模型请求失败")
        recovery = next((tool["metadata"].get("display_name") for tool in bindings.values() if tool["action"] == "rest"), None) or "休息"
        details.update(source="fallback", summary=f"{reason}，执行{recovery}保底。",
                       latency_ms=(time.perf_counter() - started) * 1000)
        return AgentDecision(action=rest(), info=DecisionInfo(**details), trace=trace)
