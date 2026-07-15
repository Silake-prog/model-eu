Installation
============

DemandForge is a Python project managed with pyproject.toml. You can install it in a fresh environment with your preferred tool.

Prerequisites
-------------
- Python 3.10+
- Recommended: a virtual environment (venv, conda, micromamba, etc.)

Steps
-----

1. Create and activate a virtual environment (example with venv)::

    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate

2. Install the package in editable mode (so code changes are reflected):

    pip install -e .

3. Optional: install Sphinx to build the documentation locally:

    pip install sphinx sphinx-rtd-theme

Building the docs locally
-------------------------
From the project root:

1. Navigate to the docs folder::

    cd docs

2. Build HTML docs::

    sphinx-build -b html . _build/html

Open _build/html/index.html in your browser.
