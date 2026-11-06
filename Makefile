# ESAM — public install path:
#   git clone … && pip install -r requirements.txt && make seed && make serve
# Targets are intentionally minimal so a stranger can run them verbatim.
#
# `make install` is optional (creates .venv + deps); the documented path uses a
# plain venv + `pip install -r requirements.txt`, which these targets honour by
# preferring an existing .venv and falling back to python3.

PYBIN ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PORT  ?= 8000
export PYTHONPATH := src:$(PYTHONPATH)

.PHONY: install seed serve clean

install:
	$(PYBIN) -m venv .venv 2>/dev/null || true
	./.venv/bin/pip install --upgrade pip >/dev/null 2>&1 || $(PYBIN) -m pip install --upgrade pip
	./.venv/bin/pip install -r requirements.txt || $(PYBIN) -m pip install -r requirements.txt

seed:
	$(PYBIN) -c "from database import init_db; from multi_entity import EntityManager; from seed import seed_all; init_db(); EntityManager().ensure_tables(); print('seed complete:', seed_all())"

serve:
	$(PYBIN) -m uvicorn api_server:app --host 0.0.0.0 --port $(PORT)

clean:
	rm -rf .venv build dist *.egg-info
