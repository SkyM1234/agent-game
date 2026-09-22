from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote, urlsplit

from .recording import Recording, decode_recording, encode_recording
from .turn_log import render_turn_log


class ReplayStore:
    """Completed exports plus a transactional per-turn combat journal."""

    def __init__(self, directory: Path, database_url: str | None = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_directory = self.directory / "logs"
        self.database = self.directory / "index.sqlite3"
        self.database_url = database_url
        self.dialect = "mysql" if database_url and urlsplit(database_url).scheme in (
            "mysql", "mysql+pymysql",
        ) else "sqlite"
        if database_url and self.dialect != "mysql" and not database_url.startswith("sqlite:///"):
            raise ValueError("ARENA_DATABASE_URL must use mysql://, mysql+pymysql://, or sqlite:///")
        if database_url and database_url.startswith("sqlite:///"):
            self.database = Path(database_url.removeprefix("sqlite:///"))
            self.database.parent.mkdir(parents=True, exist_ok=True)
        if self.dialect == "mysql":
            self._ensure_mysql_database()
        self._initialize()

    def _mysql_parameters(self, *, include_database: bool) -> dict:
        parsed = urlsplit(self.database_url)
        database = unquote(parsed.path.lstrip("/"))
        if not parsed.hostname or not re.fullmatch(r"[A-Za-z0-9_$-]{1,64}", database):
            raise ValueError("MySQL URL requires a valid host and database name")
        parameters = {
            "host": parsed.hostname, "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
            "charset": "utf8mb4", "autocommit": False,
        }
        if include_database:
            parameters["database"] = database
        return parameters

    @staticmethod
    def _pymysql():
        try:
            import pymysql
        except ImportError as error:
            raise RuntimeError("MySQL persistence requires PyMySQL") from error
        return pymysql

    def _ensure_mysql_database(self) -> None:
        pymysql = self._pymysql()
        try:
            existing = pymysql.connect(**self._mysql_parameters(include_database=True))
        except pymysql.err.OperationalError as error:
            if not error.args or error.args[0] != 1049:
                raise
        else:
            existing.close()
            return

        parameters = self._mysql_parameters(include_database=False)
        database = unquote(urlsplit(self.database_url).path.lstrip("/"))
        connection = pymysql.connect(**parameters)
        try:
            cursor = connection.cursor()
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            connection.commit()
            cursor.close()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _connection(self):
        if self.dialect == "sqlite":
            connection = sqlite3.connect(self.database)
        else:
            connection = self._pymysql().connect(
                **self._mysql_parameters(include_database=True),
            )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.dialect == "mysql" else statement

    def _initialize(self) -> None:
        text_type = "LONGTEXT" if self.dialect == "mysql" else "TEXT"
        integer_type = "BIGINT" if self.dialect == "mysql" else "INTEGER"
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS replays (
                    replay_id VARCHAR(32) PRIMARY KEY, created_at VARCHAR(40) NOT NULL,
                    metadata {text_type} NOT NULL
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS combat_matches (
                    replay_id VARCHAR(32) PRIMARY KEY,
                    created_at VARCHAR(40) NOT NULL,
                    updated_at VARCHAR(40) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    header_json {text_type} NOT NULL,
                    initial_frame_json {text_type} NOT NULL,
                    checkpoint_json {text_type} NOT NULL,
                    turn_count {integer_type} NOT NULL DEFAULT 0,
                    summary_json {text_type} NULL
                )
            """)
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS combat_turns (
                    replay_id VARCHAR(32) NOT NULL,
                    sequence_no {integer_type} NOT NULL,
                    turn_no {integer_type} NOT NULL,
                    tick {integer_type} NOT NULL,
                    created_at VARCHAR(40) NOT NULL,
                    payload_json {text_type} NOT NULL,
                    PRIMARY KEY (replay_id, sequence_no)
                )
            """)
            cursor.close()

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_id(replay_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", replay_id):
            raise FileNotFoundError("invalid replay id")

    def path(self, replay_id: str) -> Path:
        self._validate_id(replay_id)
        return self.directory / f"{replay_id}.jsonl"

    def turn_log_path(self, replay_id: str, turn_no: int) -> Path:
        self._validate_id(replay_id)
        if turn_no < 1:
            raise ValueError("turn number must be positive")
        return self.log_directory / replay_id / f"turn-{turn_no:06d}.log"

    def save_turn_log(self, replay_id: str, turn_no: int, payload: dict) -> None:
        path = self.turn_log_path(replay_id, turn_no)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(render_turn_log(payload), encoding="utf-8")
        temporary.replace(path)

    def begin_match(self, header: dict, initial_frame: dict, checkpoint: dict) -> None:
        replay_id = header["replay_id"]
        self._validate_id(replay_id)
        now = self._now()
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql(
                "INSERT INTO combat_matches "
                "(replay_id, created_at, updated_at, status, header_json, initial_frame_json, "
                "checkpoint_json, turn_count, summary_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ), (replay_id, header["created_at"], now, "running", self._json(header),
                self._json(initial_frame), self._json(checkpoint), 0, None))
            cursor.close()

    def append_turn(self, replay_id: str, sequence_no: int, turn_no: int, tick: int,
                    payload: dict, checkpoint: dict) -> None:
        self._validate_id(replay_id)
        now = self._now()
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql(
                "INSERT INTO combat_turns "
                "(replay_id, sequence_no, turn_no, tick, created_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ), (replay_id, sequence_no, turn_no, tick, now, self._json(payload)))
            cursor.execute(self._sql(
                "UPDATE combat_matches SET updated_at = ?, checkpoint_json = ?, turn_count = ? "
                "WHERE replay_id = ? AND status = ?"
            ), (now, self._json(checkpoint), sequence_no, replay_id, "running"))
            if cursor.rowcount != 1:
                raise FileNotFoundError(replay_id)
            cursor.close()

    def load_checkpoint(self, replay_id: str) -> dict:
        self._validate_id(replay_id)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql(
                "SELECT status, header_json, initial_frame_json, checkpoint_json, turn_count "
                "FROM combat_matches WHERE replay_id = ?"
            ), (replay_id,))
            row = cursor.fetchone()
            if row is None or row[0] != "running":
                raise FileNotFoundError(replay_id)
            cursor.execute(self._sql(
                "SELECT payload_json FROM combat_turns WHERE replay_id = ? ORDER BY sequence_no"
            ), (replay_id,))
            turns = [json.loads(item[0]) for item in cursor.fetchall()]
            cursor.close()
        if len(turns) != row[4]:
            raise ValueError("combat journal is incomplete")
        return {
            "header": json.loads(row[1]), "initial_frame": json.loads(row[2]),
            "checkpoint": json.loads(row[3]), "turn_count": row[4], "turns": turns,
        }

    def list_incomplete(self) -> list[dict]:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT replay_id, created_at, updated_at, header_json, turn_count "
                "FROM combat_matches WHERE status = 'running' ORDER BY updated_at DESC"
            )
            rows = cursor.fetchall()
            cursor.close()
        result = []
        for replay_id, created_at, updated_at, header_json, turn_count in rows:
            header = json.loads(header_json)
            result.append({
                "replay_id": replay_id, "created_at": created_at, "updated_at": updated_at,
                "agents": header["agents"], "turn_count": turn_count, "status": "running",
            })
        return result

    def save(self, recording: Recording) -> None:
        path = self.path(recording.replay_id)
        temporary = path.with_suffix(".tmp")
        if path.exists():
            raise FileExistsError(recording.replay_id)
        metadata = {
            "replay_id": recording.replay_id,
            "created_at": recording.created_at.isoformat(),
            "agents": recording.agents, "result": recording.summary["result"],
            "frame_count": len(recording.frames), "config_hash": recording.config_hash,
        }
        try:
            temporary.write_text(encode_recording(recording), encoding="utf-8")
            temporary.replace(path)
            with self._connection() as connection:
                cursor = connection.cursor()
                cursor.execute(self._sql(
                    "INSERT INTO replays (replay_id, created_at, metadata) VALUES (?, ?, ?)"
                ), (recording.replay_id, metadata["created_at"], self._json(metadata)))
                cursor.execute(self._sql(
                    "UPDATE combat_matches SET status = ?, updated_at = ?, summary_json = ? "
                    "WHERE replay_id = ? AND status = ?"
                ), ("completed", self._now(), self._json(recording.summary),
                    recording.replay_id, "running"))
                cursor.close()
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise

    def load(self, replay_id: str) -> Recording:
        return decode_recording(self.path(replay_id).read_text(encoding="utf-8"))

    def delete(self, replay_id: str) -> None:
        path = self.path(replay_id)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql("DELETE FROM replays WHERE replay_id = ?"), (replay_id,))
            replay_deleted = cursor.rowcount
            cursor.execute(self._sql("DELETE FROM combat_turns WHERE replay_id = ?"), (replay_id,))
            cursor.execute(self._sql("DELETE FROM combat_matches WHERE replay_id = ?"), (replay_id,))
            match_deleted = cursor.rowcount
            cursor.close()
            if not replay_deleted and not match_deleted:
                raise FileNotFoundError(replay_id)
        path.unlink(missing_ok=True)

    def list(self) -> list[dict]:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT metadata FROM replays ORDER BY created_at DESC LIMIT 100")
            rows = cursor.fetchall()
            cursor.close()
        return [json.loads(row[0]) for row in rows]
