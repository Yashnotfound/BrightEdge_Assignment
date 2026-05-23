#!/usr/bin/env bash
# Smoke-test the deployed crawler.
# Requires: aws CLI (configured), curl, jq, and the stack 'brightedge-crawler' deployed.
set -euo pipefail
cd "$(dirname "$0")/.."

API_URL=$(aws cloudformation describe-stacks --stack-name brightedge-crawler \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)

echo "==> Health"
curl -fsS "$API_URL/health" | jq .

echo "==> Sync /extract — REI"
curl -fsX POST "$API_URL/extract" -H 'Content-Type: application/json' -d '{
  "url":"http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"
}' | jq '{title, topics: .topics[:5], confidence: .extraction_confidence, fetcher: .fetcher_used}'

echo "==> Sync /extract — CNN"
curl -fsX POST "$API_URL/extract" -H 'Content-Type: application/json' -d '{
  "url":"https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"
}' | jq '{title, topics: .topics[:5], confidence: .extraction_confidence, fetcher: .fetcher_used}'

echo "==> Sync /extract — Amazon (may use headless or fixture)"
curl -fsX POST "$API_URL/extract" -H 'Content-Type: application/json' -d '{
  "url":"http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"
}' | jq '{title, topics: .topics[:5], confidence: .extraction_confidence, fetcher: .fetcher_used}' \
  || echo "(headless/network issue, trying fixture mode)"

curl -fsX POST "$API_URL/extract?fixture=1" -H 'Content-Type: application/json' -d '{
  "url":"http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"
}' | jq '{title, topics: .topics[:5], fetcher: .fetcher_used, errors}'

echo "==> Batch + Jobs"
JOB=$(curl -sX POST "$API_URL/batch" -H 'Content-Type: application/json' -d '{
  "urls": [
    "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/",
    "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"
  ]
}' | jq -r .job_id)
echo "Job: $JOB"
for i in 1 2 3 4 5 6; do
  STATUS=$(curl -fsS "$API_URL/jobs/$JOB" | jq -r .status)
  echo "  status=$STATUS"
  if [ "$STATUS" = "complete" ] || [ "$STATUS" = "partial" ]; then break; fi
  sleep 5
done
curl -fsS "$API_URL/jobs/$JOB" | jq .

echo "==> /pages cached lookup"
curl -fsS "$API_URL/pages?url=http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/" \
  | jq '{title, topics: .topics[:3]}'

echo "ALL SMOKE PASSED"
