#!/usr/bin/env python3
"""Route modules for ES Agent Management.

Each module exports a ``register(app)`` function that
registers its routes on a FastAPI instance.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import auth
from . import agents
from . import traces
from . import prompts
from . import eval as eval_module
from . import tether
from . import accounting
from . import jobs
from . import audit
from . import workflow
from . import escalations
from . import tools


def register_routes(app: FastAPI):
    """Register all route modules on the given FastAPI app.

    Call this after creating the app instance but before starting the server.
    The original ``api_server.py`` routes remain intact — these are additive.
    """
    auth.register(app)
    agents.register(app)
    traces.register(app)
    prompts.register(app)
    eval_module.register(app)
    tether.register(app)
    accounting.register(app)
    jobs.register(app)
    audit.register(app)
    workflow.register(app)
    escalations.register(app)
    tools.register(app)
