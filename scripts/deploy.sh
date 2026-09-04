#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this deployment script as root." >&2
  exit 1
fi

APP_DIR=${RRPP_APP_DIR:-/opt/rrpp-agent-bridge}
ENV_FILE=${RRPP_ENV_FILE:-/etc/rrpp-agent-bridge/rrpp.env}
export RRPP_ENV_FILE="${ENV_FILE}"
COMPOSE=(docker compose --project-directory "${APP_DIR}" --env-file "${ENV_FILE}")

test -f "${ENV_FILE}"
test -x "${APP_DIR}/.venv/bin/rrpp-bridge"

git -C "${APP_DIR}" pull --ff-only
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade -e "${APP_DIR}[deployment]"
"${COMPOSE[@]}" build web
systemctl stop rrpp-agent-bridge-healthcheck.timer
systemctl stop rrpp-agent-bridge-worker.service
"${COMPOSE[@]}" --profile instagram --profile container-worker stop \
  web worker maintenance instagram
bash "${APP_DIR}/scripts/prepare-production-storage.sh"
"${COMPOSE[@]}" --profile tools run --rm migrate
"${COMPOSE[@]}" --profile instagram up -d --remove-orphans web maintenance instagram

systemctl daemon-reload
systemctl restart rrpp-agent-bridge-worker.service
systemctl enable --now rrpp-agent-bridge-healthcheck.timer

wait_for_health() {
  local service=$1
  local attempt
  for attempt in {1..12}; do
    if "${COMPOSE[@]}" exec -T "${service}" rrpp-bridge healthcheck "${service}"; then
      return 0
    fi
    sleep 5
  done
  echo "Healthcheck failed for ${service}." >&2
  return 1
}

systemctl is-active --quiet rrpp-agent-bridge-worker.service
wait_for_health web
wait_for_health maintenance
wait_for_health instagram

"${COMPOSE[@]}" ps
systemctl --no-pager --full status rrpp-agent-bridge-worker.service
