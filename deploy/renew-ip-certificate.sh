#!/bin/sh
set -eu
deploy_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker run --rm \
  -v "$deploy_dir/letsencrypt:/etc/letsencrypt" \
  -v "$deploy_dir/acme:/var/www/acme" \
  certbot/certbot:v5.4.0 renew --non-interactive --cert-name paper-assistant-ip "$@"
docker compose -p paper-assistant -f "$deploy_dir/compose.yaml" \
  -f "$deploy_dir/compose.https.yaml" exec -T gateway nginx -t
docker compose -p paper-assistant -f "$deploy_dir/compose.yaml" \
  -f "$deploy_dir/compose.https.yaml" exec -T gateway nginx -s reload
