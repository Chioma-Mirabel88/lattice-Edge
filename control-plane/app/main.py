from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
from pathlib import Path
import json

from app.audit import emit_event


app = FastAPI(
    title="Lattice Control Plane",
    version="0.4.0",
)


HEALTH_STATE_FILE = Path("/state/origins.state")
CONFIG_FILE = Path("/config/domains.json")


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
