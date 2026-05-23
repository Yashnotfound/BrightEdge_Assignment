#!/usr/bin/env bash
# End-to-end deploy of the brightedge-crawler SAM stack.
#
# Why this script is not just `sam build && sam deploy`:
#   1. The HeadlessWorkerFunction must be built linux/amd64 via buildx
#      (sam's docker build doesn't honor --platform reliably). See
#      scripts/build-headless-image.sh.
#   2. To stop sam from re-trying that broken build, we deploy with a
#      copy of template.yaml that has the Metadata.DockerContext block
#      stripped off HeadlessWorkerFunction — sam treats it as an
#      already-built image referenced via ImageUri.
#   3. Lambda's image deploy doesn't auto-refresh when the :latest tag
#      in ECR moves under a stable URI. We follow up with an explicit
#      update-function-code against the new digest.
#
# Usage:
#   AWS_PROFILE=brightedge-session API_KEY='<bearer-token>' ./scripts/deploy.sh
#
# See docs/deploy.md for the full runbook (account quota gotcha, ECR repo
# bootstrap, etc.).
set -euo pipefail

cd "$(dirname "$0")/.."

: "${AWS_REGION:=us-east-1}"
: "${AWS_PROFILE:?Set AWS_PROFILE (e.g. brightedge-session)}"
: "${API_KEY:?Set API_KEY to the Bearer token reviewers will use (use empty string for unauthenticated demo mode)}"

stack_name="brightedge-crawler"
src_template="infra/template.yaml"
deploy_template="infra/template-skip-image.yaml"  # gitignored, regenerated below

# ----- 1. Build + push the headless image ---------------------------------
echo
echo "######## 1/4  Build + push headless image ########"
digest=$(./scripts/build-headless-image.sh | tail -n 1)
echo "    digest: $digest"

# ----- 2. Regenerate the skip-image template -------------------------------
echo
echo "######## 2/4  Generate skip-image template ########"
python3 - "$src_template" "$deploy_template" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    t = f.read()
# Remove the Metadata block that tells SAM to docker-build HeadlessWorkerFunction.
# The function stays PackageType: Image with ImageUri pointing at :latest;
# sam deploy will just push that ref to CloudFormation.
t2 = re.sub(
    r"    Metadata:\s*\n"
    r"      DockerTag: latest\s*\n"
    r"      DockerContext: \.\./\s*\n"
    r"      Dockerfile: infra/headless\.Dockerfile\s*\n",
    "",
    t,
)
if t2 == t:
    print("WARN: no Metadata block found to strip; skip-template may be wrong", file=sys.stderr)
with open(dst, "w") as f:
    f.write(t2)
print(f"    wrote {dst}")
PY

# ----- 3. sam build + sam deploy -------------------------------------------
echo
echo "######## 3/4  sam build + sam deploy ########"
sam build --no-cached --template-file "$deploy_template"
sam deploy \
    --parameter-overrides "ApiKey=${API_KEY}" \
    --resolve-image-repos

# ----- 4. Force Lambda to pick up the new image digest ---------------------
# The CFN template references brightedge-headless:latest. CFN sees no
# change to that string across deploys, so it leaves HeadlessWorkerFunction
# pointing at whatever digest :latest resolved to LAST time. Without this
# step the Lambda runs stale code even though :latest was updated.
echo
echo "######## 4/4  Refresh Lambda image to current digest ########"
account_id=$(aws sts get-caller-identity --query Account --output text)
image_uri="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/brightedge-headless@${digest}"

aws lambda update-function-code \
    --function-name brightedge-crawler-headless-worker \
    --region "$AWS_REGION" \
    --image-uri "$image_uri" \
    --query '{State:State,LastUpdate:LastUpdateStatus}' \
    --output json

# Wait for it to settle so the verification step below sees the new digest.
until [ "$(aws lambda get-function-configuration \
        --function-name brightedge-crawler-headless-worker \
        --region "$AWS_REGION" \
        --query 'LastUpdateStatus' --output text 2>/dev/null)" != "InProgress" ]; do
    sleep 3
done

# ----- Drift check ---------------------------------------------------------
echo
echo "######## Drift check ########"
lambda_digest=$(aws lambda get-function \
    --function-name brightedge-crawler-headless-worker \
    --region "$AWS_REGION" \
    --query 'Code.ResolvedImageUri' --output text | grep -oE 'sha256:[a-f0-9]+')
if [ "$lambda_digest" = "$digest" ]; then
    echo "    Lambda digest == ECR digest ✓"
else
    echo "    !!! DRIFT: Lambda=$lambda_digest  ECR=$digest"
    exit 1
fi

api_url=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
echo
echo "######## Deployed ########"
echo "    API:        $api_url"
echo "    Stack:      $stack_name"
echo "    Region:     $AWS_REGION"
echo "    Image:      $image_uri"
echo
echo "    Smoke-test:"
echo "      curl -sS \"\$API/health\""
echo "      curl -sS \"\$API/extract\" -X POST \\"
echo "        -H 'Authorization: Bearer <KEY>' -H 'Content-Type: application/json' \\"
echo "        -d '{\"url\":\"https://quotes.toscrape.com/js/\"}' | jq ."
