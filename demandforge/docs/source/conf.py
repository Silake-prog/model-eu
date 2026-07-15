# Configuration file for the Sphinx documentation builder.
import os
import sys
from datetime import datetime

# Add project root to sys.path so Sphinx can import demandforge
THIS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

project = 'DemandForge'
copyright = f"{datetime.now().year}, DemandForge"
author = 'DemandForge'

# The full version, including alpha/beta/rc tags
release = '0.2.3'

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.napoleon',  # Google/NumPy style docstrings
    'sphinx.ext.viewcode',
]

autosummary_generate = True
napoleon_google_docstring = True
napoleon_numpy_docstring = True

# Templates and source
templates_path = ['_templates']
exclude_patterns = ['_build']

# HTML theme
html_theme = 'pydata_sphinx_theme'

html_static_path = ['_static']

# Master document
master_doc = 'index'
