#!/usr/bin/env python3
"""Revert the per-route changes and add middleware-based auth instead.

Strategy: 
1. Revert the file by removing all injected 'current_user: dict = Depends(get_current_user)' params
2. Add middleware that protects all /api/ routes (except /api/auth/* and /health)
   - Checks Authorization header
   - Validates JWT
   - Attaches user via request.state
"""

import re
import sys

path = sys.argv[1]

with open(path) as f:
    content = f.read()

# Step 1: Remove all injected auth params
# Pattern: current_user: dict = Depends(get_current_user), 
# or , current_user: dict = Depends(get_current_user) if it was added at end
content = content.replace('current_user: dict = Depends(get_current_user), ', '')
content = content.replace(', current_user: dict = Depends(get_current_user)', '')
content = content.replace('current_user: dict = Depends(get_current_user)', '')

# Clean up any double commas or trailing commas
content = content.replace('(, ', '(')
content = content.replace(', )', ')')
content = content.replace(',,', ',')

# Step 2: Add middleware after the CORS middleware setup (around line 47-53)
# We need to inject the auth middleware
middleware_code = '''
from auth import decode_access_token, get_user_by_id
from starlette.requests import Request
from starlette.responses import JSONResponse

@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    """Protect all /api/ routes (except auth, health, docs)."""
    path = request.url.path
    # Public routes
    public_prefixes = ["/api/auth/", "/health", "/docs", "/openapi.json", "/redoc", "/swagger", "/favicon"]
    if not path.startswith("/api/"):
        return await call_next(request)
    for prefix in public_prefixes:
        if path.startswith(prefix):
            return await call_next(request)
    
    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"}
        )
    token = auth_header[7:]
    payload = decode_access_token(token)
    if payload is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired token"}
        )
    user_id = payload.get("sub")
    if user_id is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid token payload"}
        )
    user = get_user_by_id(user_id)
    if user is None or not user["is_active"]:
        return JSONResponse(
            status_code=401,
            content={"detail": "User not found or inactive"}
        )
    # Attach user to request state
    request.state.current_user = user
    request.state.current_entity_id = payload.get("entity_id")
    
    response = await call_next(request)
    return response
'''

# Find the CORS middleware section and add after it
insert_marker = "app.add_middleware("
# Find the closing of CORS middleware block
cors_end = content.find(")\n", content.find(insert_marker))
if cors_end > 0:
    content = content[:cors_end+1] + middleware_code + content[cors_end+1:]

with open(path, 'w') as f:
    f.write(content)

print("✅ Reverted per-route changes and added middleware")
