#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this storage bootstrap as root." >&2
  exit 1
fi

APP_DIR=${RRPP_APP_DIR:-/opt/rrpp-agent-bridge}
HOST_USER=${RRPP_HOST_USER:-rrpp}
CONTAINER_UID=${RRPP_CONTAINER_UID:-10001}
CONTAINER_GID=${RRPP_CONTAINER_GID:-10001}

for command in find getent install runuser setfacl setpriv; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Required command is unavailable: ${command}" >&2
    exit 1
  fi
done

if ! id "${HOST_USER}" >/dev/null 2>&1; then
  echo "Host worker user does not exist: ${HOST_USER}" >&2
  exit 1
fi
if [[ ! ${CONTAINER_UID} =~ ^[1-9][0-9]*$ || ! ${CONTAINER_GID} =~ ^[1-9][0-9]*$ ]]; then
  echo "Container UID and GID must be positive integers." >&2
  exit 1
fi

HOST_GROUP=${RRPP_HOST_GROUP:-$(id -gn "${HOST_USER}")}
if ! getent group "${HOST_GROUP}" >/dev/null; then
  echo "Host worker group does not exist: ${HOST_GROUP}" >&2
  exit 1
fi

HOST_UID=$(id -u "${HOST_USER}")
HOST_GID=$(id -g "${HOST_USER}")
STORAGE_DIRS=(
  "${APP_DIR}/var"
  "${APP_DIR}/backups"
  "${APP_DIR}/backup-export"
)

install -d -o "${HOST_USER}" -g "${HOST_GROUP}" -m 0750 "${STORAGE_DIRS[@]}"

directory_acl="u:${HOST_USER}:rwx"
file_acl="u:${HOST_USER}:rw-"
default_acl="d:u:${HOST_USER}:rwx"
if [[ ${HOST_UID} != "${CONTAINER_UID}" ]]; then
  directory_acl+=",u:${CONTAINER_UID}:rwx"
  file_acl+=",u:${CONTAINER_UID}:rw-"
  default_acl+=",d:u:${CONTAINER_UID}:rwx"
fi
if [[ ${HOST_GID} != "${CONTAINER_GID}" ]]; then
  directory_acl+=",g:${CONTAINER_GID}:rwx"
  file_acl+=",g:${CONTAINER_GID}:rw-"
  default_acl+=",d:g:${CONTAINER_GID}:rwx"
fi
directory_acl+=",m::rwx"
file_acl+=",m::rw-"
default_acl+=",d:m::rwx"

for directory in "${STORAGE_DIRS[@]}"; do
  find -P "${directory}" -type d -exec setfacl -m "${directory_acl}" -- {} +
  find -P "${directory}" -type f -exec setfacl -m "${file_acl}" -- {} +
  find -P "${directory}" -type d -exec setfacl -m "${default_acl}" -- {} +
done

probe_paths=()
cleanup_probes() {
  if (( ${#probe_paths[@]} )); then
    rm -f -- "${probe_paths[@]}"
  fi
}
trap cleanup_probes EXIT

for directory in "${STORAGE_DIRS[@]}"; do
  host_probe="${directory}/.rrpp-host-storage-check-${$}"
  container_probe="${directory}/.rrpp-container-storage-check-${$}"
  probe_paths+=("${host_probe}" "${container_probe}")

  runuser -u "${HOST_USER}" -- sh -c 'umask 0077; : > "$1"' sh "${host_probe}"
  setpriv --reuid="${CONTAINER_UID}" --regid="${CONTAINER_GID}" --clear-groups \
    sh -c 'printf x >> "$1"' sh "${host_probe}"

  setpriv --reuid="${CONTAINER_UID}" --regid="${CONTAINER_GID}" --clear-groups \
    sh -c 'umask 0077; : > "$1"' sh "${container_probe}"
  runuser -u "${HOST_USER}" -- sh -c 'printf x >> "$1"' sh "${container_probe}"

  rm -f -- "${host_probe}" "${container_probe}"
done

trap - EXIT
echo "Production storage permissions verified for ${HOST_USER} and ${CONTAINER_UID}:${CONTAINER_GID}."
