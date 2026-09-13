#!/bin/sh

STATE_DIR="/state"
STATE_FILE="$STATE_DIR/origins.state"
CONTROL_PLANE="http://lattice-control-plane:8000"

mkdir -p "$STATE_DIR"

echo "Lattice Health Monitor starting..."
echo "Control Plane: $CONTROL_PLANE"

check_health() {
    ORIGIN_NAME="$1"
    ORIGIN_ADDRESS="$2"
    ORIGIN_PORT="$3"

    RESPONSE=$(curl -fsS --max-time 3 \
        "http://${ORIGIN_ADDRESS}:${ORIGIN_PORT}/health/" \
        2>/dev/null)

    if [ "$RESPONSE" = "OK" ]; then
        STATUS="HEALTHY"
    else
        STATUS="UNHEALTHY"
    fi

    echo "$ORIGIN_NAME=$STATUS"
}

while true; do
    echo "----------------------------------------"
    echo "Health check: $(date)"

    DOMAINS=$(curl -fsS --retry 3 --retry-delay 2 --max-time 5 \
    "$CONTROL_PLANE/domains" 2>/dev/null)

    if [ -z "$DOMAINS" ]; then
        echo "ERROR: Unable to retrieve domains from Control Plane"
        sleep 5
        continue
    fi

    TMP_STATE="${STATE_FILE}.tmp"

    : > "$TMP_STATE"

    echo "$DOMAINS" |
        sed 's/},{/\n/g' |
        grep -o '"name":"[^"]*","address":"[^"]*","port":[0-9]*,"enabled":[a-z]*' |
        while IFS= read -r origin; do

            NAME=$(echo "$origin" |
                sed 's/.*"name":"\([^"]*\)".*/\1/')

            ADDRESS=$(echo "$origin" |
                sed 's/.*"address":"\([^"]*\)".*/\1/')

            PORT=$(echo "$origin" |
                sed 's/.*"port":\([0-9]*\).*/\1/')

            ENABLED=$(echo "$origin" |
                sed 's/.*"enabled":\([^,]*\).*/\1/')

            if [ "$ENABLED" = "true" ]; then
                check_health "$NAME" "$ADDRESS" "$PORT" |
                    tee -a "$TMP_STATE"
            fi
        done

    echo "timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "$TMP_STATE"

    mv "$TMP_STATE" "$STATE_FILE"

    echo "State updated:"
    cat "$STATE_FILE"

    sleep 5
done
