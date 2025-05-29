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
    from ncdlmuse import config

    config.load(config_file)
    logs_path = config.execution.ncdlmuse_dir / 'logs'
    logs_path.mkdir(parents=True, exist_ok=True)
    boilerplate = workflow.visit_desc()
    citation_files = {ext: logs_path / f'CITATION.{ext}' for ext in ('bib', 'tex', 'md', 'html')}

    if boilerplate:
        # To please git-annex users and also to guarantee consistency
        # among different renderings of the same file, first remove any
        # existing one
        for citation_file in citation_files.values():
            try:
                citation_file.unlink()
            except FileNotFoundError:
                pass

    citation_files['md'].write_text(boilerplate)

    if not config.execution.md_only_boilerplate and citation_files['md'].exists():
        from shutil import copyfile
        from subprocess import CalledProcessError, TimeoutExpired, check_call

        from ncdlmuse.data import load as load_data

        # Generate HTML file resolving citations
        cmd = [
            'pandoc',
            '-s',
            '--bibliography',
            str(load_data('boilerplate.bib')),
            '--citeproc',
            '--metadata',
            'pagetitle="NCDLMUSE citation boilerplate"',
            str(citation_files['md']),
            '-o',
            str(citation_files['html']),
        ]

        config.loggers.cli.info('Generating an HTML version of the citation boilerplate...')
        try:
            check_call(cmd, timeout=10)
        except (FileNotFoundError, CalledProcessError, TimeoutExpired):
            config.loggers.cli.warning('Could not generate CITATION.html file:\n%s', ' '.join(cmd))

        # Generate LaTex file resolving citations
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
        config.loggers.cli.info('Generating a LaTeX version of the citation boilerplate...')
        try:
            check_call(cmd, timeout=10)
        except (FileNotFoundError, CalledProcessError, TimeoutExpired):
            config.loggers.cli.warning('Could not generate CITATION.tex file:\n%s', ' '.join(cmd))
        else:
            copyfile(load_data('boilerplate.bib'), citation_files['bib'])
