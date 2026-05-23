#!/usr/bin/env bash
# Deploy the SAM stack. Run from repo root.
# Requires: aws-sam-cli installed, AWS credentials configured for us-east-1.
set -euo pipefail

cd "$(dirname "$0")/.."

sam build --template infra/template.yaml --use-container=false
sam deploy --template-file .aws-sam/build/template.yaml --config-file ../samconfig.toml
