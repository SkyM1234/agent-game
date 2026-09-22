from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import stat

from backend.replay.recording import decode_recording
from .analysis import analyze_report, backfill_behavior


class EvaluationInProgressError(Exception):
    pass


class EvaluationStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def run_directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise FileNotFoundError("invalid evaluation id")
        return self.directory / run_id

    def save(self, report: dict) -> None:
        directory = self.run_directory(report["run_id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "report.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(directory / "report.json")

    def load(self, run_id: str) -> dict:
        report = json.loads((self.run_directory(run_id) / "report.json").read_text(encoding="utf-8"))
        if report.get("schema_version") != 1 or report.get("run_id") != run_id:
            raise ValueError("invalid evaluation report")
        return report

    def list(self) -> list[dict]:
        result = []
        for path in self.directory.glob("*/report.json"):
            try:
                report = self.load(path.parent.name)
                result.append({key: report[key] for key in (
                    "run_id", "created_at", "status", "strategies", "planned_matches", "completed_matches", "failed_matches",
                )})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def detail(self, run_id: str) -> dict:
        report = self.load(run_id)
        backfill_behavior(report, self.run_directory(run_id))
        report["analysis"] = analyze_report(report)
        return report

    def delete(self, run_id: str) -> None:
        directory = self.run_directory(run_id)
        # Restrict recursive deletion to this exact run, including on Windows junctions.
        attributes = directory.lstat()
        if (directory.resolve() != self.directory.resolve() / run_id
                or stat.S_ISLNK(attributes.st_mode)
                or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("evaluation directory must not be a link")
        if self.load(run_id)["status"] == "running":
            raise EvaluationInProgressError("评测仍在生成中，请结束后再删除")
        shutil.rmtree(directory)

    def replay_path(self, run_id: str, replay_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", replay_id):
            raise FileNotFoundError("invalid replay id")
        report = self.load(run_id)
        if not any(sample.get("replay_id") == replay_id for sample in report["matches"]):
            raise FileNotFoundError("replay not in evaluation")
        return self.run_directory(run_id) / "replays" / f"{replay_id}.jsonl"

    def replay(self, run_id: str, replay_id: str):
        return decode_recording(self.replay_path(run_id, replay_id).read_text(encoding="utf-8"))
