"""Run Pi's tool loop while Python owns HTTP, validation and combat timing."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
import json
from pathlib import Path
import shutil

from .deepseek import AgentDecision, DeepSeekAgent, ProviderError, build_tools, prompt_messages


BRIDGE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pi_battle.mjs"


class PiRuntime(AbstractAsyncContextManager):
    """One lazy Node process per match, with one independent loop per model player."""

    def __init__(self):
        self.process = None
        self.reader = None
        self.ready = None
        self.agents = {}
        self.requests = set()
        self.start_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def start(self):
        async with self.start_lock:
            if self.process is not None:
                return
            node = shutil.which("node")
            if node is None:
                raise ProviderError("agent_runtime_unavailable")
            self.ready = asyncio.get_running_loop().create_future()
            try:
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    node, str(BRIDGE_SCRIPT), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    limit=4 * 1024 * 1024,
                ))
                try:
                    self.process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    # Retain the process handle so match cleanup can reap it.
                    self.process = await spawn
                    raise
                self.reader = asyncio.create_task(self._read())
                await asyncio.wait_for(asyncio.shield(self.ready), 10)
            except (OSError, TimeoutError):
                raise ProviderError("agent_runtime_unavailable") from None

    async def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionError):
            raise ProviderError("agent_runtime_unavailable") from None

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if message["type"] == "ready":
                    self.ready.set_result(None)
                    continue
                agent = self.agents.get(message.get("player"))
                if agent is None or agent.pending is None or agent.pending.done():
                    continue
                if message["type"] == "request":
                    task = asyncio.create_task(agent.request(message))
                    self.requests.add(task)
                    task.add_done_callback(self.requests.discard)
                elif message["type"] == "action":
                    if message["sequence"] == agent.sequence:
                        agent.pending.set_result(agent.decision)
                elif message["type"] == "failed":
                    agent.pending.set_exception(ProviderError("agent_runtime_unavailable"))
        finally:
            if not self.ready.done():
                self.ready.set_exception(ProviderError("agent_runtime_unavailable"))
            for agent in self.agents.values():
                if agent.pending is not None and not agent.pending.done():
                    agent.pending.set_exception(ProviderError("agent_runtime_unavailable"))

    async def __aexit__(self, *exc):
        cleanup = asyncio.create_task(self.close())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def close(self):
        for task in list(self.requests):
            task.cancel()
        await asyncio.gather(*self.requests, return_exceptions=True)
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    await self.send({"type": "stop"})
                    await asyncio.wait_for(self.process.wait(), 2)
                except (ProviderError, TimeoutError):
                    if self.process.returncode is None:
                        self.process.kill()
                    await self.process.wait()
            if self.reader is not None:
                await asyncio.gather(self.reader, return_exceptions=True)
            self.process.stdin.close()
            try:
                await self.process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass


class PiBattleAgent:
    def __init__(self, runtime: PiRuntime, player: str, provider: DeepSeekAgent):
        self.runtime = runtime
        self.player = player
        self.provider = provider
        self.pending = None
        self.decision = None
        self.observation = None
        self.sequence = 0
        self.history = []
        runtime.agents[player] = self

    async def decide(self, observation: dict) -> AgentDecision:
        await self.runtime.start()
        self.sequence += 1
        self.observation = observation
        self.decision = None
        self.pending = asyncio.get_running_loop().create_future()
        messages = prompt_messages(observation, self.provider.system_prompt)
        await self.runtime.send({
            "type": "observe", "player": self.player, "sequence": self.sequence,
            "system": messages[0]["content"], "observation": messages[1]["content"],
            "tools": build_tools(observation), "history": self.history,
            "model": self.provider.settings.model,
        })
        try:
            return await asyncio.wait_for(self.pending, self.provider.settings.timeout_seconds + 2)
        except TimeoutError:
            raise ProviderError("agent_runtime_unavailable") from None

    async def request(self, message):
        try:
            self.history = message["history"]
            decision = await self.provider.decide(self.observation, messages=message["messages"])
            self.decision = decision
            name = decision.info.selected_tool
            arguments = decision.action.model_dump(exclude_none=True)
            arguments.pop("skill")
            arguments["decision_summary"] = decision.info.summary
            if decision.info.source == "fallback":
                name, arguments = "__arena_fallback", {}
            await self.runtime.send({
                "type": "response", "player": self.player,
                "sequence": self.sequence, "name": name, "arguments": arguments,
                "usage": decision.info.model_dump(include={
                    "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
                }),
            })
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self.pending.done():
                self.pending.set_exception(error)

    def export_state(self) -> dict:
        # Only completed call/result pairs are saved. The engine checkpoint and
        # its recent_turns describe the action currently waiting for settlement.
        return {"pi_history": self.history}

    def restore_state(self, state: dict):
        self.history = state.get("pi_history", [])
