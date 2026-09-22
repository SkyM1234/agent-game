from __future__ import annotations

import os
import asyncio
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import dotenv_values
from pydantic import Field, ValidationError, model_validator

from backend.game import Arena
from backend.game.models import CharacterId, GameConfig, Schema
from backend.game.characters import CharacterConfigError, PromptVariant, character_catalog, with_characters
from backend.game.weapons import WeaponConfigError, weapon_catalog, with_weapons
from backend.game.items import ItemConfigError, item_catalog, with_items
from backend.replay.recording import decode_recording
from backend.replay.store import ReplayStore
from backend.agents.deepseek import DeepSeekSettings, ProviderError
from backend.matches.live import MODEL_AGENT_ID, record_live_match
from backend.evaluation.store import EvaluationInProgressError, EvaluationStore

ROOT = Path(__file__).resolve().parents[2]
MAX_IMPORT_BYTES = 12 * 1024 * 1024


class MatchRequest(Schema):
    p1: Literal["test", "heuristic", "deepseek", "counterfactual", "counterfactual_deepseek"] = "test"
    p2: Literal["test", "heuristic", "deepseek", "counterfactual", "counterfactual_deepseek"] = "test"
    config: GameConfig = GameConfig()
    characters: dict[Literal["p1", "p2"], CharacterId] | None = None
    prompt_variants: dict[Literal["p1", "p2"], PromptVariant] | None = None
    weapons: dict[Literal["p1", "p2"], str] | None = None
    items: dict[Literal["p1", "p2"], list[str]] | None = None
    resume_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def bound_recording_size(self):
        if self.prompt_variants is not None and self.characters is None:
            raise ValueError("prompt variants require character selections")
        if self.characters is not None:
            if set(self.characters) != {"p1", "p2"}:
                raise ValueError("choose a character for both players")
            object.__setattr__(self, "config", with_characters(
                self.config, self.characters, prompt_variants=self.prompt_variants,
            ))
        if self.weapons is not None:
            if set(self.weapons) != {"p1", "p2"}:
                raise ValueError("choose a weapon for both players")
            object.__setattr__(self, "config", with_weapons(self.config, self.weapons))
        if self.items is not None:
            if set(self.items) != {"p1", "p2"}:
                raise ValueError("choose items for both players")
            object.__setattr__(self, "config", with_items(self.config, self.items))
        if self.config.time_limit_ms // self.config.step_ms > 2400:
            raise ValueError("web matches allow at most 2400 simulation steps")
        return self


def create_app(data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Agent Arena", version="0.7.0")
    dotenv = dotenv_values(ROOT / ".env", interpolate=False, encoding="utf-8-sig")
    database_url = os.environ.get("ARENA_DATABASE_URL") or dotenv.get("ARENA_DATABASE_URL")
    store = ReplayStore(
        data_dir or Path(os.environ.get("ARENA_DATA_DIR", ROOT / "data")),
        database_url=database_url if data_dir is None else None,
    )
    app.state.store = store
    evaluations = EvaluationStore(store.directory / "evaluations")

    def evaluation_read(operation):
        try:
            return operation()
        except FileNotFoundError as error:
            raise HTTPException(404, "找不到评测报告或回放") from error
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, "评测文件损坏或版本不受支持") from error

    @app.get("/api/evaluations")
    def list_evaluations():
        return evaluations.list()

    @app.get("/api/evaluations/{run_id}")
    def get_evaluation(run_id: str):
        return evaluation_read(lambda: evaluations.detail(run_id))

    @app.delete("/api/evaluations/{run_id}", status_code=204)
    def delete_evaluation(run_id: str):
        try:
            evaluation_read(lambda: evaluations.delete(run_id))
        except EvaluationInProgressError as error:
            raise HTTPException(409, str(error)) from error
        except OSError as error:
            raise HTTPException(500, "删除失败，请检查报告文件是否被占用后重试") from error

    @app.get("/api/evaluations/{run_id}/export")
    def export_evaluation(run_id: str):
        report = evaluation_read(lambda: evaluations.detail(run_id))
        return JSONResponse(report, headers={"Content-Disposition": f'attachment; filename="evaluation-{run_id[:8]}.json"'})

    @app.get("/api/evaluations/{run_id}/replays/{replay_id}")
    def evaluation_replay(run_id: str, replay_id: str):
        return evaluation_read(lambda: evaluations.replay(run_id, replay_id)).model_dump(mode="json")

    @app.get("/api/evaluations/{run_id}/replays/{replay_id}/export")
    def export_evaluation_replay(run_id: str, replay_id: str):
        evaluation_read(lambda: evaluations.replay(run_id, replay_id))
        return FileResponse(evaluations.replay_path(run_id, replay_id), media_type="application/x-ndjson",
                            filename=f"arena-{replay_id[:8]}.jsonl")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.7.0"}

    @app.get("/api/agents")
    def agent_options():
        try:
            settings = DeepSeekSettings.from_env()
            model, ready, reason = settings.model, settings.ready, None
            if not ready:
                reason = "未配置密钥"
        except ProviderError as error:
            model, ready, reason = "deepseek-v4-flash", False, str(error)
        return [
            {"id": "test", "label": "测试", "available": True},
            {"id": "heuristic", "label": "规则机器人", "available": True},
            {"id": "counterfactual", "label": "反事实推演", "available": True},
            {"id": "deepseek", "label": "纯 DeepSeek", "available": ready,
             "model": model, "reason": reason},
            {"id": MODEL_AGENT_ID, "label": "反事实推演 + DeepSeek", "available": ready,
             "model": model, "reason": reason},
        ]

    @app.get("/api/characters")
    def character_options():
        try:
            return [character.model_dump() for character in character_catalog().values()]
        except CharacterConfigError as error:
            raise HTTPException(503, str(error)) from None

    @app.get("/api/weapons")
    def weapon_options():
        try:
            return [weapon.model_dump() for weapon in weapon_catalog().values()]
        except WeaponConfigError as error:
            raise HTTPException(503, str(error)) from None

    @app.get("/api/items")
    def item_options():
        try:
            return [item.model_dump() for item in item_catalog().values()]
        except ItemConfigError as error:
            raise HTTPException(503, str(error)) from None

    @app.get("/api/replays")
    def list_replays():
        return store.list()

    @app.get("/api/matches/incomplete")
    def list_incomplete_matches():
        return store.list_incomplete()

    @app.post("/api/preview")
    def preview_match(body: MatchRequest):
        arena = Arena(body.config)
        return {
            "config": arena.config.model_dump(mode="json"),
            "frame": arena.playback_frame(),
        }

    @app.post("/api/matches", status_code=201)
    def create_match(body: MatchRequest):
        try:
            recording = asyncio.run(record_live_match(
                body.p1, body.p2, body.config, store=store, resume_id=body.resume_id,
            ))
        except ProviderError as error:
            raise HTTPException(503, str(error)) from None
        except FileNotFoundError as error:
            raise HTTPException(404, "找不到可继续的战斗") from error
        return recording.model_dump(mode="json")

    @app.websocket("/api/live")
    async def live_match(socket: WebSocket):
        await socket.accept()
        tasks = []
        try:
            body = MatchRequest.model_validate(await asyncio.wait_for(socket.receive_json(), 30))
            settings = DeepSeekSettings.from_env()
            acknowledgments = asyncio.Queue(maxsize=1)

            async def listen():
                while True:
                    message = await socket.receive_json()
                    if not isinstance(message, dict):
                        raise ValueError("invalid live control")
                    if message.get("type") == "cancel":
                        await socket.send_json({"type": "cancelled"})
                        return
                    if message.get("type") == "next" and acknowledgments.empty():
                        acknowledgments.put_nowait(True)

            async def run():
                recording = await record_live_match(
                    body.p1, body.p2, body.config, settings=settings,
                    on_update=socket.send_json, wait_for_next=acknowledgments.get,
                    store=store, resume_id=body.resume_id,
                )
                await socket.send_json({"type": "finished", "replay": recording.model_dump(mode="json")})

            tasks = [asyncio.create_task(run()), asyncio.create_task(listen())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except ProviderError as error:
            await socket.send_json({"type": "error", "code": error.code, "message": str(error),
                                    "reason": error.reason, "player": error.player})
        except FileNotFoundError:
            await socket.send_json({"type": "error", "code": "resume_not_found",
                                    "message": "找不到可继续的战斗"})
        except (ValidationError, ValueError, TypeError, TimeoutError):
            await socket.send_json({"type": "error", "code": "invalid_request", "message": "实时对局请求无效"})
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await socket.close()
            except RuntimeError:
                pass

    @app.post("/api/replays/import", status_code=201)
    async def import_replay(request: Request):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_IMPORT_BYTES:
                raise HTTPException(413, "回放文件不能超过 12 MB")
        try:
            recording = decode_recording(payload.decode("utf-8-sig"))
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            raise HTTPException(422, "回放文件无效、版本不受支持或内容不完整") from error
        recording = recording.model_copy(update={"replay_id": uuid4().hex})
        store.save(recording)
        return recording.model_dump(mode="json")

    def load(replay_id: str):
        try:
            return store.load(replay_id)
        except FileNotFoundError as error:
            raise HTTPException(404, "找不到这场对局") from error
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, "回放文件损坏") from error

    @app.get("/api/replays/{replay_id}")
    def get_replay(replay_id: str):
        return load(replay_id).model_dump(mode="json")

    @app.delete("/api/replays/{replay_id}", status_code=204)
    def delete_replay(replay_id: str):
        try:
            store.delete(replay_id)
        except FileNotFoundError as error:
            raise HTTPException(404, "找不到这场对局") from error

    @app.get("/api/replays/{replay_id}/export")
    def export_replay(replay_id: str):
        load(replay_id)
        return FileResponse(store.path(replay_id), media_type="application/x-ndjson",
                            filename=f"arena-{replay_id[:8]}.jsonl")

    frontend = ROOT / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:
        @app.get("/")
        def missing_frontend():
            return Response("Frontend not built. Run npm run build in frontend/.",
                            media_type="text/plain", status_code=503)
    return app


app = create_app()
