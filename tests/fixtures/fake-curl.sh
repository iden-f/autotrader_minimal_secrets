#!/usr/bin/env sh
# Stands in for curl in tests/test_keep_time.py: prints a canned body and
# status so every branch of scripts/keep-time.sh can be exercised.
printf '%s\n%s' "${FAKE_BODY:-{}}" "${FAKE_CODE:-204}"
