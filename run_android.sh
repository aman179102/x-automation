#!/usr/bin/env bash
# ==============================================================================
# X-Automation 24/7 Android (Termux) Runner — 100% Ban-Proof Setup
# Runs on genuine mobile carrier IP (Jio/Airtel) with zero datacenter bot flags.
# ==============================================================================

set -e

# Acquire Termux wake lock so Android doesn't put script to sleep
if command -v termux-wake-lock &> /dev/null; then
    termux-wake-lock
    echo "🔋 Termux wake-lock acquired (running 24/7 in background)."
fi

cd "$(dirname "$0")"

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "🚀 Starting 100% Ban-Proof X-Automation Engine on Android..."
python -m app.main --daemon --live
