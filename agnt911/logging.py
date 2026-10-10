"""One JSONL file per run. Raw injection requests and signing keys are omitted."""

import json
from pathlib import Path


class RunLog:
    def __init__(self, directory: str | Path, run_id: str):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{run_id}.jsonl"
        self._file = self.path.open("x", encoding="utf-8", buffering=1)
        self.run_id = run_id

    def write(self, event: str, time_s: float, **data) -> None:
        record = {"run_id": self.run_id, "event": event, "time_s": time_s, **data}
        self._file.write(json.dumps(record, allow_nan=False) + "\n")

    def close(self) -> None:
        self._file.close()
