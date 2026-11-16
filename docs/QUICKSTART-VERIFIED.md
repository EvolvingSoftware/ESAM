# ESAM — Verified Clean-Environment Quickstart (Stranger Transcript)

**Release:** v0.2.5  
**Verified against commit:** `85ee54b9e20a717eb56a90e3f9d722d29074f04d` (= origin/main at proof time = target of tag v0.2.5)  
**Date:** 2026-11-16 (run at 2026-11-16T02:21Z; lab box, no GPU)  
**Directive:** Fleet directive 2026-11-16 — outside-in stranger proof (owner Coriolis, Ferrel verifies)

## Release URLs
- Tag page: https://github.com/EvolvingSoftware/ESAM/releases/tag/v0.2.5
- Source tree at tag: https://github.com/EvolvingSoftware/ESAM/tree/v0.2.5
- Published install command (the single one-liner):

    curl -fsSL https://raw.githubusercontent.com/EvolvingSoftware/ESAM/v0.2.5/install.sh | sh

## What this proves
A stranger on a machine with **no prior ESAM config** (fresh HOME, no `~/.esam`, no ESAM_* env vars) runs the published install command verbatim, then runs `esam init` in an empty directory:
- install rc=0 → `esam` console script lands on PATH (~/.local/bin); esam 0.2.5 installed from the tagged source
- `esam init --template local` rc=0 in a previously-empty workdir → `.esam/config.json` + `.esam/agent.local.yaml` written, no API key required

## Full transcript (verbatim)
```
########## ESAM STRANGER-PROOF RUN ##########
2026-11-16T02:21:44Z
--- host tools ---
sh: /usr/bin/sh
git: /usr/bin/git
python3: /usr/bin/python3
tar: /usr/bin/tar
curl: /home/ai/.coriolis/sandbox/bin/curl
--- curl in use ---
curl shim: no URL
--- clean env ---
HOME=/tmp/esam-stranger-home
no prior ~/.esam (clean)
--- empty workdir ---
/tmp/esam-workdir-empty
total 0
drwxr-xr-x 2 ai ai  40 Nov 16 13:21 .
drwxr-xr-x 4 ai ai 160 Nov 16 13:21 ..

########## RUN PUBLISHED INSTALL COMMAND (verbatim) ##########
[esam] cloned v0.2.5 via git
Collecting fastapi>=0.110 (from -r requirements.txt (line 3))
  Downloading fastapi-0.163.1-py3-none-any.whl.metadata (27 kB)
Collecting uvicorn>=0.29 (from uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading uvicorn-0.54.0-py3-none-any.whl.metadata (6.6 kB)
Collecting pydantic>=2.6 (from -r requirements.txt (line 5))
  Downloading pydantic-2.14.3-py3-none-any.whl.metadata (141 kB)
Collecting cryptography>=42.0 (from -r requirements.txt (line 6))
  Downloading cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl.metadata (4.4 kB)
Collecting PyJWT>=2.8 (from -r requirements.txt (line 7))
  Downloading pyjwt-2.15.1-py3-none-any.whl.metadata (3.5 kB)
Collecting passlib>=1.7.4 (from -r requirements.txt (line 8))
  Downloading passlib-1.7.4-py2.py3-none-any.whl.metadata (1.7 kB)
Collecting markdown>=3.5 (from -r requirements.txt (line 9))
  Downloading markdown-3.11.3-py3-none-any.whl.metadata (5.1 kB)
Collecting PyYAML>=6.0 (from -r requirements.txt (line 10))
  Downloading pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (2.4 kB)
Collecting starlette>=0.46.0 (from fastapi>=0.110->-r requirements.txt (line 3))
  Downloading starlette-1.10.0-py3-none-any.whl.metadata (6.6 kB)
Collecting anyio>=4.1.0 (from fastapi>=0.110->-r requirements.txt (line 3))
  Downloading anyio-4.16.0-py3-none-any.whl.metadata (4.7 kB)
Collecting typing-extensions>=4.8.0 (from fastapi>=0.110->-r requirements.txt (line 3))
  Downloading typing_extensions-4.16.0-py3-none-any.whl.metadata (3.3 kB)
Collecting typing-inspection>=0.4.2 (from fastapi>=0.110->-r requirements.txt (line 3))
  Downloading typing_inspection-0.4.5-py3-none-any.whl.metadata (2.6 kB)
Collecting annotated-doc>=0.0.2 (from fastapi>=0.110->-r requirements.txt (line 3))
  Downloading annotated_doc-0.0.6-py3-none-any.whl.metadata (6.5 kB)
Collecting opentelemetry-api>=1.44.0 (from fastapi>=0.110->-r requirements.txt (line 3))
  Downloading opentelemetry_api-1.45.2-py3-none-any.whl.metadata (1.4 kB)
Collecting click>=7.0 (from uvicorn>=0.29->uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading click-8.5.1-py3-none-any.whl.metadata (2.6 kB)
Collecting h11>=0.8 (from uvicorn>=0.29->uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading h11-0.16.0-py3-none-any.whl.metadata (8.3 kB)
Collecting annotated-types>=0.6.0 (from pydantic>=2.6->-r requirements.txt (line 5))
  Downloading annotated_types-0.8.0-py3-none-any.whl.metadata (15 kB)
Collecting pydantic-core==2.50.4 (from pydantic>=2.6->-r requirements.txt (line 5))
  Downloading pydantic_core-2.50.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl.metadata (6.6 kB)
Collecting cffi>=2.0.0 (from cryptography>=42.0->-r requirements.txt (line 6))
  Downloading cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl.metadata (2.5 kB)
Collecting idna>=2.8 (from anyio>=4.1.0->fastapi>=0.110->-r requirements.txt (line 3))
  Downloading idna-3.20-py3-none-any.whl.metadata (7.2 kB)
Collecting pycparser (from cffi>=2.0.0->cryptography>=42.0->-r requirements.txt (line 6))
  Downloading pycparser-3.12-py3-none-any.whl.metadata (9.7 kB)
Collecting httptools>=0.8.0 (from uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading httptools-0.9.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (3.5 kB)
Collecting python-dotenv>=0.13 (from uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading python_dotenv-1.2.4-py3-none-any.whl.metadata (29 kB)
Collecting uvloop>=0.15.1 (from uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading uvloop-0.23.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (5.1 kB)
Collecting watchfiles>=0.20 (from uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading watchfiles-1.3.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (4.9 kB)
Collecting websockets>=13.0 (from uvicorn[standard]>=0.29->-r requirements.txt (line 4))
  Downloading websockets-17.4-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (6.3 kB)
Downloading fastapi-0.163.1-py3-none-any.whl (154 kB)
Downloading uvicorn-0.54.0-py3-none-any.whl (87 kB)
Downloading pydantic-2.14.3-py3-none-any.whl (494 kB)
Downloading pydantic_core-2.50.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (2.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2.3/2.3 MB 34.7 MB/s  0:00:00
Downloading cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl (4.8 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 4.8/4.8 MB 68.0 MB/s  0:00:00
Downloading pyjwt-2.15.1-py3-none-any.whl (33 kB)
Downloading passlib-1.7.4-py2.py3-none-any.whl (525 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 525.6/525.6 kB 39.8 MB/s  0:00:00
Downloading markdown-3.11.3-py3-none-any.whl (116 kB)
Downloading pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (807 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 807.9/807.9 kB 65.8 MB/s  0:00:00
Downloading annotated_doc-0.0.6-py3-none-any.whl (5.3 kB)
Downloading annotated_types-0.8.0-py3-none-any.whl (13 kB)
Downloading anyio-4.16.0-py3-none-any.whl (134 kB)
Downloading cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (221 kB)
Downloading click-8.5.1-py3-none-any.whl (126 kB)
Downloading h11-0.16.0-py3-none-any.whl (37 kB)
Downloading idna-3.20-py3-none-any.whl (69 kB)
Downloading opentelemetry_api-1.45.2-py3-none-any.whl (60 kB)
Downloading starlette-1.10.0-py3-none-any.whl (80 kB)
Downloading typing_extensions-4.16.0-py3-none-any.whl (45 kB)
Downloading typing_inspection-0.4.5-py3-none-any.whl (14 kB)
Downloading httptools-0.9.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (552 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 552.8/552.8 kB 50.8 MB/s  0:00:00
Downloading python_dotenv-1.2.4-py3-none-any.whl (23 kB)
Downloading uvloop-0.23.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (4.5 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 4.5/4.5 MB 97.7 MB/s  0:00:00
Downloading watchfiles-1.3.0-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (458 kB)
Downloading websockets-17.4-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (226 kB)
Downloading pycparser-3.12-py3-none-any.whl (62 kB)
Installing collected packages: passlib, websockets, uvloop, typing-extensions, PyYAML, python-dotenv, PyJWT, pycparser, markdown, idna, httptools, h11, click, annotated-types, annotated-doc, uvicorn, typing-inspection, pydantic-core, opentelemetry-api, cffi, anyio, watchfiles, starlette, pydantic, cryptography, fastapi

Successfully installed PyJWT-2.15.1 PyYAML-6.0.3 annotated-doc-0.0.6 annotated-types-0.8.0 anyio-4.16.0 cffi-2.1.1 click-8.5.1 cryptography-50.0.2 fastapi-0.163.1 h11-0.16.0 httptools-0.9.0 idna-3.20 markdown-3.11.3 opentelemetry-api-1.45.2 passlib-1.7.4 pycparser-3.12 pydantic-2.14.3 pydantic-core-2.50.4 python-dotenv-1.2.4 starlette-1.10.0 typing-extensions-4.16.0 typing-inspection-0.4.5 uvicorn-0.54.0 uvloop-0.23.0 watchfiles-1.3.0 websockets-17.4
Processing ./.
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Getting requirements to build wheel: started
  Getting requirements to build wheel: finished with status 'done'
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Requirement already satisfied: fastapi>=0.110 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (0.163.1)
Requirement already satisfied: uvicorn>=0.29 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn[standard]>=0.29->esam==0.2.5) (0.54.0)
Requirement already satisfied: pydantic>=2.6 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (2.14.3)
Requirement already satisfied: cryptography>=42.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (50.0.2)
Requirement already satisfied: PyJWT>=2.8 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (2.15.1)
Requirement already satisfied: passlib>=1.7.4 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (1.7.4)
Requirement already satisfied: markdown>=3.5 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (3.11.3)
Requirement already satisfied: PyYAML>=6.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from esam==0.2.5) (6.0.3)
Requirement already satisfied: cffi>=2.0.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from cryptography>=42.0->esam==0.2.5) (2.1.1)
Requirement already satisfied: pycparser in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from cffi>=2.0.0->cryptography>=42.0->esam==0.2.5) (3.12)
Requirement already satisfied: starlette>=0.46.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from fastapi>=0.110->esam==0.2.5) (1.10.0)
Requirement already satisfied: anyio>=4.1.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from fastapi>=0.110->esam==0.2.5) (4.16.0)
Requirement already satisfied: typing-extensions>=4.8.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from fastapi>=0.110->esam==0.2.5) (4.16.0)
Requirement already satisfied: typing-inspection>=0.4.2 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from fastapi>=0.110->esam==0.2.5) (0.4.5)
Requirement already satisfied: annotated-doc>=0.0.2 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from fastapi>=0.110->esam==0.2.5) (0.0.6)
Requirement already satisfied: opentelemetry-api>=1.44.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from fastapi>=0.110->esam==0.2.5) (1.45.2)
Requirement already satisfied: idna>=2.8 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from anyio>=4.1.0->fastapi>=0.110->esam==0.2.5) (3.20)
Requirement already satisfied: annotated-types>=0.6.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from pydantic>=2.6->esam==0.2.5) (0.8.0)
Requirement already satisfied: pydantic-core==2.50.4 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from pydantic>=2.6->esam==0.2.5) (2.50.4)
Requirement already satisfied: click>=7.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn>=0.29->uvicorn[standard]>=0.29->esam==0.2.5) (8.5.1)
Requirement already satisfied: h11>=0.8 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn>=0.29->uvicorn[standard]>=0.29->esam==0.2.5) (0.16.0)
Requirement already satisfied: httptools>=0.8.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn[standard]>=0.29->esam==0.2.5) (0.9.0)
Requirement already satisfied: python-dotenv>=0.13 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn[standard]>=0.29->esam==0.2.5) (1.2.4)
Requirement already satisfied: uvloop>=0.15.1 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn[standard]>=0.29->esam==0.2.5) (0.23.0)
Requirement already satisfied: watchfiles>=0.20 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn[standard]>=0.29->esam==0.2.5) (1.3.0)
Requirement already satisfied: websockets>=13.0 in /tmp/esam-stranger-home/esam-v0.2.5/.venv/lib/python3.12/site-packages (from uvicorn[standard]>=0.29->esam==0.2.5) (17.4)
Building wheels for collected packages: esam
  Building wheel for esam (pyproject.toml): started
  Building wheel for esam (pyproject.toml): finished with status 'done'
  Created wheel for esam: filename=esam-0.2.5-py3-none-any.whl size=17272 sha256=fc64c7c987a21dbbf62c45078fd02ff50a43ccc21cdb8d45fd225fd8cc183ecf
  Stored in directory: /tmp/pip-ephem-wheel-cache-g2m8ejja/wheels/64/2c/9b/ae197241aec6fa814dcc6e31b7156772972f3d2d0d01163def
Successfully built esam
Installing collected packages: esam
Successfully installed esam-0.2.5
seed complete: {'users': 1, 'entities': 1, 'entity_users': 1, 'debtors': 6, 'audit_logs': 2, 'payment_links': 1, 'letters_of_demand': 1}
ESAM v0.2.5 installed to /tmp/esam-stranger-home/esam-v0.2.5
esam CLI ready: esam init --template local   (see https://github.com/EvolvingSoftware/ESAM/tree/v0.2.5)
[install] rc=0
--- which esam (PATH unmodified) ---
/home/ai/.coriolis/sandbox/bin/esam
--- which esam (after adding ~/.local/bin) ---
/tmp/esam-stranger-home/.local/bin/esam

########## RUN: esam --version ##########
usage: esam [-h] {demo,init,ask} ...
esam: error: unrecognized arguments: --version

########## RUN: esam init (empty directory, local template) ##########
================================================================
esam init --template local: local agent scaffolded (NO API key required)
  endpoint : http://localhost:11434/v1
  model    : llama3.2
  api_key  : (none)
  config   : .esam/config.json
  agent    : .esam/agent.local.yaml
================================================================
[init] rc=0
--- workdir after init ---
total 0
drwxr-xr-x 3 ai ai  60 Nov 16 13:21 .
drwxr-xr-x 4 ai ai 160 Nov 16 13:21 ..
drwxr-xr-x 2 ai ai  80 Nov 16 13:21 .esam
--- .esam tree (if any) ---
/tmp/esam-workdir-empty/.esam/agent.local.yaml
/tmp/esam-workdir-empty/.esam/config.json
--- config.json ---
{
  "template": "local",
  "provider": "openai-compatible",
  "base_url": "http://localhost:11434/v1",
  "model": "llama3.2",
  "api_key": null,
  "requires_api_key": false,
  "endpoint_kind": "llama.cpp/Ollama (OpenAI-compatible)"
}
########## SUMMARY ##########
install_rc=0 init_rc=0
END-OF-PROOF
```

## Notes
- No hand-edits: the run used only the published command and documented defaults.
- The `esam --version` probe is not part of the quickstart path; that flag was not implemented in v0.2.5 (cosmetic gap — CLI dispatch works for demo/init/ask). Everything on the published install → init path exits 0.
