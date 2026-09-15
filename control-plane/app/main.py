import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from app.audit import emit_event
from app.metrics import (
    DOMAINS_TOTAL,
    ENABLED_ORIGINS_TOTAL,
    HEALTHY_ORIGINS_TOTAL,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_TOTAL,
    HTTP_RESPONSES_TOTAL,
    ORIGINS_TOTAL,
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "lattice-control-plane",
            "event": getattr(record, "event", "application"),
        }

        for field in (
            "request_id",
            "method",
            "path",
            "status",
            "duration_ms",
        ):
            value = getattr(record, field, None)
            if value is not None:
                event[field] = value

        return json.dumps(event)


logger = logging.getLogger("lattice-control-plane")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())

logger.handlers.clear()
logger.addHandler(handler)
logger.propagate = False


HEARTBEAT_TIMEOUT_SECONDS = 30
LIFECYCLE_CHECK_INTERVAL_SECONDS = 5


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def lifecycle_loop():
        while True:
            await check_edge_node_lifecycle()
            await asyncio.sleep(LIFECYCLE_CHECK_INTERVAL_SECONDS)

    lifecycle_task = asyncio.create_task(lifecycle_loop())

    try:
        yield
    finally:
        lifecycle_task.cancel()
        try:
            await lifecycle_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    lifespan=lifespan,
    title="Lattice Control Plane",
    version="0.4.0",
)


HEALTH_STATE_FILE = Path("/state/origins.state")
CONFIG_FILE = Path("/config/domains.json")
EDGE_NODES_FILE = Path("/config/edge-nodes.json")


class Origin(BaseModel):
    name: str
    address: str
    port: int = 80
    enabled: bool = True


class Domain(BaseModel):
    hostname: str
    origins: List[Origin]


class DomainCreate(BaseModel):
    hostname: str


class OriginUpdate(BaseModel):
    address: str | None = None
    port: int | None = None
    enabled: bool | None = None


class OriginCreate(BaseModel):
    name: str
    address: str
    port: int = 80
    enabled: bool = True


class EdgeNode(BaseModel):
    node_id: str
    name: str
    status: str
    runtime: str
    config_version: int
    registered_at: str
    last_heartbeat: str | None = None


class EdgeNodeRegister(BaseModel):
    node_id: str
    name: str
    runtime: str


class EdgeNodeHeartbeat(BaseModel):
    config_version: int


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        duration_seconds = time.perf_counter() - start_time
        duration_ms = round(duration_seconds * 1000, 2)

        if request.url.path != "/metrics":
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method,
                path=request.url.path,
            ).inc()

            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                path=request.url.path,
            ).observe(duration_seconds)

            HTTP_RESPONSES_TOTAL.labels(
                method=request.method,
                path=request.url.path,
                status="500",
            ).inc()

        logger.error(
            "http_request_failed",
            extra={
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "duration_ms": duration_ms,
            },
            exc_info=True,
        )

        raise

    duration_seconds = time.perf_counter() - start_time
    duration_ms = round(duration_seconds * 1000, 2)

    if request.url.path != "/metrics":
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            path=request.url.path,
        ).inc()

        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            path=request.url.path,
        ).observe(duration_seconds)

        HTTP_RESPONSES_TOTAL.labels(
            method=request.method,
            path=request.url.path,
            status=str(response.status_code),
        ).inc()

    response.headers["X-Request-ID"] = request_id

    logger.info(
        "http_request",
        extra={
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    return response


def load_domains():
    if not CONFIG_FILE.exists():
        return {}

    with CONFIG_FILE.open("r") as file:
        data = json.load(file)

    return {
        hostname: Domain(**domain)
        for hostname, domain in data.get("domains", {}).items()
    }


def save_domains(domains):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "domains": {
            hostname: domain.model_dump()
            for hostname, domain in domains.items()
        }
    }

    temp_file = CONFIG_FILE.with_suffix(".tmp")

    with temp_file.open("w") as file:
        json.dump(data, file, indent=2)

    temp_file.replace(CONFIG_FILE)


def load_edge_nodes():
    if not EDGE_NODES_FILE.exists():
        return {}

    with EDGE_NODES_FILE.open("r") as file:
        data = json.load(file)

    return {
        node_id: EdgeNode(**node)
        for node_id, node in data.get("edge_nodes", {}).items()
    }


def save_edge_nodes(edge_nodes):
    EDGE_NODES_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "edge_nodes": {
            node_id: node.model_dump()
            for node_id, node in edge_nodes.items()
        }
    }

    temp_file = EDGE_NODES_FILE.with_suffix(".tmp")

    with temp_file.open("w") as file:
        json.dump(data, file, indent=2)

    temp_file.replace(EDGE_NODES_FILE)


async def check_edge_node_lifecycle():
    edge_nodes = load_edge_nodes()
    now = datetime.now(timezone.utc)

    changed = False

    for node in edge_nodes.values():
        if not node.last_heartbeat:
            continue

        last_heartbeat = datetime.fromisoformat(
            node.last_heartbeat.replace("Z", "+00:00")
        )

        heartbeat_age = (now - last_heartbeat).total_seconds()

        if (
            node.status == "active"
            and heartbeat_age > HEARTBEAT_TIMEOUT_SECONDS
        ):
            node.status = "offline"

            emit_event(
                event_type="EDGE_NODE_OFFLINE",
                resource_type="edge_node",
                resource_id=node.node_id,
                status="warning",
                details={
                    "last_heartbeat": node.last_heartbeat,
                    "heartbeat_age_seconds": round(heartbeat_age, 2),
                },
            )

            changed = True

    if changed:
        save_edge_nodes(edge_nodes)


def read_health_state():
    health = {}

    if not HEALTH_STATE_FILE.exists():
        return health

    with HEALTH_STATE_FILE.open("r") as file:
        for line in file:
            line = line.strip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            health[key] = value

    return health


def get_eligible_origins(hostname: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return None

    health = read_health_state()

    eligible = []

    for origin in domain.origins:
        origin_health = health.get(origin.name)

        if origin.enabled and origin_health == "HEALTHY":
            eligible.append(origin)

    return eligible


@app.get("/")
def root():
    return {
        "service": "Lattice Control Plane",
        "version": "0.4.0",
        "status": "healthy",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


@app.get("/metrics")
def metrics():
    domains = load_domains()

    total_domains = len(domains)
    total_origins = 0
    enabled_origins = 0
    healthy_origins = 0

    health = read_health_state()

    for domain in domains.values():
        for origin in domain.origins:
            total_origins += 1

            if origin.enabled:
                enabled_origins += 1

            if health.get(origin.name) == "HEALTHY":
                healthy_origins += 1

    DOMAINS_TOTAL.set(total_domains)
    ORIGINS_TOTAL.set(total_origins)
    ENABLED_ORIGINS_TOTAL.set(enabled_origins)
    HEALTHY_ORIGINS_TOTAL.set(healthy_origins)

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/domains")
def get_domains():
    return list(load_domains().values())


@app.post("/domains", status_code=201)
def create_domain(request: DomainCreate):
    domains = load_domains()

    hostname = request.hostname.strip().lower()

    if not hostname:
        return {
            "error": "invalid_hostname",
            "message": "Hostname cannot be empty",
        }

    if hostname in domains:
        return {
            "error": "domain_exists",
            "hostname": hostname,
        }

    domain = Domain(
        hostname=hostname,
        origins=[],
    )

    domains[hostname] = domain
    save_domains(domains)

    emit_event(
        event_type="DOMAIN_CREATED",
        resource_type="domain",
        resource_id=hostname,
        domain=hostname,
        details={
            "hostname": hostname,
        },
    )

    return domain


@app.delete("/domains/{hostname}")
def delete_domain(hostname: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    del domains[hostname]
    save_domains(domains)

    emit_event(
        event_type="DOMAIN_DELETED",
        resource_type="domain",
        resource_id=hostname,
        domain=hostname,
        details={
            "hostname": hostname,
        },
    )

    return {
        "message": "domain_deleted",
        "hostname": hostname,
    }


@app.post("/domains/{hostname}/origins", status_code=201)
def create_origin(hostname: str, request: OriginCreate):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    origin_name = request.name.strip().lower()

    if not origin_name:
        return {
            "error": "invalid_origin",
            "message": "Origin name cannot be empty",
        }

    for origin in domain.origins:
        if origin.name == origin_name:
            return {
                "error": "origin_exists",
                "hostname": hostname,
                "origin": origin_name,
            }

    origin = Origin(
        name=origin_name,
        address=request.address.strip(),
        port=request.port,
        enabled=request.enabled,
    )

    domain.origins.append(origin)
    domains[hostname] = domain
    save_domains(domains)

    emit_event(
        event_type="ORIGIN_CREATED",
        resource_type="origin",
        resource_id=origin_name,
        domain=hostname,
        details={
            "address": origin.address,
            "port": origin.port,
            "enabled": origin.enabled,
        },
    )

    return origin


@app.get("/domains/{hostname}/origins")
def get_origins(hostname: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    return {
        "hostname": hostname,
        "origins": domain.origins,
    }


@app.put("/domains/{hostname}/origins/{origin_name}")
def update_origin(
    hostname: str,
    origin_name: str,
    request: OriginUpdate,
):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    origin = next(
        (origin for origin in domain.origins if origin.name == origin_name),
        None,
    )

    if not origin:
        return {
            "error": "origin_not_found",
            "hostname": hostname,
            "origin": origin_name,
        }

    if request.address is not None:
        origin.address = request.address.strip()

    if request.port is not None:
        origin.port = request.port

    if request.enabled is not None:
        origin.enabled = request.enabled

    save_domains(domains)

    emit_event(
        event_type="ORIGIN_UPDATED",
        resource_type="origin",
        resource_id=origin_name,
        domain=hostname,
        details={
            "address": origin.address,
            "port": origin.port,
            "enabled": origin.enabled,
        },
    )

    return origin


@app.delete("/domains/{hostname}/origins/{origin_name}")
def delete_origin(hostname: str, origin_name: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    origin = next(
        (origin for origin in domain.origins if origin.name == origin_name),
        None,
    )

    if not origin:
        return {
            "error": "origin_not_found",
            "hostname": hostname,
            "origin": origin_name,
        }

    domain.origins.remove(origin)
    domains[hostname] = domain

    save_domains(domains)

    emit_event(
        event_type="ORIGIN_DELETED",
        resource_type="origin",
        resource_id=origin_name,
        domain=hostname,
        details={
            "hostname": hostname,
            "origin": origin_name,
        },
    )

    return {
        "message": "origin_deleted",
        "hostname": hostname,
        "origin": origin_name,
    }


@app.get("/domains/{hostname}")
def get_domain(hostname: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    return domain


@app.get("/origins/health")
def origins_health():
    return read_health_state()


@app.get("/domains/{hostname}/eligible-origins")
def eligible_origins(hostname: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    health = read_health_state()
    eligible = get_eligible_origins(hostname)

    return {
        "hostname": hostname,
        "health": health,
        "eligible_origins": eligible,
    }


@app.get("/domains/{hostname}/edge-config")
def edge_config(hostname: str):
    domains = load_domains()
    domain = domains.get(hostname)

    if not domain:
        return {
            "error": "domain_not_found",
            "hostname": hostname,
        }

    eligible = get_eligible_origins(hostname)

    return {
        "hostname": hostname,
        "origins": [
            {
                "name": origin.name,
                "address": origin.address,
                "port": origin.port,
            }
            for origin in eligible
        ],
    }


@app.post("/edge-nodes/register", status_code=201)
def register_edge_node(request: EdgeNodeRegister):
    edge_nodes = load_edge_nodes()

    node_id = request.node_id.strip()
    name = request.name.strip()
    runtime = request.runtime.strip()

    if not node_id:
        return {
            "error": "invalid_node_id",
            "message": "Node ID cannot be empty",
        }

    if not name:
        return {
            "error": "invalid_node_name",
            "message": "Node name cannot be empty",
        }

    if not runtime:
        return {
            "error": "invalid_runtime",
            "message": "Runtime cannot be empty",
        }

    if node_id in edge_nodes:
        return {
            "error": "edge_node_exists",
            "node_id": node_id,
        }

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    node = EdgeNode(
        node_id=node_id,
        name=name,
        status="registered",
        runtime=runtime,
        config_version=0,
        registered_at=now,
        last_heartbeat=now,
    )

    edge_nodes[node_id] = node
    save_edge_nodes(edge_nodes)

    emit_event(
        event_type="EDGE_NODE_REGISTERED",
        resource_type="edge_node",
        resource_id=node_id,
        details={
            "name": name,
            "runtime": runtime,
        },
    )

    return node


@app.get("/edge-nodes")
def get_edge_nodes():
    edge_nodes = load_edge_nodes()

    return {
        "edge_nodes": list(edge_nodes.values()),
    }


@app.post("/edge-nodes/{node_id}/heartbeat")
def edge_node_heartbeat(
    node_id: str,
    request: EdgeNodeHeartbeat,
):
    edge_nodes = load_edge_nodes()

    node = edge_nodes.get(node_id)

    if not node:
        return {
            "error": "edge_node_not_found",
            "node_id": node_id,
        }

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    previous_status = node.status

    node.status = "active"
    node.config_version = request.config_version
    node.last_heartbeat = now

    if previous_status == "offline":
        emit_event(
            event_type="EDGE_NODE_RECONNECTED",
            resource_type="edge_node",
            resource_id=node_id,
            details={
                "previous_status": previous_status,
                "config_version": request.config_version,
            },
        )

    edge_nodes[node_id] = node
    save_edge_nodes(edge_nodes)

    return node
