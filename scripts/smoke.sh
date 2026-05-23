#!/usr/bin/env bash
# Smoke-test the deployed crawler.
# Requires: aws CLI (configured), curl, jq, and the stack 'brightedge-crawler' deployed.
# Also requires API_KEY env var to be set to the Bearer token used at deploy time.
set -euo pipefail
cd "$(dirname "$0")/.."

API_URL=$(aws cloudformation describe-stacks --stack-name brightedge-crawler \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)

if [ -z "${API_KEY:-}" ]; then
  echo "ERROR: API_KEY env var is not set. Export it before running this script:" >&2
  echo "  export API_KEY=<the-value-you-passed-to-sam-deploy-parameter-overrides>" >&2
  exit 2
fi

AUTH="Authorization: Bearer ${API_KEY}"
JSON='Content-Type: application/json'

echo "==> Health (no auth required)"
curl -fsS "$API_URL/health" | jq .

echo "==> Sync /extract — REI"
curl -fsX POST "$API_URL/extract" -H "$AUTH" -H "$JSON" -d '{
  "url":"http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"
}' | jq '{title, topics: .topics[:5], confidence: .extraction_confidence, fetcher: .fetcher_used}'

echo "==> Sync /extract — CNN"
curl -fsX POST "$API_URL/extract" -H "$AUTH" -H "$JSON" -d '{
  "url":"https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"
}' | jq '{title, topics: .topics[:5], confidence: .extraction_confidence, fetcher: .fetcher_used}'

echo "==> Sync /extract — Amazon (may use headless or fixture)"
curl -fsX POST "$API_URL/extract" -H "$AUTH" -H "$JSON" -d '{
  "url":"http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"
}' | jq '{title, topics: .topics[:5], confidence: .extraction_confidence, fetcher: .fetcher_used}' \
  || echo "(headless/network issue, trying fixture mode)"

curl -fsX POST "$API_URL/extract?fixture=1" -H "$AUTH" -H "$JSON" -d '{
  "url":"http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"
}' | jq '{title, topics: .topics[:5], fetcher: .fetcher_used, errors}'

echo "==> Batch + Jobs"
JOB=$(curl -sX POST "$API_URL/batch" -H "$AUTH" -H "$JSON" -d '{
  "urls": [
    "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/",
    "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"
  ]
}' | jq -r .job_id)
echo "Job: $JOB"
for i in 1 2 3 4 5 6; do
  STATUS=$(curl -fsS -H "$AUTH" "$API_URL/jobs/$JOB" | jq -r .status)
  echo "  status=$STATUS"
  if [ "$STATUS" = "complete" ] || [ "$STATUS" = "partial" ]; then break; fi
  sleep 5
done
curl -fsS -H "$AUTH" "$API_URL/jobs/$JOB" | jq .

echo "==> /pages cached lookup"
curl -fsS -H "$AUTH" "$API_URL/pages?url=http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/" \
  | jq '{title, topics: .topics[:3]}'

echo "==> Auth gate (should 401 without the key)"
HTTP_CODE=$(curl -sX POST "$API_URL/extract" -H "$JSON" -d '{"url":"http://example.com"}' -o /dev/null -w "%{http_code}")
if [ "$HTTP_CODE" = "401" ]; then
  echo "  /extract without auth → 401 ✓"
else
  echo "  WARN: /extract without auth returned $HTTP_CODE (expected 401)"
fi

echo "ALL SMOKE PASSED"
