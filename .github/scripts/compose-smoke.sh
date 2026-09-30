#!/usr/bin/env bash
# Compose smoke (R2-15, R3-27): boot the shipped stack and send real
# requests through the web nginx, the path unit tests never take.
#
#   local  sign in as the bootstrap admin, change the password, create a
#          project (runs `graphrag init` in the api image), upload a 2 MiB
#          .txt through web:8080, GET /api/ready
#   proxy  boot the proxy-auth overlay against the test IdP
#          (deploy/test-idp) and expect 401 from /api/auth/me
#
# Run from the repo root with a .env (cp .env.example .env, set
# JWT_SECRET); BOOTSTRAP_ADMIN_EMAIL/PASSWORD come from the environment or
# .env. Each mode uses its own compose project and removes it, volumes
# included, on exit. When host port 8080 is taken, point SMOKE_PORT at
# another one and pass an override file that republishes it:
#   SMOKE_PORT=18080 SMOKE_OVERRIDE=/path/ports.yml .github/scripts/compose-smoke.sh local
# Works under bash 3.2 (macOS) and 5.x.
set -euo pipefail

mode=${1:?usage: compose-smoke.sh local|proxy}
port=${SMOKE_PORT:-8080}
base="http://localhost:${port}"

case "$mode" in
  local)
    project=graphrag-smoke
    files=(-f docker-compose.yml)
    env_files=(--env-file .env)
    ;;
  proxy)
    project=graphrag-smoke-proxy
    files=(-f docker-compose.yml -f docker-compose.proxy-auth.yml
      -f deploy/test-idp/docker-compose.test-idp.yml)
    env_files=(--env-file .env --env-file deploy/test-idp/test-idp.env)
    ;;
  *) echo "unknown mode: $mode" >&2; exit 2 ;;
esac
if [ -n "${SMOKE_OVERRIDE:-}" ]; then files+=(-f "$SMOKE_OVERRIDE"); fi

compose() { docker compose -p "$project" "${env_files[@]}" "${files[@]}" "$@"; }

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then compose logs --no-color --tail=200 || true; fi
  compose down -v --remove-orphans > /dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }

# One JSON field of stdin, without depending on jq.
field() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

# `curl` that prints the body and fails on any status but the expected one.
call() {
  expected=$1; shift
  out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' "$@")
  if [ "$code" != "$expected" ]; then
    url=""  # the URL only: the arguments carry the bearer token
    for arg in "$@"; do case "$arg" in http*) url=$arg ;; esac; done
    echo "expected $expected, got $code: $url" >&2
    cat "$out" >&2; echo >&2
    rm -f "$out"
    return 1
  fi
  cat "$out"
  rm -f "$out"
}

step "compose up ($mode)"
compose up -d --build --wait

if [ "$mode" = proxy ]; then
  step "overlay answers /api/* with 401, not a login redirect"
  for _ in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$base/api/auth/me" || true)
    [ "$code" = 401 ] && break
    sleep 2
  done
  [ "$code" = 401 ] || { echo "GET /api/auth/me -> $code, expected 401" >&2; exit 1; }
  echo "GET /api/auth/me -> 401"
  exit 0
fi

# Same precedence as compose: the shell environment wins over .env.
dotenv() { sed -n "s/^$1=//p" .env | tail -n 1 | sed 's/^["'\'']//; s/["'\'']$//'; }
email=${BOOTSTRAP_ADMIN_EMAIL:-$(dotenv BOOTSTRAP_ADMIN_EMAIL)}
password=${BOOTSTRAP_ADMIN_PASSWORD:-$(dotenv BOOTSTRAP_ADMIN_PASSWORD)}

step "api ready through web"
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$base/api/ready" || true)
  [ "$code" = 200 ] && break
  sleep 2
done
call 200 "$base/api/ready"; echo

login() {
  call 200 -X POST "$base/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\": \"$email\", \"password\": \"$1\"}" | field access_token
}

step "bootstrap admin signs in and changes the password"
token=$(login "$password")
new_password="smoke-$(date +%s)-new-password"
call 204 -X POST "$base/api/auth/change-password" -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' \
  -d "{\"current_password\": \"$password\", \"new_password\": \"$new_password\"}"
token=$(login "$new_password")
auth=(-H "Authorization: Bearer $token")

step "create a project (graphrag init in the api image)"
pid=$(call 201 -X POST "$base/api/projects" "${auth[@]}" -H 'Content-Type: application/json' \
  -d '{"name": "smoke", "input_file_type": "text"}' | field id)
echo "project $pid"

step "upload 2 MiB through nginx (past its 1 MiB default body cap)"
doc=$(mktemp)
python3 -c 'import sys; sys.stdout.write(("smoke test line\n" * 131072))' > "$doc"
call 201 -X POST "$base/api/projects/$pid/files" "${auth[@]}" -F "file=@$doc;filename=smoke.txt"
echo
rm -f "$doc"
call 200 "$base/api/projects/$pid/files" "${auth[@]}" | python3 -c '
import json, sys
names = [f["name"] for f in json.load(sys.stdin)["files"]]
assert "smoke.txt" in names, names
print("listed:", names)'

step "smoke passed"
