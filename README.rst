############################################################
*BIDS_NiChart_DLMUSE*: A BIDS-App wrapper for NiChart DLMUSE
############################################################

*****
About
*****

This is a BIDS-App wrapper for `NiChart DLMUSE <https://github.com/CBICA/NiChart_DLMUSE/tree/main>`_, a tool for brain mask extraction, brain segmentation and getting ROI volumes.

Installation
------------

BIDS_NiChart_DLMUSE can be installed using pip:

.. code-block:: bash

    pip install ncdlmuse==<version>

Or using Docker:

.. code-block:: bash

    docker pull cbica/ncdlmuse:<version>

Or using Singularity:

.. code-block:: bash

    singularity build ncdlmuse.sif docker://cbica/ncdlmuse:<version>

Usage
-----

Basic usage:

.. code-block:: bash

    # participant-level analysis
    # if no `--participant-label` is provided, run all
    ncdlmuse bids_dir output_dir participant --device cuda

    # group-level analysis
    ncdlmuse bids_dir output_dir group

For more options:

.. code-block:: bash

    ncdlmuse --help

More information and documentation can be found at https://cbica.github.io/NiChart_DLMUSE.

Using Singularity (Recommended):
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    # participant-level analysis
    singularity run --nv --cleanenv \
           -B /path/to/bids/dir:/data:ro \
           -B /path/to/output/dir:/out \
           -B /path/to/work/dir:/work \
           ncdlmuse.sif \
                /data \
                /out \
                participant \
                --participant-label 01 \
                --session-id 1 \
                --device cuda \
                --work-dir /work \
                --stop-on-first-crash \
                --skip_bids_validation

    # group-level analysis
    singularity run --nv --cleanenv \
           -B /path/to/bids/dir:/data:ro \
           -B /path/to/output/dir:/out \
           -B /path/to/work/dir:/work \
           ncdlmuse.sif \
                /data \
                /out \
                group
