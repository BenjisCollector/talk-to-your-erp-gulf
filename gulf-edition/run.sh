#!/usr/bin/env bash
# One-command local demo. Needs only Python 3.9+; no Azure, no pip install.
cd "$(dirname "$0")" && exec python3 -m erp.server
