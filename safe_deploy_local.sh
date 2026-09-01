#!/bin/bash
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Root deploy script proxy to scripts/deployment - start
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/scripts/deployment/safe_deploy_local.sh" "$@"
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Root deploy script proxy to scripts/deployment - end
