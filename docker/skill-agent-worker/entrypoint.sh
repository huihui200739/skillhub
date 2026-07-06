#!/bin/sh
set -e
exec uvicorn skill_runner.worker.app:app --host 0.0.0.0 --port "${SKILL_RUNNER_POD_PORT:-8080}"
