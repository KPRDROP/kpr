import json
from pathlib import Path

from .config import Time


class Cache:
    now_ts: float = Time.rn().timestamp()

    def __init__(self, filename: str, exp: float) -> None:
        self.file = Path(__file__).parent.parent / "caches" / f"{filename.lower()}.json"

        self.exp = exp

    def is_fresh(self, entry: dict) -> bool:
        ts: float | int = entry.get("timestamp", Time.default_8())

        return self.now_ts - ts < self.exp

    def load(
        self,
        per_entry: bool = True,
        ts_index: int | None = None,
    ) -> dict[str, dict[str, str | float]]:

        try:
            data: dict = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

        if per_entry:
            return {k: v for k, v in data.items() if self.is_fresh(v)}

        ts: float | int = (
            data[ts_index].get("timestamp", Time.default_8())
            if ts_index
            else data.get("timestamp", Time.default_8())
        )

        return data if self.is_fresh({"timestamp": ts}) else {}

    def write(self, data: dict) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)

        self.file.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


__all__ = ["Cache"]
