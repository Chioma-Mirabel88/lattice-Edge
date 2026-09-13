FROM nginx:alpine

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY edge-agent.sh /usr/local/bin/edge-agent.sh
COPY waf/waf.conf /etc/nginx/waf/waf.conf

RUN chmod +x /usr/local/bin/edge-agent.sh

EXPOSE 80 443

CMD ["/bin/sh", "-c", "nginx -g 'daemon off;' & /usr/local/bin/edge-agent.sh"]
