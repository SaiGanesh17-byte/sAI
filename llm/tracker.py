import json
import threading
import time
from datetime import date
from pathlib import Path
from typing import Optional

# Daily spend in USD, kept across sessions for the optional daily_budget_usd cap.
SPEND_FILE = Path(__file__).resolve().parent.parent / ".sai" / "spend.json"


class TokenTracker:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TokenTracker, cls).__new__(cls, *args, **kwargs)
            cls._instance.input_tokens = 0
            cls._instance.output_tokens = 0
            cls._instance.calls_count = 0
            cls._instance.cost_usd = 0.0
            cls._instance.unpriced_calls = 0
            cls._instance.start_time = time.time()
            cls._instance._lock = threading.Lock()
        return cls._instance

    def reset(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls_count = 0
        self.cost_usd = 0.0
        self.unpriced_calls = 0
        self.start_time = time.time()

    def add(self, prompt_tokens: int, completion_tokens: int, cost_usd: Optional[float] = None):
        with self._lock:
            if prompt_tokens:
                self.input_tokens += prompt_tokens
            if completion_tokens:
                self.output_tokens += completion_tokens
            self.calls_count += 1
            if cost_usd is None:
                self.unpriced_calls += 1  # provider didn't report a price (estimates, non-OpenRouter)
            else:
                self.cost_usd += float(cost_usd)
        if cost_usd:
            _record_spend(float(cost_usd))

    def add_usage(self, usage) -> None:
        """Records an OpenAI-style usage object; OpenRouter adds the call's price as `cost` (USD)."""
        cost = getattr(usage, "cost", None)
        if cost is None and isinstance(getattr(usage, "model_extra", None), dict):
            cost = usage.model_extra.get("cost")
        self.add(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0,
                 float(cost) if cost is not None else None)

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time

    @property
    def speed(self) -> float:
        elapsed = self.elapsed_time
        if elapsed > 0.1:
            return self.output_tokens / elapsed
        return 0.0


_spend_lock = threading.Lock()


def _load_spend() -> dict:
    try:
        return json.loads(SPEND_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _record_spend(usd: float) -> None:
    with _spend_lock:
        data = _load_spend()
        today = date.today().isoformat()
        data[today] = round(data.get(today, 0.0) + usd, 8)
        # keep ~2 months of history
        data = dict(sorted(data.items())[-60:])
        try:
            SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
            SPEND_FILE.write_text(json.dumps(data))
        except OSError:
            pass


def spent_today() -> float:
    return float(_load_spend().get(date.today().isoformat(), 0.0))


def format_usd(usd: float) -> str:
    if usd == 0:
        return "$0"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


token_tracker = TokenTracker()
