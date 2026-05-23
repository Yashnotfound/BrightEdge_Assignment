# Deployment runbook

How to deploy `brightedge-crawler` to AWS, what each script does, and
the non-obvious gotchas (cross-arch chromium, Lambda image refresh,
account concurrency quota).

Audience: anyone with deploy access. Stack name `brightedge-crawler`,
region `us-east-1`.

## 1. Prerequisites

| Tool | Why | Install |
|---|---|---|
| AWS CLI v2 | All AWS calls. | `brew install awscli` |
| SAM CLI ≥ 1.161 | Builds + deploys the zip-package functions. | `brew install aws-sam-cli` |
| Docker (Desktop or daemon) with buildx | Builds the headless container image. | `brew install --cask docker` |
| `jq` | Used by scripts. | `brew install jq` |
| Python 3.12 + `uv` | Local tests before deploy. | `brew install uv` |

### AWS credentials with MFA

Don't deploy with raw access keys. The repo expects a profile
`brightedge-session` that holds short-lived MFA credentials.

```bash
# One-time: configure the long-lived keys as `brightedge`
aws configure --profile brightedge   # access key + secret

# Every ~12h: mint a session with MFA
read "MFA?MFA code from authenticator: "
STS=$(aws sts get-session-token --profile brightedge \
    --serial-number arn:aws:iam::080050138726:mfa/<your-device> \
    --token-code "$MFA" --duration-seconds 43200)
aws configure set aws_access_key_id "$(echo "$STS" | jq -r .Credentials.AccessKeyId)" --profile brightedge-session
aws configure set aws_secret_access_key "$(echo "$STS" | jq -r .Credentials.SecretAccessKey)" --profile brightedge-session
aws configure set aws_session_token "$(echo "$STS" | jq -r .Credentials.SessionToken)" --profile brightedge-session
aws configure set region us-east-1 --profile brightedge-session
```

Confirm with `AWS_PROFILE=brightedge-session aws sts get-caller-identity`.

### One-time AWS-side bootstrap (if deploying to a fresh account)

```bash
# ECR repo for the headless image — SAM does NOT create this
AWS_PROFILE=brightedge-session aws ecr create-repository \
    --repository-name brightedge-headless --region us-east-1
```

## 2. The two-track build problem

The stack has three Lambda functions:

| Function | PackageType | Architecture | Build path |
|---|---|---|---|
| `ApiFunction` | Zip | arm64 | `sam build` |
| `StaticWorkerFunction` | Zip | arm64 | `sam build` |
| `HeadlessWorkerFunction` | Image | **x86_64** | `docker buildx` (manual) |

`HeadlessWorkerFunction` runs sparticuz/chromium, which only ships x86_64
binaries. On any modern Apple-Silicon dev machine that means the image
build must cross-compile via QEMU. **SAM's docker build path does not
respect `--platform=linux/amd64`** under the legacy docker builder it
shells out to — it silently pulls the arm64 base image and produces a
broken artifact. We bypass it for that one function.

`scripts/deploy.sh` orchestrates both tracks:

1. Build + push the headless image with `docker buildx` (cross-arch safe).
2. Generate a "skip-image" copy of `infra/template.yaml` that strips the
   `Metadata: DockerContext` block on `HeadlessWorkerFunction`, so SAM
   won't try to rebuild the image.
3. `sam build` (zip functions only) + `sam deploy` against that template.
4. Verify the Lambda is resolving to the digest we just pushed.

## 3. Deploying

### Routine deploy (code changes)

```bash
export AWS_PROFILE=brightedge-session
export API_KEY="<token from secret store; same value reviewers received>"

./scripts/deploy.sh
```

The script:
- (Re)builds the headless image, tags it `amd64-vN` (timestamp) **and** `:latest`,
  pushes both to ECR.
- Generates `infra/template-skip-image.yaml` (gitignored).
- Runs `sam build --template-file infra/template-skip-image.yaml`.
- Runs `sam deploy --parameter-overrides ApiKey=$API_KEY --resolve-image-repos`.
- After deploy, forces a `update-function-code` on `HeadlessWorkerFunction`
  to point at the new digest — see [Lambda image refresh](#lambda-image-refresh-gotcha)
  for why this is needed.

ETA: 4–8 minutes (most of it is the chromium pack download inside the
build; cached layers cut subsequent runs to ~2 min).

### Code-only deploy (no Dockerfile / Python deps changed)

The script is idempotent — re-run `./scripts/deploy.sh`. Docker layer
cache makes the image build a no-op (~5s).

### Image-only deploy (chromium tweak)

```bash
./scripts/build-headless-image.sh           # build + push :latest
AWS_PROFILE=brightedge-session aws lambda update-function-code \
    --function-name brightedge-crawler-headless-worker \
    --region us-east-1 \
    --image-uri "$(AWS_PROFILE=brightedge-session aws ecr describe-images \
        --repository-name brightedge-headless --region us-east-1 \
        --query 'imageDetails[?contains(imageTags,`latest`)].[imageDigest][0]' --output text \
        | xargs -I{} echo '080050138726.dkr.ecr.us-east-1.amazonaws.com/brightedge-headless@{}')"
```

### Generating / rotating the API key

```bash
# Mint a 32-char URL-safe key
NEW_KEY=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32)
echo "$NEW_KEY"

# Deploy with the new key (SAM stores it as a NoEcho parameter in CFN)
API_KEY="$NEW_KEY" ./scripts/deploy.sh
```

The plaintext is **never** committed and never printed in CloudFormation
events. To retrieve it later: `aws lambda get-function-configuration
--function-name brightedge-crawler-api --query 'Environment.Variables.API_KEY'`.

## 4. Drift guard

After every deploy, the script verifies:

- `ECR :latest digest == Lambda ResolvedImageUri digest` — catches the
  Lambda-doesn't-auto-refresh-on-:latest gotcha (§5).
- Each Lambda's `Architectures`, `MemorySize`, `Timeout` match the values
  in `infra/template.yaml`.

If you ever suspect drift outside the script's checks:

```bash
# Show every Lambda's current config in one go
for fn in brightedge-crawler-api brightedge-crawler-static-worker brightedge-crawler-headless-worker; do
    AWS_PROFILE=brightedge-session aws lambda get-function-configuration \
        --function-name "$fn" --region us-east-1 \
        --query '{Fn:FunctionName,Arch:Architectures[0],Mem:MemorySize,To:Timeout,LastMod:LastModified,HasApiKey:Environment.Variables.API_KEY!=null}'
done

# Show what ECR thinks :latest is vs what Lambda is running
AWS_PROFILE=brightedge-session aws ecr describe-images \
    --repository-name brightedge-headless --region us-east-1 \
    --query 'imageDetails[?contains(imageTags || `[]`,`latest`)].imageDigest' --output text
AWS_PROFILE=brightedge-session aws lambda get-function \
    --function-name brightedge-crawler-headless-worker --region us-east-1 \
    --query 'Code.ResolvedImageUri' --output text
```

## 5. Known gotchas

### Lambda image refresh

When a CFN template references `ecr...:latest`, CFN sees "no change" if
that string is unchanged across deploys — even though the `:latest` tag
in ECR now points at a different digest. The Lambda keeps running the
**old** digest until you explicitly call `update-function-code` with a
new URI (which is why the script ends with that step) **or** redeploy
with the resolved-digest URI baked into the template.

### Account concurrency quota

Fresh AWS accounts default to **10 concurrent Lambda executions** and
AWS enforces a **minimum of 10 unreserved**. The two limits together
mean *any* `ReservedConcurrentExecutions` value on any function is
rejected:

> Specified ReservedConcurrentExecutions for function decreases account's
> UnreservedConcurrentExecution below its minimum value of [10].

`infra/template.yaml` leaves `ReservedConcurrentExecutions` commented
out on `StaticWorkerFunction` for this reason. Uncomment (suggested
value: 20) once you raise the account quota via Service Quotas console
(Lambda → "Concurrent executions" → Request quota increase).

### Cross-arch image build

If you forget `--platform=linux/amd64` or use `sam build` for the
headless function on Apple Silicon, the resulting image will pull
`aarch64` base layers. It uploads fine and Lambda even runs it — until
the first request, when sparticuz's x86_64 chromium binary fails with
"cannot execute binary file" or hangs forever (the failure mode is
that ugly).

`scripts/build-headless-image.sh` is the only sanctioned path.

### Sparticuz pack layout

`v131.0.0` ships:

```
al2.tar.br        # Amazon Linux 2 system libs
al2023.tar.br     # Amazon Linux 2023 system libs (we use this — base is python:3.12 / AL2023)
chromium.br       # The chromium binary
fonts.tar.br
swiftshader.tar.br
```

Older docs sometimes reference `lib.tar.br` — that's been replaced by
the per-distro `alN.tar.br` files. The Dockerfile pins the version and
verifies the pack's sha256 to lock the layout.

Swiftshader (`libEGL.so`, `libGLESv2.so`, `libvk_swiftshader.so`) must
land **directly** at `/opt/chromium/` — chromium dlopen's them without
a path. AL2023 libs go to `/opt/chromium/lib/` so `LD_LIBRARY_PATH`
picks them up. **No trailing colon** on `LD_LIBRARY_PATH` — empty
entries make the dynamic linker also search `cwd`, which is a
known footgun.

### REI / Akamai

Akamai edge nodes block AWS Lambda IP ranges with HTTP/2 protocol
errors. **Neither the static fetcher nor the headless fetcher can
reach those sites from inside Lambda.** This is an external block, not
a bug. For demo purposes use the fixture mode (`?fixture=1`) or pick
non-Akamai URLs.

## 6. Testing the deploy

```bash
API=https://fhicx1tu0i.execute-api.us-east-1.amazonaws.com
KEY=<from $API_KEY>

# Health (no auth)
curl -sS "$API/health"

# Auth gate
curl -sSo /dev/null -w 'no-auth=%{http_code}\n' "$API/extract" -X POST \
    -H 'Content-Type: application/json' -d '{"url":"https://example.com"}'
curl -sSo /dev/null -w 'auth=%{http_code}\n' "$API/extract" -X POST \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d '{"url":"https://example.com"}'

# Static path
curl -sS "$API/extract" -X POST \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d '{"url":"https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"}' \
    | jq '{fetcher_used, extraction_confidence, title}'

# Escalation path (JS-only site)
curl -sS "$API/extract" -X POST \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d '{"url":"https://quotes.toscrape.com/js/"}' \
    | jq '{fetcher_used, extraction_confidence, word_count, topics: [.topics[].label]}'
# expect fetcher_used == "headless"
```

## 7. Tearing down

```bash
AWS_PROFILE=brightedge-session aws cloudformation delete-stack \
    --stack-name brightedge-crawler --region us-east-1
AWS_PROFILE=brightedge-session aws cloudformation wait stack-delete-complete \
    --stack-name brightedge-crawler --region us-east-1

# ECR repo + images aren't in the stack; nuke separately if you want
AWS_PROFILE=brightedge-session aws ecr delete-repository \
    --repository-name brightedge-headless --region us-east-1 --force
```

The S3 buckets (`brightedge-raw-…`, `brightedge-jobs-…`) are part of
the stack and will only delete if empty. `aws s3 rm s3://bucket --recursive`
before stack-delete if you have data.
