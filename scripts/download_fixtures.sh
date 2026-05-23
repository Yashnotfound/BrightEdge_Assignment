#!/usr/bin/env bash
# Download HTML for the three test URLs into tests/fixtures/.
# Useful so unit tests can run offline and deterministically.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p tests/fixtures

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"

curl -sL -A "$UA" \
  "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/" \
  -o tests/fixtures/amazon_toaster.html || echo "Amazon fetch failed (expected with anti-bot); we ship a saved copy."

curl -sL -A "$UA" \
  "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/" \
  -o tests/fixtures/rei_outdoors.html

curl -sL -A "$UA" \
  "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai" \
  -o tests/fixtures/cnn_tech.html

echo "Fixture sizes:"
wc -c tests/fixtures/*.html
