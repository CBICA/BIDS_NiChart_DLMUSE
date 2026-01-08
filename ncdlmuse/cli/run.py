#!/usr/bin/env python
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
import os
import sys
import warnings
from multiprocessing import Manager, Process

from bids.layout import BIDSLayout, BIDSLayoutIndexer

from .. import data

# Filter warnings that are visible datetime import during process execution
# See https://github.com/nipreps/fmriprep/issues/2871
# Existing filters
warnings.filterwarnings('ignore', message='.*already loaded.*packaging.*')
warnings.filterwarnings('ignore', message='.*is non-raw schema type.*')


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

    from .. import config
    from ..utils.bids import write_bidsignore, write_derivative_description
    from ..workflows.group import aggregate_volumes
    from .parser import parse_args

    # Helper function to ensure dataset_description.json exists
    def ensure_dataset_description():
        """Create dataset_description.json and .bidsignore if they don't exist."""
        try:
            # Check if file already exists to avoid unnecessary recreation
            desc_path = Path(config.execution.ncdlmuse_dir) / 'dataset_description.json'
            if not desc_path.exists():
                write_derivative_description(
                    config.execution.bids_dir, config.execution.ncdlmuse_dir
                )
            # Ensure .bidsignore file exists
            write_bidsignore(config.execution.ncdlmuse_dir)
            return True
        except (OSError, PermissionError) as e:
            config.loggers.cli.warning(
                f'Error creating dataset_description.json or .bidsignore: {e}'
            )
            return False

    # 1. Parse arguments and config file, setup logging
    parse_args()

    # --- Handle group-level analysis separately and exit ---
    if config.execution.analysis_level == 'group':
        config.loggers.cli.info('Performing group-level aggregation.')
        output_dir = Path(config.execution.ncdlmuse_dir)
        group_output_file = output_dir / 'group_ncdlmuse.tsv'
        retcode = 0

        # Ensure dataset_description.json for the output directory
        if not ensure_dataset_description():
            # Log warning but proceed with aggregation attempt
            config.loggers.cli.warning(
                'Failed to create dataset_description.json for the group output directory. '
                'Attempting aggregation regardless.'
            )

        try:
            aggregate_volumes(
                derivatives_dir=output_dir,
                output_file=group_output_file,
                add_provenance=config.execution.add_provenance,
            )
            config.loggers.cli.info(
                f'Finished aggregating ROI volumes. Results are in {group_output_file}.'
            )
        except FileNotFoundError:
            config.loggers.cli.warning(
                f'Not found any JSON files with ROI volumes for aggregation in {output_dir}.'
            )
            # This is a warning, not necessarily a failure of the command itself.
            # If this should be an error, set retcode = 1
        except (ValueError, OSError) as e:
            config.loggers.cli.critical(f'Group aggregation failed: {e}', exc_info=True)
            retcode = 1
        return retcode  # Exit after group processing

    # --- Participant-level analysis and other modes (reports-only, boilerplate) ---

    # Called with reports only
    if config.execution.reports_only:
        from ..reports.individual import generate_reports

        # Initialize the layout from the config file
        try:
            # --- Initialize BIDS Layout --- #
            # Copied/adapted from parser.py
            ignore_patterns = (
                'code',
                'stimuli',
                'sourcedata',
                'models',
                'derivatives',
                re.compile(r'^\\.'),  # Hidden files
            )
            bids_indexer = BIDSLayoutIndexer(
                validate=not config.execution.skip_bids_validation,
                ignore=ignore_patterns,
            )

            # Initialize layout with both BIDS and derivatives directories
            layout = BIDSLayout(
                root=str(config.execution.bids_dir),
                database_path=None,  # Use in-memory DB for reports-only
                indexer=bids_indexer,
                reset_database=True,
                derivatives=str(config.execution.ncdlmuse_dir),  # Include derivatives directory
            )
            config.execution.layout = layout

            # --- DEBUG: Check layout object before generating reports --- #
            if isinstance(layout, BIDSLayout):
                config.loggers.cli.info(f'Layout object created successfully. Root: {layout.root}')
                config.loggers.cli.info(f'Derivatives directory: {config.execution.ncdlmuse_dir}')
            else:
                config.loggers.cli.error(f'Layout object is invalid or None: {layout}')
                return 1  # Exit if layout is bad
            # end debug
        except (OSError, ValueError, RuntimeError) as e:
            config.loggers.cli.critical(f'Could not initialize BIDSLayout: {e}')
            return 1

        config.loggers.cli.info('Running solely the reporting module')

        # Ensure dataset_description.json exists
        ensure_dataset_description()

        exit_code = generate_reports(
            subject_list=config.execution.participant_label,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            work_dir=config.execution.work_dir,
            layout=config.execution.layout,
        )

        return exit_code

    # Build-only run (e.g., generating boilerplate)
    if config.execution.boilerplate_only:
        import json  # For writing dataset_description.json

        # Imports needed for BIDSLayout initialization
        import re

        from ..reports.individual import generate_reports

        # Initialize the layout from the config file
        try:
            # --- Initialize BIDS Layout --- #
            # Copied/adapted from parser.py
            ignore_patterns = (
                'code',
                'stimuli',
                'sourcedata',
                'models',
                'derivatives',
                re.compile(r'^\\.'),  # Hidden files
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
            desc_file = reportlets_path_for_layout / 'dataset_description.json'
            if not desc_file.exists():
                desc_content = {
                    'Name': 'NCDLMUSE Reportlets',
                    'BIDSVersion': '1.10.0',
                    'GeneratedBy': [{'Name': 'ncdlmuse'}],
                }
                with open(desc_file, 'w') as f:
                    json.dump(desc_content, f, indent=2)

            layout = BIDSLayout(
                root=str(config.execution.bids_dir),
                database_path=None,  # Use in-memory DB for reports-only
                indexer=bids_indexer,
                reset_database=True,
                derivatives=str(reportlets_path_for_layout),  # Index reportlets dir
            )
            config.execution.layout = layout  # Store layout in config
        except (OSError, ValueError, RuntimeError) as e:
            config.loggers.cli.critical(f'Could not initialize BIDSLayout: {e}')
            return 1

        config.loggers.cli.info('Generating boilerplate text only. Workflow will not be executed.')

        # Ensure dataset_description.json exists
        ensure_dataset_description()

        # Generate boilerplate
        exit_code = generate_reports(
            subject_list=config.execution.participant_label,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            work_dir=config.execution.work_dir,
            boilerplate_only=True,
            layout=config.execution.layout,
        )

        return exit_code

    # 2. Setup environment
    # Set up maximum number of cores available to nipype
    # n_procs is used by nipype's plugin system when needed

    # Set OMP_NUM_THREADS
    omp_nthreads = config.nipype.omp_nthreads
    if omp_nthreads is None or omp_nthreads < 1:
        omp_nthreads = os.cpu_count()
        config.nipype.omp_nthreads = omp_nthreads
    os.environ['OMP_NUM_THREADS'] = str(config.nipype.omp_nthreads)

    # Set memory limits
    mem_gb = config.nipype.mem_gb
    if mem_gb:
        from niworkflows.utils.misc import setup_mcr

        try:
            setup_mcr(mem_gb)
        except RuntimeError as e:
            config.loggers.cli.critical(f'Error setting memory limits: {e}')
            return 1

    # 3. Check dependencies
    # Check NiChart_DLMUSE availability
    try:
        # Use shutil.which to find the full path to the executable
        from shutil import which

        dlmuse_path = which('NiChart_DLMUSE')
        if not dlmuse_path:
            raise FileNotFoundError('NiChart_DLMUSE executable not found in PATH')
        retcode = subprocess.check_call([dlmuse_path, '--version'])
        if retcode != 0:
            raise RuntimeError
        config.loggers.cli.info('Found NiChart_DLMUSE executable.')
    except (FileNotFoundError, RuntimeError):
        config.loggers.cli.critical(
            'NiChart_DLMUSE command not found. Please ensure it is installed and in your PATH.'
        )
        return 1

    # 4. Build workflow in an isolated process
    config.loggers.cli.info(
        f'Building ncdlmuse workflow (analysis level: {config.execution.analysis_level}).'
    )
    config_file = config.execution.log_dir / 'ncdlmuse.toml'

    # This section is now only for participant level, as group level exits early.
    # Import build_workflow here as it's participant-specific.
    # Needed for participant workflow

    from .workflow import build_boilerplate, build_workflow

    # Set up a dictionary for retrieving workflow results
    with Manager() as mgr:
        retval = mgr.dict()
        p = Process(target=build_workflow, args=(str(config_file), retval))
        p.start()
        p.join()
        retcode = p.exitcode or 0
        workflow = retval.get('workflow', None)

    # Check exit code from build process
    if retcode != 0:
        config.loggers.cli.critical('Workflow building failed. See logs for details.')
        return retcode

    if workflow is None:
        config.loggers.cli.critical('Workflow building did not return a workflow object.')
        return 1

    # Generate citation boilerplate after successful workflow build
    config.loggers.cli.info('Generating citation boilerplate.')
    try:
        with Manager() as mgr:
            p = Process(target=build_boilerplate, args=(str(config_file), workflow))
            p.start()
            p.join()

            if p.exitcode != 0:
                config.loggers.cli.warning(
                    f'Citation boilerplate generation failed with exit code: {p.exitcode}'
                )
            else:
                config.loggers.cli.info('Citation boilerplate generation completed successfully.')

            # Check for debug log file to capture subprocess details
            debug_log_path = config.execution.ncdlmuse_dir / 'logs' / 'boilerplate_debug.log'
            if debug_log_path.exists():
                try:
                    debug_content = debug_log_path.read_text()
                    config.loggers.cli.info(f'Boilerplate debug log contents:\n{debug_content}')
                    # Clean up debug file
                    debug_log_path.unlink()
                except (OSError, PermissionError, UnicodeDecodeError) as e:
                    config.loggers.cli.info(f'Could not read debug log: {e}')

    except (OSError, PermissionError, RuntimeError) as e:
        config.loggers.cli.warning(f'Citation boilerplate generation failed: {e}')

    # Save workflow graph if requested
    if config.execution.write_graph:
        try:
            workflow.write_graph(graph2use='colored', format='svg', simple_form=True)
            config.loggers.cli.info('Workflow graph saved to work directory.')
        except (OSError, RuntimeError) as e:
            config.loggers.cli.warning(f'Could not save workflow graph: {e}')

    # Check workflow for errors before running
    workflow.config['execution']['crashdump_dir'] = str(config.execution.log_dir)
    for node in workflow.list_node_names():
        node_config = workflow.get_node(node).config or {}  # Handle None case
        memory_or_cpu_reqs = ('memory_gb', 'memory_mb', 'num_threads', 'num_cpus')
        if any(req in node_config for req in memory_or_cpu_reqs):
            workflow.get_node(node).config = node_config  # Ensure config exists
            workflow.get_node(node).config['rules'] = False

    # 5. Execute workflow (participant level only for now)
    retcode = 0
    if config.execution.analysis_level == 'participant' and workflow:
        gc.collect()  # Clean up memory before running
        config.loggers.cli.info('Starting participant-level workflow execution.')
        try:
            workflow.run(**config.nipype.get_plugin())
        except (RuntimeError, OSError, ValueError) as e:
            config.loggers.cli.critical(f'Workflow execution failed: {e}')
            retcode = 1
        else:
            config.loggers.cli.info('Workflow finished successfully.')
            # Check for final output existence?

    # 6. Generate reports (unless build failed)
    if retcode == 0:
        from ..reports.individual import generate_reports

        config.loggers.cli.info('Generating final reports.')

        # Ensure dataset_description.json exists for participant output
        ensure_dataset_description()

        exit_code = generate_reports(
            subject_list=config.execution.participant_label,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            work_dir=config.execution.work_dir,
            layout=config.execution.layout,
            bootstrap_file=data.load('reports-spec.yml'),  # Explicitly provide bootstrap file
        )
        # Update overall exit code if report generation failed
        if exit_code != 0:
            retcode = exit_code
        # --- Clean up Nipype logs generated by reporting --- #
        else:
            try:
                log_file = Path(config.execution.ncdlmuse_dir) / 'pipeline.log'
                log_file.unlink(missing_ok=True)
                log_file = Path(config.execution.ncdlmuse_dir) / 'pypeline.log'
                log_file.unlink(missing_ok=True)
            except OSError:
                pass  # Ignore errors if file couldn't be deleted
    else:
        config.loggers.cli.warning('Skipping report generation due to workflow execution failure.')

    config.loggers.cli.info(
        f'Execution finished. Exit code: {retcode}'
        f' ({config.execution.participant_label or "group"})'
    )
    return retcode


if __name__ == '__main__':
    # This is only run when script is executed directly,
    # e.g., python ncdlmuse/cli/run.py
    # The primary entry point is via `ncdlmuse` command (setup.py) or `python -m ncdlmuse.cli`
    sys.exit(main())
