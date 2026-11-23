#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
python3 -B outputs/phase3d_experiment_execution/phase3d_runner.py "$@"
