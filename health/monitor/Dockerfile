FROM alpine:3.20

RUN apk add --no-cache curl

COPY monitor.sh /monitor.sh

RUN chmod +x /monitor.sh

RUN mkdir -p /state

VOLUME ["/state"]

CMD ["/monitor.sh"]
