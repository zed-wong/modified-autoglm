#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -x "${VENV_PY}" ]]; then
  echo "Missing virtual environment: ${ROOT_DIR}/.venv"
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e ."
  exit 1
fi

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  source "${ENV_FILE}"
  set +a
fi

export PHONE_AGENT_BASE_URL="${PHONE_AGENT_BASE_URL:-}"
export PHONE_AGENT_MODEL="${PHONE_AGENT_MODEL:-}"

if [[ "${PHONE_AGENT_BASE_URL}" == "" ]]; then
  echo "Please set PHONE_AGENT_BASE_URL in ${ENV_FILE}"
  echo "Template: ${ROOT_DIR}/.env.example"
  exit 1
fi

if [[ "${PHONE_AGENT_MODEL}" == "" ]]; then
  echo "Please set PHONE_AGENT_MODEL in ${ENV_FILE}"
  echo "Template: ${ROOT_DIR}/.env.example"
  exit 1
fi

if [[ "${PHONE_AGENT_API_KEY:-}" == "" || "${PHONE_AGENT_API_KEY:-}" == "YOUR_DEEPSEEK_API_KEY" || "${PHONE_AGENT_API_KEY:-}" == "YOUR_ANTIGRAVITY_API_KEY" || "${PHONE_AGENT_API_KEY:-}" == "YOUR_GLM_API_KEY" ]]; then
  echo "Please set PHONE_AGENT_API_KEY in ${ENV_FILE}"
  echo "Template: ${ROOT_DIR}/.env.example"
  exit 1
fi

exec "${VENV_PY}" "${ROOT_DIR}/main.py" --auto-confirm-sensitive --memory-file "${ROOT_DIR}/memory.json" --batch-actions --batch-size 3 "$@"
