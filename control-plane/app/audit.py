import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


AUDIT_FILE = Path("/state/audit/events.jsonl")


def emit_event(
    event_type: str,
    resource_type: str,
    resource_id: str,
    *,
    domain: str | None = None,
    status: str = "success",
    details: dict | None = None,
):
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "domain": domain,
        "status": status,
        "details": details or {},
    }

    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with AUDIT_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")

    return event
