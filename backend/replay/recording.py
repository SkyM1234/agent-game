from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from backend.agents.scripted import AGENTS
from backend.agents.deepseek import DecisionInfo
from backend.game import Arena, GameConfig
from backend.game.models import Action, ActionName, Schema, Skill
from backend.matches import run_match

Player = Literal["p1", "p2"]
RULES_VERSION = "0.7.0"
FORMAT_VERSION = 5


def config_digest(config: GameConfig) -> str:
    normalized = GameConfig.model_validate(config.model_dump())
    data = normalized.model_dump()
    for character in data["characters"].values():
        if character["profession"] is None:
            del character["profession"]
        for name in ("max_health", "max_stamina", "max_mana"):
            if character[name] is None:
                del character[name]
    # Preserve existing format-3 hashes when per-skill restoration was not configured.
    for skills in [data["skills"], *(character["skills"] for character in data["characters"].values())]:
        for spec in skills.values():
            if not spec["teleport"]:
                del spec["teleport"]
            for name in ("stamina_restore", "mana_restore"):
                if spec[name] is None:
                    del spec[name]
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class ActionView(Action):
    action_id: str
    phase: Literal["windup", "active", "recovery"]
    elapsed_ms: int = Field(ge=0)
    remaining_ms: int = Field(ge=0)


class FighterView(Schema):
    fighter_id: Player
    position: float
    facing: Literal[-1, 1]
    health: float = Field(ge=0)
    stamina: float = Field(ge=0)
    mana: float = Field(ge=0)
    action: ActionView | None
    ongoing_actions: list[ActionView] = Field(default_factory=list)
    cooldowns: dict[Skill, int] = Field(default_factory=dict)
    items: dict[str, int] = Field(default_factory=dict)
    shield: float = Field(default=0, ge=0)
    shield_turns: int = Field(default=0, ge=0)


class ResultView(Schema):
    winner: Player | None
    reason: Literal["knockout", "double_knockout", "time_limit"]
    simulation_time: float = Field(ge=0)


class ToolView(Schema):
    available: list[ActionName]
    unavailable: dict[ActionName, str]
    items: dict = Field(default_factory=dict)


class Frame(Schema):
    turn: int = Field(ge=1)
    tick: int = Field(ge=0)
    simulation_time: float = Field(ge=0)
    fighters: dict[Player, FighterView]
    result: ResultView | None
    tools: dict[Player, ToolView]
    event_count: int = Field(ge=0)


class Decision(Schema):
    tick: int = Field(ge=0)
    simulation_time: float = Field(ge=0)
    actions: dict[Player, Action]
    details: dict[Player, DecisionInfo] = Field(default_factory=dict)


class RecordedEvent(Schema):
    simulation_time: float = Field(ge=0)
    actor: Player | None
    action_id: str | None
    status: str
    reason: str | None
    effects: dict


class Recording(Schema):
    format_version: Literal[5] = FORMAT_VERSION
    rules_version: str = RULES_VERSION
    replay_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    created_at: datetime
    config_hash: str
    config: GameConfig
    agents: dict[Player, str]
    agent_metadata: dict[Player, dict] = Field(default_factory=dict)
    frames: list[Frame] = Field(min_length=2, max_length=10000)
    decisions: list[Decision]
    events: list[RecordedEvent]
    summary: dict

    @model_validator(mode="after")
    def validate_recording(self) -> Recording:
        if self.config_hash != config_digest(self.config):
            raise ValueError("configuration hash mismatch")
        if set(self.agents) != {"p1", "p2"}:
            raise ValueError("recording must contain two agents")
        if self.frames[0].tick != 0 or self.frames[0].event_count != 0:
            raise ValueError("recording must start with the initial state")
        previous_tick, previous_count, previous_turn = -1, 0, 1
        for frame in self.frames:
            if frame.tick < previous_tick or frame.event_count < previous_count or frame.turn < previous_turn:
                raise ValueError("frames must be chronological")
            if abs(frame.simulation_time - frame.tick * self.config.step_ms / 1000) > 1e-8:
                raise ValueError("frame time does not match its tick")
            if frame.event_count > len(self.events):
                raise ValueError("frame references missing events")
            if set(frame.fighters) != {"p1", "p2"} or set(frame.tools) != {"p1", "p2"}:
                raise ValueError("frame must contain both fighters and tool sets")
            for player, fighter in frame.fighters.items():
                if fighter.fighter_id != player:
                    raise ValueError("invalid fighter identity")
                if any(getattr(fighter, resource) > self.config.resource_limit(player, resource)
                       for resource in ("health", "stamina", "mana")):
                    raise ValueError("invalid fighter resources")
                if not self.config.fighter_radius - 1e-8 <= fighter.position <= (
                    self.config.arena_width - self.config.fighter_radius + 1e-8
                ):
                    raise ValueError("fighter is outside arena")
            previous_tick, previous_count = frame.tick, frame.event_count
            previous_turn = frame.turn
        final = self.frames[-1]
        if final.result is None or final.event_count != len(self.events):
            raise ValueError("recording is incomplete")
        if self.summary.get("result") != final.result.model_dump():
            raise ValueError("summary does not match final frame")
        if final.simulation_time > self.config.time_limit_ms / 1000:
            raise ValueError("recording exceeds game time limit")
        for sequence in (self.events, self.decisions):
            times = [item.simulation_time for item in sequence]
            if times != sorted(times) or any(time > final.simulation_time for time in times):
                raise ValueError("invalid event or decision times")
        return self


def record_match(p1: str = "test", p2: str = "test",
                 config: GameConfig | None = None) -> Recording:
    arena = Arena(config)
    frames = [arena.playback_frame()]
    decisions = []
    summary = run_match(
        arena, {"p1": AGENTS[p1](), "p2": AGENTS[p2]()},
        on_frame=frames.append, on_decision=decisions.append,
    )
    return Recording(
        replay_id=uuid4().hex, created_at=datetime.now(timezone.utc),
        config_hash=config_digest(arena.config), config=arena.config,
        agents={"p1": p1, "p2": p2}, frames=frames,
        decisions=decisions, events=[asdict(event) for event in arena.events],
        summary=summary,
    )


def encode_recording(recording: Recording) -> str:
    data = recording.model_dump(mode="json", exclude_none=False)
    header = {key: value for key, value in data.items()
              if key not in ("frames", "decisions", "events", "summary")}
    lines = [{"type": "header", "data": header}]
    for name, kind in (("frames", "frame"), ("decisions", "decision"), ("events", "event")):
        lines.extend({"type": kind, "data": item} for item in data[name])
    lines.append({"type": "summary", "data": data["summary"]})
    return "\n".join(json.dumps(line, separators=(",", ":"), ensure_ascii=False)
                     for line in lines) + "\n"


def decode_recording(payload: str) -> Recording:
    lines = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if not lines or not all(isinstance(line, dict) for line in lines):
        raise ValueError("invalid JSONL recording")
    if lines[0].get("type") != "header" or lines[-1].get("type") != "summary":
        raise ValueError("recording requires header and final summary")
    data = dict(lines[0]["data"])
    data.update(frames=[], decisions=[], events=[], summary=lines[-1]["data"])
    fields = {"frame": "frames", "decision": "decisions", "event": "events"}
    for line in lines[1:-1]:
        if line.get("type") not in fields:
            raise ValueError("unknown recording entry")
        data[fields[line["type"]]].append(line["data"])
    return Recording.model_validate(data)
