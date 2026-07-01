#!/usr/bin/env python3
"""Add auth protection to all /api/ routes in api_server.py.

Reads api_server.py, finds every route handler function definition
for routes matching /api/ (except /api/auth/* and /health),
and adds current_user: dict = Depends(get_current_user) as the first parameter.

Handles both sync and async functions.
"""

import re
import sys

path = sys.argv[1]

with open(path) as f:
    content = f.read()

lines = content.split('\n')

# Track which function def lines need the parameter added
# We look for: def funcname(params): where the preceding line
# has @app.(get|post|put|delete)("/api/...") but NOT /api/auth/ or /health
protected_lines = set()

i = 0
while i < len(lines):
    line = lines[i]
    # Check if this is a route decorator for /api/
    m = re.match(r'^\s*@app\.(get|post|put|delete|patch)\("(/api/[^"]*)"\)', line)
    if m:
        route_path = m.group(2)
        # Skip auth routes and health
        if route_path.startswith('/api/auth/') or route_path == '/health':
            i += 1
            continue
        # The next non-comment, non-decorator line should be the function def
        j = i + 1
        while j < len(lines) and (lines[j].strip().startswith('@') or lines[j].strip().startswith('#') or lines[j].strip() == ''):
            j += 1
        if j < len(lines) and 'def ' in lines[j]:
            protected_lines.add(j)
        i = j
    else:
        i += 1

print(f"Found {len(protected_lines)} routes to protect")

# Now add the parameter to each function def
# We need to find the opening ( and insert after it
result = []
for idx, line in enumerate(lines):
    if idx in protected_lines:
        # Found a function def. Add the auth param after the opening paren.
        # Pattern: def funcname(params) -> Type: or def funcname(params):
        # Insert current_user: dict = Depends(get_current_user) as first param
        # Handle multi-line defs by looking for the ( on this line
        line_stripped = line.strip()
        if line_stripped.startswith('async def '):
            # Remove async def prefix to find the params
            pass
        
        if '(' in line and ')' in line:
            # Single-line def
            # def func(...): -> def func(current_user: dict = Depends(get_current_user), ...):
            new_line = line.replace('(', '(current_user: dict = Depends(get_current_user), ', 1)
            result.append(new_line)
        elif '(' in line:
            # Multi-line def, ( on this line, ) on a later line
            # Just add the param after (
            new_line = line.replace('(', '(current_user: dict = Depends(get_current_user), ', 1)
            result.append(new_line)
        else:
            result.append(line)
    else:
        result.append(line)

output = '\n'.join(result)
with open(path, 'w') as f:
    f.write(output)

print("Done writing changes")
