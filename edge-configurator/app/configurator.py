import json
import os
import time
import urllib.request
from pathlib import Path


CONTROL_PLANE_URL = os.getenv(
    "CONTROL_PLANE_URL",
    "http://lattice-control-plane:8000",
)

OUTPUT_FILE = os.getenv(
    "OUTPUT_FILE",
    "/config/nginx.conf",
)

INTERVAL = int(
    os.getenv(
        "INTERVAL",
        "5",
    )
)


def fetch_json(url, retries=3, delay=2):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return json.loads(response.read().decode())

        except Exception as error:
            last_error = error

            print(
                f"Control Plane request failed "
                f"(attempt {attempt}/{retries}): {error}"
            )

            if attempt < retries:
                time.sleep(delay)

    raise last_error


def get_domains():
    url = f"{CONTROL_PLANE_URL}/domains"

    return fetch_json(url)


def get_edge_config(hostname):
    url = f"{CONTROL_PLANE_URL}/domains/{hostname}/edge-config"

    return fetch_json(url)


def upstream_name(hostname):
    return "lattice_" + hostname.replace(".", "_").replace("-", "_")


def generate_nginx_config(domains):
    lines = [
        "events {}",
        "",
        "http {",
    ]

    configured_domains = 0
    total_origins = 0

    for domain in domains:
        hostname = domain["hostname"]

        config = get_edge_config(hostname)
        origins = config.get("origins", [])

        if not origins:
            print(
                f"Skipping {hostname}: "
                "no eligible origins available"
            )
            continue

        upstream = upstream_name(hostname)

        lines.extend(
            [
                "",
                f"    upstream {upstream} {{",
            ]
        )

        for origin in origins:
            address = origin["address"]
            port = origin["port"]

            lines.append(
                f"        server {address}:{port};"
            )

        lines.extend(
            [
                "    }",
                "",
                # HTTP → HTTPS redirect
                "    server {",
                "        listen 80;",
                f"        server_name {hostname};",
                "",
                "        return 301 https://$host$request_uri;",
                "    }",
                "",
                # HTTPS
                "    server {",
                "        listen 443 ssl;",
                f"        server_name {hostname};",
                "",
                "        ssl_certificate /etc/nginx/certs/lattice.crt;",
                "        ssl_certificate_key /etc/nginx/certs/lattice.key;",
                "",
                "        include /etc/nginx/waf/waf.conf;",
                "",
                "        location / {",
                f"            proxy_pass http://{upstream};",
                "",
                "            proxy_set_header Host $host;",
                "            proxy_set_header X-Real-IP $remote_addr;",
                "            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "            proxy_set_header X-Forwarded-Proto $scheme;",
                "        }",
                "    }",
            ]
        )

        configured_domains += 1
        total_origins += len(origins)

    lines.extend(
        [
            "",
            # HTTP default server
            "    server {",
            "        listen 80 default_server;",
            "        server_name _;",
            "",
            "        return 404;",
            "    }",
            "",
            # HTTPS default server
            "    server {",
            "        listen 443 ssl default_server;",
            "        server_name _;",
            "",
            "        ssl_certificate /etc/nginx/certs/lattice.crt;",
            "        ssl_certificate_key /etc/nginx/certs/lattice.key;",
            "",
            "        return 404;",
            "    }",
            "}",
            "",
        ]
    )

    if configured_domains == 0:
        raise ValueError(
            "No domains have eligible origins"
        )

    print(
        f"Configuration contains "
        f"{configured_domains} domain(s) and "
        f"{total_origins} eligible origin(s)"
    )

    return "\n".join(lines)



def write_config(config_text):
    output = Path(OUTPUT_FILE)

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = output.with_suffix(".tmp")

    temporary.write_text(config_text)

    temporary.replace(output)


def main():
    print("Lattice Edge Configurator starting...")
    print(f"Control Plane: {CONTROL_PLANE_URL}")
    print(f"Output: {OUTPUT_FILE}")

    while True:
        try:
            domains_response = get_domains()
            domains = domains_response

            nginx_config = generate_nginx_config(domains)

            write_config(nginx_config)

            print("NGINX configuration written successfully.")

        except Exception as error:
            print(f"Configuration error: {error}")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
