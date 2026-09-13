FROM ubuntu:22.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y bind9 bind9utils \
    && rm -rf /var/lib/apt/lists/*

COPY named.conf /etc/bind/named.conf
COPY zones/db.lattice.test /etc/bind/zones/db.lattice.test

EXPOSE 53/tcp
EXPOSE 53/udp

CMD ["named", "-g", "-c", "/etc/bind/named.conf"]
