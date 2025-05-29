"""
The workflow builder factory method.

All the checks and the construction of the workflow are done
inside this function that has pickleable inputs and output
dictionary (``retval``) to allow isolation using a
``multiprocessing.Process`` that allows ncdlmuse to enforce
a hard-limited memory-scope.

"""


def build_workflow(config_file, retval):
    """Create the Nipype Workflow that supports the whole execution graph."""
    import re

    from bids.layout import BIDSLayout, BIDSLayoutIndexer

    from ncdlmuse import config, data
    from ncdlmuse.reports.individual import generate_reports
    from ncdlmuse.utils.misc import check_deps
    from ncdlmuse.workflows.base import init_ncdlmuse_wf

    config.load(config_file)

    # --- Re-initialize BIDS Layout in this process --- #
    # The layout object doesn't serialize/deserialize properly across processes
    # via the config file. Recreate it here using loaded config paths.
    build_log = config.loggers.workflow  # Get logger after config load
    try:
        indexer = BIDSLayoutIndexer(
            validate=not config.execution.skip_bids_validation,
            ignore=(
                'code',
                'stimuli',
                'sourcedata',
                'models',
                'derivatives',
                re.compile(r'^\.'),  # Ignore hidden files/dirs
            ),
        )
        # Use database path from config if available, otherwise let BIDSLayout manage it
        db_path = config.execution.bids_database_dir
        reset_db = bool(db_path is None)  # Reset if no specific path is given
        layout = BIDSLayout(
            str(config.execution.bids_dir),
            database_path=db_path,
            reset_database=reset_db,  # Force fresh index if db_path is None or reuse if specified
            indexer=indexer,
        )
        config.execution.layout = layout  # Store the layout object back into config
        build_log.info('Successfully re-initialized BIDS Layout for workflow building process.')
    except (OSError, ValueError, RuntimeError) as e:
        build_log.critical(f'Failed to re-initialize BIDS Layout in workflow builder: {e}')
        # Optionally, provide more details
        import traceback

        build_log.error(f'Full Traceback:\n{traceback.format_exc()}')
        retval['return_code'] = 1  # Indicate failure
        retval['workflow'] = None
        return retval  # Exit early if layout fails
    # -------------------------------------------------- #

    version = config.environment.version

    retval['return_code'] = 1
    retval['workflow'] = None

    banner = [f'Running NCDLMUSE version {version}']
    notice_path = data.load.readable('NOTICE')
    if notice_path.exists():
        banner[0] += '\n'
        banner += [f'License NOTICE {"#" * 50}']
        banner += [f'NCDLMUSE {version}']
        banner += notice_path.read_text().splitlines(keepends=False)[1:]
        banner += ['#' * len(banner[1])]
    build_log.log(25, f'\n{" " * 9}'.join(banner))

    subject_list_for_logging = config.execution.participant_label or ['all']

    if config.execution.reports_only:
        from ncdlmuse.data import load as load_data

        build_log.log(
            25, 'Running --reports-only on participants %s', ', '.join(subject_list_for_logging)
        )
        session_list = config.execution.session_label
        build_log.warning('Reports-only mode might need layout object, check implementation.')

        retval['return_code'] = generate_reports(
            subject_list=subject_list_for_logging,
            output_dir=config.execution.ncdlmuse_dir,
            run_uuid=config.execution.run_uuid,
            session_list=session_list,
            bootstrap_file=load_data('reports-spec.yml'),
        )
        return retval

    participant_filter = (
        ', '.join(config.execution.participant_label)
        if config.execution.participant_label
        else 'All'
    )
    init_msg = [
        "Building NCDLMUSE's workflow:",
        f'BIDS dataset path: {config.execution.bids_dir}.',
        f'Found {len(config.execution.t1w_list or [])} T1w files for processing.',
        f'Run identifier: {config.execution.run_uuid}.',
        f'Participant: {participant_filter}.',
        f'Session filter: {config.execution.session_label}.',
    ]

    build_log.log(25, f'\n{" " * 11}* '.join(init_msg))

    retval['workflow'] = init_ncdlmuse_wf()

    missing = check_deps(retval['workflow'])
    if missing:
        build_log.critical(
            'Cannot run NCDLMUSE. Missing dependencies:%s',
            '\n\t* '.join([''] + [f'{cmd} (Interface: {iface})' for iface, cmd in missing]),
        )
        retval['return_code'] = 127
        return retval

    config.to_filename(config_file)
    build_log.info(
        'NCDLMUSE workflow graph with %d nodes built successfully.',
        len(retval['workflow']._get_all_nodes()),
    )
    retval['return_code'] = 0
    return retval


def build_boilerplate(config_file, workflow):
    """Write boilerplate in an isolated process."""
    import traceback

    from ncdlmuse import config

    # Create a debug log file to capture what's happening
    debug_log_file = None
    try:
        config.load(config_file)

        # Create debug log file in the logs directory
        citation_logs_path = config.execution.ncdlmuse_dir / 'logs'
        citation_logs_path.mkdir(parents=True, exist_ok=True)
        debug_log_file = citation_logs_path / 'boilerplate_debug.log'

        def debug_log(message):
            """Log to both config logger and debug file."""
            try:
                config.loggers.cli.info(message)
            except (AttributeError, RuntimeError):
                # Logger might not be available in subprocess
                pass
            if debug_log_file:
                try:
                    with open(debug_log_file, 'a') as f:
                        f.write(f'{message}\n')
                        f.flush()
                except (OSError, PermissionError):
                    # Can't write to debug file, but don't fail
                    pass

        debug_log('Starting citation boilerplate generation...')
        debug_log(f'Config file: {config_file}')
        debug_log(f'Citation logs directory: {citation_logs_path}')

        citation_files = {
            ext: citation_logs_path / f'CITATION.{ext}' for ext in ('bib', 'tex', 'md', 'html')
        }
        debug_log(f'Citation files to generate: {list(citation_files.keys())}')

        # Check if citation files already exist and skip if they do
        existing_files = [f for f in citation_files.values() if f.exists()]
        if existing_files:
            debug_log(
                f'Citation files exist: {[f.name for f in existing_files]}. Skipping generation.'
            )
            return

        debug_log('No existing citation files found, proceeding with generation.')

        boilerplate = workflow.visit_desc()
        debug_log(
            f'Generated boilerplate content: {len(boilerplate) if boilerplate else 0} characters'
        )

        if not boilerplate:
            debug_log('No boilerplate content generated. Skipping citation files.')
            return

        # Generate the markdown file first
        citation_files['md'].write_text(boilerplate)
        debug_log(f'Generated {citation_files["md"].name}')

        if not config.execution.md_only_boilerplate:
            from shutil import copyfile, which
            from subprocess import CalledProcessError, TimeoutExpired, check_call

            from ncdlmuse.data import load as load_data

            # Check if pandoc is available
            pandoc_path = which('pandoc')
            if not pandoc_path:
                debug_log('Pandoc not found in PATH. Skipping HTML and TeX generation.')
                return

            debug_log(f'Found pandoc at: {pandoc_path}')

            # Generate HTML version (using bibliography for proper citation processing)
            cmd = [
                'pandoc',
                '-s',
                '--bibliography',
                str(load_data('boilerplate.bib')),
                '--citeproc',
                '--metadata',
                'pagetitle=BIDS_NiChart_DLMUSE citation boilerplate',
                str(citation_files['md']),
                '-o',
                str(citation_files['html']),
            ]

            debug_log('Generating an HTML version of the citation boilerplate...')
            debug_log(f'Pandoc command: {" ".join(cmd)}')
            try:
                check_call(cmd, timeout=10)
                debug_log(f'Generated {citation_files["html"].name}')
            except (FileNotFoundError, CalledProcessError, TimeoutExpired) as e:
                debug_log(f'Could not generate CITATION.html file: {e}')

            # Generate LaTeX version
            cmd = [
                'pandoc',
                '-s',
                '--bibliography',
                str(load_data('boilerplate.bib')),
                '--natbib',
                str(citation_files['md']),
                '-o',
                str(citation_files['tex']),
            ]
            debug_log('Generating a LaTeX version of the citation boilerplate...')
            debug_log(f'Pandoc command: {" ".join(cmd)}')
            try:
                check_call(cmd, timeout=10)
                debug_log(f'Generated {citation_files["tex"].name}')
                # Only copy bib file if tex generation succeeded
                copyfile(load_data('boilerplate.bib'), citation_files['bib'])
                debug_log(f'Generated {citation_files["bib"].name}')
            except (FileNotFoundError, CalledProcessError, TimeoutExpired) as e:
                debug_log(f'Could not generate CITATION.tex file: {e}')
        else:
            debug_log('md_only_boilerplate is True, skipping HTML and TeX generation.')

        debug_log('Citation boilerplate generation completed.')

    except Exception as e:
        error_msg = f'Exception in build_boilerplate: {e}'
        traceback_msg = f'Traceback: {traceback.format_exc()}'

        # Try to log to debug file even if config logging fails
        if debug_log_file:
            try:
                with open(debug_log_file, 'a') as f:
                    f.write(f'{error_msg}\n{traceback_msg}\n')
                    f.flush()
            except (OSError, PermissionError):
                # Can't write to debug file, but don't prevent the original exception
                pass
        raise
