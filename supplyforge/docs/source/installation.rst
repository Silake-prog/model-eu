Installation
============

There are several ways to install `supplyforge` and its dependencies.

Using Conda (Recommended)
-------------------------

You can create a dedicated conda environment with all the necessary dependencies using the provided environment file. This is the recommended method as it ensures that all dependencies are at the correct versions.

.. code-block:: bash

    conda env create -f ci/envs/environment-all.yaml
    conda activate supplyforge-env

Using Pip
---------

You can install the package directly using pip:

.. code-block:: bash

    pip install supplyforge

Or from source:

.. code-block:: bash

    git clone https://git.persee.minesparis.psl.eu/energy-alternatives/supplyforge.git
    cd supplyforge
    pip install -e .
