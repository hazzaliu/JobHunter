#!/usr/bin/env bash
#
# setup.sh — bootstrap a fresh JobHunter clone.
#
# Creates a virtualenv, installs dependencies, copies the example config
# files into place if they're not already there, and ensures the
# private_docs/, embeddings/, logs/, and output_docs/ directories exist.
#
# Run from inside the job-scout/ directory:
#     bash scripts/setup.sh
#

set -euo pipefail

# Resolve job-scout/ as the working directory regardless of where the
# script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "==> Bootstrapping JobHunter in ${PROJECT_ROOT}"

# 1. Python version check
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 not found. Install Python 3.10+ first." >&2
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "    Found python3 ${PY_VERSION}"

# 2. Virtualenv
if [ ! -d ".venv" ]; then
    echo "==> Creating virtualenv at .venv"
    python3 -m venv .venv
else
    echo "==> Reusing existing .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Upgrading pip"
python -m pip install --quiet --upgrade pip

echo "==> Installing requirements"
python -m pip install --quiet -r requirements.txt

# 3. Config files
copy_if_missing() {
    local example=$1
    local target=$2
    if [ -f "${target}" ]; then
        echo "    ${target} already exists — leaving it alone"
    elif [ -f "${example}" ]; then
        cp "${example}" "${target}"
        echo "    Copied ${example} → ${target}"
    else
        echo "    ⚠️  ${example} not found; cannot create ${target}"
    fi
}

echo "==> Ensuring config files are in place"
copy_if_missing ".env.example" ".env"
copy_if_missing "config.example.json" "config.json"
copy_if_missing "strategy.example.json" "strategy.json"

# 4. Runtime directories
echo "==> Ensuring runtime directories exist"
mkdir -p private_docs embeddings logs output_docs
echo "    private_docs/ embeddings/ logs/ output_docs/"

# 5. Next steps
cat <<'EOF'

==> Setup complete. Next steps:

  1. Fill in .env with your real API keys:
     - OPENROUTER_API_KEY       (https://openrouter.ai/keys)
     - APIFY_API_TOKEN          (https://console.apify.com/account/integrations)
     - NOTION_TOKEN             (https://www.notion.so/my-integrations)
     - NOTION_DATABASE_ID       (from the Notion DB URL)
     - DISCORD_WEBHOOK_URL      (Server Settings → Integrations → Webhooks)

  2. Edit strategy.json with your target roles, selling points, and search terms.

  3. Drop your CV/resume PDF into private_docs/
     (the filename must contain "resume" or "cv")

  4. Run the preflight validator:
         source .venv/bin/activate
         python run_daily_scout.py --validate

  5. Once validation passes, try a dry run (no Notion/Discord writes):
         python run_daily_scout.py --dry-run

  6. When ready, run the real thing:
         python run_daily_scout.py
EOF
