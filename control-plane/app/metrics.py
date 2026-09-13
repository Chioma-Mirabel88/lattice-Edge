from prometheus_client import Counter, Gauge, Histogram


HTTP_REQUESTS_TOTAL = Counter(
    "lattice_http_requests_total",
    "Total number of HTTP requests handled by the Lattice control plane",
    ["method", "path"],
)

HTTP_REQUEST_DURATION = Histogram(
    "lattice_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

HTTP_RESPONSES_TOTAL = Counter(
    "lattice_http_responses_total",
    "Total number of HTTP responses returned by the Lattice control plane",
    ["method", "path", "status"],
)

DOMAINS_TOTAL = Gauge(
    "lattice_domains_total",
    "Total number of configured domains",
)

ORIGINS_TOTAL = Gauge(
    "lattice_origins_total",
    "Total number of configured origins",
)

HEALTHY_ORIGINS_TOTAL = Gauge(
    "lattice_healthy_origins_total",
    "Total number of currently healthy origins",
)

ENABLED_ORIGINS_TOTAL = Gauge(
    "lattice_enabled_origins_total",
    "Total number of currently enabled origins",
)
