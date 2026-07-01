#!/bin/bash
# CI validation script for workflow YAML files
# Run this in CI after every push/PR
set -euo pipefail

echo "🔍 Validating all workflow YAML files..."
cd "$(dirname "$0")/.."

errors=0
for f in workflows/*.yaml; do
    if [[ "$f" == workflows/templates/* ]]; then
        continue
    fi
    echo -n "  Checking $f... "
    if PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -c "
import sys; sys.path.insert(0, 'src')
from workflow_loader import import_one_yaml, validate_workflow
data = import_one_yaml(open('$f').read())
v = validate_workflow(data)
if not v['valid']:
    print('INVALID')
    for e in v['errors']:
        print(f'    - {e}')
    sys.exit(1)
else:
    print('valid')
"; then
        :
    else
        errors=$((errors + 1))
    fi
done

if [ "$errors" -gt 0 ]; then
    echo "❌ $errors workflow(s) have errors"
    exit 1
else
    echo "✅ All workflows valid"
fi
