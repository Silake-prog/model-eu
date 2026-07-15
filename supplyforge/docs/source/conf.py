# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'supplyforge'
copyright = '2025, Yassine Abdelouadoud'
author = 'Yassine Abdelouadoud'
release = '0.1.5'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax"
]
mathjax3_config = {
    "tex": {"packages": {"[+]": ["amsmath", "amssymb"]}}
}
import os
import sys
import json
import subprocess
from pathlib import Path
sys.path.insert(0, os.path.abspath(".."))

napoleon_google_docstring = True
napoleon_numpy_docstring = True

templates_path = ['_templates']
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'pydata_sphinx_theme'
html_static_path = ['_static']

def format_list(items):
    if not items:
        return "    None"
    return "\n".join([f"    - ``{x}``" for x in items])

def run_apidoc(app):
    import subprocess
    subprocess.call([
        "sphinx-apidoc",
        "-o", "source",
        "../supplyforge"
    ])

# -----------------------------------------------------------------------------
# 2. Utility: convert script path → Python module
# -----------------------------------------------------------------------------

def script_to_module(script_path, base_dir):
    if not script_path:
        return None

    full_path = (base_dir / script_path).resolve()
    parts = full_path.with_suffix("").parts
    if "supplyforge" in parts:
        idx = parts.index("supplyforge")
        return ".".join(parts[(idx + 1):])

    return None

# -----------------------------------------------------------------------------
# 3. Format Snakemake rule fields
# -----------------------------------------------------------------------------

def format_field(field):
    if not field:
        return "None"

    lines = []
    for item in field:
        lines.append(f"- ``{item}``")
    return "\n".join(lines)

# -----------------------------------------------------------------------------
# 4. Generate RST for each rule
# -----------------------------------------------------------------------------

def generate_rule_rst(rule):
    module = script_to_module(rule.script, Path(rule.get("basedir")))

    title = rule.name
    underline = "=" * len(title)

    doc = rule.docstring or "No description."

    inputs = format_field(rule.input)
    outputs = format_field(rule.output)
    params = format_field(rule.params)

    module_link = f":mod:`{module}`" if module else "None"

    return f"""
{title}
{underline}

{doc}

**Implementation**
    {module_link}

**Inputs**
{inputs}

**Outputs**
{outputs}

**Params**
{params}
"""

# -----------------------------------------------------------------------------
# 5. Generate all Snakemake rule docs
# -----------------------------------------------------------------------------

def generate_snakemake_docs(app):
    docs_dir = Path(__file__).parent
    rules_dir = docs_dir / "rules"
    rules_dir.mkdir(exist_ok=True)

    script_path = docs_dir / "scripts" / "extract_snakemake_rules.py"

    result = subprocess.run(
        ["python", str(script_path)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("Snakemake doc generation failed:")
        print(result.stderr)
        return

    rules = json.loads(result.stdout)

    index_lines = [
        "Snakemake Rules",
        "================",
        "",
        ".. toctree::",
        "   :maxdepth: 1",
        "",
    ]

    for rule in rules:
        module = script_to_module(rule.get("script"), Path(rule.get("basedir")))

        content = f"""
{rule['name']}
{'=' * len(rule['name'])}

{rule.get('doc') or 'No description.'}

**Implementation**
    {f":mod:`{module}`" if module else "None"}

**Inputs**
{format_list(rule.get("input"))}

**Outputs**
{format_list(rule.get("output"))}

**Params**
{format_list(rule.get("params"))}
"""

        rst_path = rules_dir / f"{rule['name']}.rst"
        with open(rst_path, "w") as f:
            f.write(content)

        index_lines.append(f"   {rule['name']}")

    with open(rules_dir / "index.rst", "w") as f:
        f.write("\n".join(index_lines))

# -----------------------------------------------------------------------------
# 6. Generate DAG and rulegraph images
# -----------------------------------------------------------------------------

def generate_interactive_rulegraph(app):
    import subprocess
    import re
    from pathlib import Path

    docs_dir = Path(__file__).parent
    img_dir = docs_dir / "_static"
    img_dir.mkdir(exist_ok=True)

    snakefile = Path(docs_dir).parents[1] / "Snakefile"
    configfile = Path(docs_dir).parents[1] / "config" / "config.yaml"


    # try:
    # 1. Get DOT graph

    result = subprocess.run(
        f"snakemake --snakefile {snakefile} --configfile {configfile} --rulegraph",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    )

    result.check_returncode()

    dot = result.stdout

    import re

    def add_links(match):
        node_id = match.group(1)
        attrs = match.group(2)

        # Extract rule name from label
        label_match = re.search(r'label\s*=\s*"([^"]+)"', attrs)
        if not label_match:
            return match.group(0)

        rule = label_match.group(1)

        # Skip special nodes if needed
        if rule == "all":
            return match.group(0)

        if "URL=" in attrs:
            return match.group(0)

        new_attrs = attrs.rstrip("]") + f', URL="../rules/{rule}.html", target="_top"]'
        return f'{node_id}[{new_attrs}'


    dot = re.sub(r'(\d+)\s*\[(.*?)\]', add_links, dot)
    dot = dot.replace(
        "digraph snakemake_dag {",
        """digraph snakemake_dag {
        rankdir=LR;
        nodesep=0.3;
        ranksep=0.2;
        """
    )

    dot = dot.replace(
        "graph[bgcolor=white, margin=0];",
        'graph[bgcolor=white, margin=0, size="8,10!"];'
    )
    # 3. Write temp DOT file
    dot_path = img_dir / "rulegraph.dot"
    dot_path.write_text(dot)

    # 4. Generate SVG
    subprocess.run(
        f"dot -Tsvg {dot_path} > {img_dir}/rulegraph.svg",
        shell=True,
        check=True
    )

    # except subprocess.CalledProcessError:
    #     print("Warning: could not generate interactive rulegraph")
# -----------------------------------------------------------------------------
# 7. Register everything
# -----------------------------------------------------------------------------

def setup(app):
    app.connect("builder-inited", run_apidoc)
    app.connect("builder-inited", generate_snakemake_docs)
    app.connect("builder-inited", generate_interactive_rulegraph)