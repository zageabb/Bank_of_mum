from __future__ import annotations

import json
from typing import Any


def snapshot(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, default=str)
