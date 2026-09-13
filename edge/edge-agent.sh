#!/bin/sh

GENERATED_CONFIG="/etc/nginx/generated/nginx.conf"
ACTIVE_CONFIG="/etc/nginx/nginx.conf"
LAST_CONFIG="/tmp/last-config"

echo "Lattice Edge Agent starting..."

echo "Waiting for NGINX..."

while [ ! -f /run/nginx.pid ]; do
    sleep 1
done

echo "NGINX is ready."

while true; do

    if [ -f "$GENERATED_CONFIG" ]; then

        CURRENT_HASH=$(sha256sum "$GENERATED_CONFIG" | cut -d ' ' -f1)

        LAST_HASH=""

        if [ -f "$LAST_CONFIG" ]; then
            LAST_HASH=$(cat "$LAST_CONFIG")
        fi

        if [ "$CURRENT_HASH" != "$LAST_HASH" ]; then

            echo "New edge configuration detected."
            echo "Preparing configuration..."

            cp "$GENERATED_CONFIG" /tmp/nginx-test.conf

            if nginx -t -c /tmp/nginx-test.conf; then

                echo "Generated configuration is valid."

                cp "$GENERATED_CONFIG" "$ACTIVE_CONFIG"

                if nginx -t; then

                    echo "Active configuration validated."

                    if nginx -s reload; then

                        echo "NGINX reload successful."

                        echo "$CURRENT_HASH" > "$LAST_CONFIG"

                    else

                        echo "ERROR: NGINX reload failed."

                    fi

                else

                    echo "ERROR: Active configuration validation failed."

                fi

            else

                echo "ERROR: Generated configuration rejected."

            fi

        fi

    fi

    sleep 2

done
