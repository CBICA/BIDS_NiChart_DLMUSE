#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# Copyright 2023 The NiPreps Developers <nipreps@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# We support and encourage derived works from this project, please read
# about our expectations at
#
#     https://www.nipreps.org/community/licensing/
#
"""The main command-line interface for ncdlmuse."""

import gc
import logging
import os
import sys
import warnings
from multiprocessing import Manager, Process

# Filter warnings that are visible datetime import during process execution
# See https://github.com/nipreps/fmriprep/issues/2871
warnings.filterwarnings("ignore", message=".*already loaded.*packaging.*")
warnings.filterwarnings("ignore", message=".*is non-raw schema type.*")


def main():
    """Entry point for ncdlmuse BIDS App.

    This function serves as the main entry point for the ncdlmuse command-line
    interface. It parses arguments, sets up the environment, builds the
    workflow, executes it, and generates reports.

    Returns
    -------
    int
        Exit code (0 for success, >0 for errors)

    """
    import re
    import subprocess
    from pathlib import Path

    from nipype import config as ncfg, logging as nlogging
    from .. import config
    from .parser import parse_args
    from .workflow import build_workflow
    # Import the group aggregation function
    from ..workflows.group import aggregate_volumes
    # Import needed for BIDSLayout initialization
    from bids.layout import BIDSLayout, BIDSLayoutIndexer
    import json # For writing dataset_description.json

    # 1. Parse arguments and config file, setup logging
    parse_args()

    # Set up conditional logging based on config
    log_file = config.execution.log_dir / f"ncdlmuse_{config.execution.run_uuid}.log"
    # Set up logging
    ncfg.update_config({
        "logging": {
            "log_directory": str(config.execution.log_dir),
            "log_to_file": False,
            "log_format": '%(asctime)s %(name)s %(levelname)s:\n\t %(message)s',
            "datefmt": '%%y%%m%%d-%%H:%%M:%%S'
        }
    })
    nlogging.update_logging(ncfg)
    # Retrieve logging level
    log_level = config.execution.log_level
    config.loggers.cli.setLevel(log_level)
    config.loggers.interface.setLevel(log_level)
    config.loggers.utils.setLevel(log_level)
    config.loggers.workflow.setLevel(log_level)

    # Called with reports only
    if config.execution.reports_only:
        from ..reports.individual import generate_reports
        # Import needed for dataset_description.json
        from ..utils.bids import write_derivative_description

        # Initialize the layout from the config file
        try:
            # --- Initialize BIDS Layout --- #
            # Copied/adapted from parser.py
            ignore_patterns = (
                'code', 'stimuli', 'sourcedata', 'models',
                'derivatives', re.compile(r'^\\.') # Hidden files
            )
            bids_indexer = BIDSLayoutIndexer(
                validate=not config.execution.skip_bids_validation,
                ignore=ignore_patterns,
            )
            # Define reportlets path for BIDSLayout
            reportlets_path_for_layout = Path(config.execution.work_dir) / 'reportlets'
            # Ensure reportlets directory exists
            reportlets_path_for_layout.mkdir(parents=True, exist_ok=True)
            # Ensure dataset_description.json exists in reportlets directory
            desc_file = reportlets_path_for_layout / "dataset_description.json"
            if not desc_file.exists():
                desc_content = {
                    "Name": "NCDLMUSE Reportlets",
                    "BIDSVersion": "1.0.2", # Or a version appropriate for nireports
                    "GeneratedBy": [{"Name": "ncdlmuse"}]
                }
                with open(desc_file, 'w') as f:
                    json.dump(desc_content, f, indent=2)

            layout = BIDSLayout(
                root=str(config.execution.bids_dir),
                database_path=None, # Use in-memory DB for reports-only
                indexer=bids_indexer,
                reset_database=True,
                derivatives=str(reportlets_path_for_layout) # Index reportlets dir
            )
            config.execution.layout = layout # Store layout in config
            # --- DEBUG: Check layout object before generating reports --- #
            if isinstance(layout, BIDSLayout):
                config.loggers.cli.info(f"Layout object created successfully. Root: {layout.root}")
            else:
                config.loggers.cli.error(f"Layout object is invalid or None: {layout}")
                return 1 # Exit if layout is bad
            # end debug
        except Exception as e:
            config.loggers.cli.critical(f"Could not initialize BIDSLayout: {e}")
            return 1

        config.loggers.cli.info("Running solely the reporting module")
        exit_code = generate_reports(
            subject_list=config.execution.participant_label,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            work_dir=config.execution.work_dir,
            layout=config.execution.layout,
        )
        # Write dataset_description.json if it doesn't exist (basic version)
        if not (config.execution.ncdlmuse_dir / "dataset_description.json").exists():
            write_derivative_description(config.execution.bids_dir, config.execution.ncdlmuse_dir)

        return exit_code

    # Build-only run (e.g., generating boilerplate)
    if config.execution.boilerplate_only:
        from ..reports.individual import generate_reports
        from ..utils.bids import write_derivative_description
        # Imports needed for BIDSLayout initialization
        import re
        from bids.layout import BIDSLayout, BIDSLayoutIndexer
        import json # For writing dataset_description.json

        # Initialize the layout from the config file
        try:
            # --- Initialize BIDS Layout --- #
            # Copied/adapted from parser.py
            ignore_patterns = (
                'code', 'stimuli', 'sourcedata', 'models',
                'derivatives', re.compile(r'^\\.') # Hidden files
            )
            bids_indexer = BIDSLayoutIndexer(
                validate=not config.execution.skip_bids_validation,
                ignore=ignore_patterns,
            )
            # Define reportlets path for BIDSLayout
            reportlets_path_for_layout = Path(config.execution.work_dir) / 'reportlets'
            # Ensure reportlets directory exists
            reportlets_path_for_layout.mkdir(parents=True, exist_ok=True)
            # Ensure dataset_description.json exists in reportlets directory
            desc_file = reportlets_path_for_layout / "dataset_description.json"
            if not desc_file.exists():
                desc_content = {
                    "Name": "NCDLMUSE Reportlets",
                    "BIDSVersion": "1.0.2", # Or a version appropriate for nireports
                    "GeneratedBy": [{"Name": "ncdlmuse"}]
                }
                with open(desc_file, 'w') as f:
                    json.dump(desc_content, f, indent=2)

            layout = BIDSLayout(
                root=str(config.execution.bids_dir),
                database_path=None, # Use in-memory DB for reports-only
                indexer=bids_indexer,
                reset_database=True,
                derivatives=str(reportlets_path_for_layout) # Index reportlets dir
            )
            config.execution.layout = layout # Store layout in config
        except Exception as e:
            config.loggers.cli.critical(f"Could not initialize BIDSLayout: {e}")
            return 1

        config.loggers.cli.info("Generating boilerplate text only. Workflow will not be executed.")
        # Generate boilerplate
        exit_code = generate_reports(
            subject_list=config.execution.participant_label,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            work_dir=config.execution.work_dir,
            boilerplate_only=True,
            layout=config.execution.layout,
        )
        # Write dataset_description.json if it doesn't exist (basic version)
        if not (config.execution.ncdlmuse_dir / "dataset_description.json").exists():
            write_derivative_description(config.execution.bids_dir, config.execution.ncdlmuse_dir)

        return exit_code

    # 2. Setup environment
    # Set up maximum number of cores available to nipype
    n_procs = config.nipype.n_procs

    # Set OMP_NUM_THREADS
    omp_nthreads = config.nipype.omp_nthreads
    if omp_nthreads is None or omp_nthreads < 1:
        omp_nthreads = os.cpu_count()
        config.nipype.omp_nthreads = omp_nthreads
    os.environ["OMP_NUM_THREADS"] = str(config.nipype.omp_nthreads)

    # Set memory limits
    mem_gb = config.nipype.mem_gb
    if mem_gb:
        from niworkflows.utils.misc import setup_mcr
        try:
            setup_mcr(mem_gb)
        except RuntimeError as e:
            config.loggers.cli.critical(f"Error setting memory limits: {e}")
            return 1

    # 3. Check dependencies
    # Check NiChart_DLMUSE availability
    try:
        retcode = subprocess.check_call(["NiChart_DLMUSE", "--version"])
        if retcode != 0:
            raise RuntimeError
        config.loggers.cli.info("Found NiChart_DLMUSE executable.")
    except (FileNotFoundError, RuntimeError):
        config.loggers.cli.critical(
            "NiChart_DLMUSE command not found. Please ensure it is installed and in your PATH."
        )
        return 1

    # Check other dependencies potentially added later
    # ...

    # 4. Build workflow in an isolated process
    config.loggers.cli.info(
        f"Building ncdlmuse workflow (analysis level: {config.execution.analysis_level})."
    )
    config_file = config.execution.log_dir / "ncdlmuse.toml"
    # If running group level, build_workflow might return early or None
    if config.execution.analysis_level == "group":
        config.to_filename(config_file)
        config.loggers.cli.info(
            "Group-level analysis selected. Skipping Nipype workflow execution."
            )
        # return 0 # Or call group-level report function
        workflow = None
        retcode = 0 # Assume success for now, as no workflow runs

    else: # Participant level
        # Set up a dictionary for retrieving workflow results
        with Manager() as mgr:
            retval = mgr.dict()
            p = Process(target=build_workflow, args=(str(config_file), retval))
            p.start()
            p.join()
            retcode = p.exitcode or 0
            workflow = retval.get("workflow", None)

        # Check exit code from build process
        if retcode != 0:
            config.loggers.cli.critical("Workflow building failed. See logs for details.")
            return retcode

        if workflow is None:
            config.loggers.cli.critical("Workflow building did not return a workflow object.")
            return 1

        # Save workflow graph if requested
        if config.execution.write_graph:
            try:
                workflow.write_graph(graph2use="colored", format="svg", simple_form=True)
                config.loggers.cli.info("Workflow graph saved to work directory.")
            except Exception as e:
                config.loggers.cli.warning(f"Could not save workflow graph: {e}")

        # Check workflow for errors before running
        workflow.config['execution']['crashdump_dir'] = str(config.execution.log_dir)
        for node in workflow.list_node_names():
            node_config = workflow.get_node(node).config or {}  # Handle None case
            if any(req in node_config for req in (
                "memory_gb", "memory_mb", "num_threads", "num_cpus"
                )):
                workflow.get_node(node).config = node_config  # Ensure config exists
                workflow.get_node(node).config["rules"] = False

    # 5. Execute workflow (participant level only for now)
    retcode = 0
    if config.execution.analysis_level == "participant" and workflow:
        gc.collect() # Clean up memory before running
        config.loggers.cli.info("Starting participant-level workflow execution.")
        try:
            workflow.run(**config.nipype.get_plugin())
        except Exception as e:
            config.loggers.cli.critical(f"Workflow execution failed: {e}")
            retcode = 1
        else:
            config.loggers.cli.info("Workflow finished successfully.")
            # Check for final output existence?
    elif config.execution.analysis_level == "group":
        config.loggers.cli.info("Group-level analysis: Aggregating participant results.")
        # Define the default output file path
        group_output_file = config.execution.ncdlmuse_dir / 'group_ncdlmuse.tsv'
        try:
            config.loggers.cli.info(
                f"Aggregating results to: {group_output_file}"
            )
            aggregate_volumes(
                derivatives_dir=config.execution.ncdlmuse_dir,
                output_file=group_output_file, # Use the default path
            )
            config.loggers.cli.info("Aggregation finished successfully.")
        except FileNotFoundError as e:
            config.loggers.cli.error(f"Aggregation failed: Could not find input files. {e}")
            retcode = 1 # Mark run as failed if aggregation fails
        except Exception as e:
            config.loggers.cli.critical(f"Group aggregation failed: {e}", exc_info=True)
            retcode = 1 # Mark run as failed


    # 6. Generate reports (unless build failed)
    if retcode == 0:
        from ..reports.individual import generate_reports

        config.loggers.cli.info("Generating final reports.")
        exit_code = generate_reports(
            subject_list=config.execution.participant_label,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            work_dir=config.execution.work_dir,
            layout=config.execution.layout,
        )
        # Update overall exit code if report generation failed
        if exit_code != 0:
            retcode = exit_code
        # --- Clean up Nipype logs generated by reporting --- #
        else:
            try:
                log_file = Path(config.execution.ncdlmuse_dir) / "pipeline.log"
                log_file.unlink(missing_ok=True)
                log_file = Path(config.execution.ncdlmuse_dir) / "pypeline.log"
                log_file.unlink(missing_ok=True)
            except OSError:
                pass # Ignore errors if file couldn't be deleted
    else:
        config.loggers.cli.warning("Skipping report generation due to workflow execution failure.")

    config.loggers.cli.info(
        f"Execution finished. Exit code: {retcode}"
        f" ({config.execution.participant_label or 'group'})"
    )
    return retcode


if __name__ == "__main__":
    # This is only run when script is executed directly,
    # e.g., python ncdlmuse/cli/run.py
    # The primary entry point is via `ncdlmuse` command (setup.py) or `python -m ncdlmuse.cli`
    sys.exit(main())
