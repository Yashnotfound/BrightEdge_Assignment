#!/usr/bin/env bash
# Build + push the HeadlessWorkerFunction container image to ECR.
#
# Why this exists as a separate script (not just `sam build`):
#   - The image must be linux/amd64 (sparticuz/chromium has no arm64 build).
#   - SAM's docker build path doesn't honor --platform=linux/amd64 reliably
#     on Apple Silicon hosts — it pulls arm64 base layers and silently
#     produces an unrunnable image.
#   - Lambda rejects buildkit's default OCI image manifest; we have to
#     opt out of OCI media types and provenance attestations.
#
# Usage:
#   AWS_PROFILE=brightedge-session ./scripts/build-headless-image.sh
#
# Optional env:
#   ECR_REPO        — full ECR URI (default: derived from sts get-caller-identity)
#   IMAGE_TAG       — explicit tag (default: amd64-<timestamp>); :latest is always
#                     also moved to point at this image
#   AWS_REGION      — default us-east-1
set -euo pipefail

cd "$(dirname "$0")/.."

: "${AWS_REGION:=us-east-1}"
: "${AWS_PROFILE:?Set AWS_PROFILE (e.g. brightedge-session) before running}"

account_id=$(aws sts get-caller-identity --query Account --output text)
: "${ECR_REPO:=${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/brightedge-headless}"
: "${IMAGE_TAG:=amd64-$(date +%s)}"

echo "==> Logging into ECR at ${ECR_REPO%%/*}"
aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "${ECR_REPO%%/*}"

echo "==> Building + pushing ${ECR_REPO}:${IMAGE_TAG} (linux/amd64)"
# --provenance=false + oci-mediatypes=false: Lambda only accepts Docker v2
# manifest format. buildx defaults emit OCI manifests, which Lambda
# rejects with "image manifest, config or layer media type ... is not
# supported".
docker buildx build \
    --platform=linux/amd64 \
    --provenance=false \
    --output "type=image,name=${ECR_REPO}:${IMAGE_TAG},push=true,oci-mediatypes=false" \
    -f infra/headless.Dockerfile \
    .

echo "==> Re-tagging :latest -> ${IMAGE_TAG}"
manifest=$(aws ecr batch-get-image \
    --repository-name "${ECR_REPO##*/}" --region "$AWS_REGION" \
    --image-ids "imageTag=${IMAGE_TAG}" \
    --query 'images[0].imageManifest' --output text)
aws ecr put-image \
    --repository-name "${ECR_REPO##*/}" --region "$AWS_REGION" \
    --image-tag latest \
    --image-manifest "$manifest" \
    --query 'image.imageId' --output json >/dev/null

digest=$(aws ecr describe-images \
    --repository-name "${ECR_REPO##*/}" --region "$AWS_REGION" \
    --query "imageDetails[?contains(imageTags || \`[]\`, \`${IMAGE_TAG}\`)].imageDigest" \
    --output text)

echo "==> Done. Image URI: ${ECR_REPO}@${digest}"
echo "    Tag:            ${IMAGE_TAG}  (also :latest)"
# Emit the digest on stdout for the deploy script to consume
echo "$digest"
