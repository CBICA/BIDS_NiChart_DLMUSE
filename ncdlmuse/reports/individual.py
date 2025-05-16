# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# Copyright The NiPreps Developers <nipreps@gmail.com>
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
import json
import re
from pathlib import Path

import yaml
from bids.layout import BIDSLayout, BIDSLayoutIndexer
from nireports.assembler.report import Report

from ncdlmuse import config, data


def generate_reports(
    subject_list,
    output_dir,  # This is the main derivatives output directory (e.g., .../ncdlmuse/)
    run_uuid,
    session_list=None,  # List of session labels (without 'ses-' prefix)
    bootstrap_file=None,  # Path to reports-spec.yml (string or Path)
    work_dir=None,
    boilerplate_only=False,
    layout: BIDSLayout = None,  # The BIDSLayout object (already including derivatives)
):
    """Generate reports for a list of subjects using nireports."""
    report_errors = []

    if not layout:
        config.loggers.cli.error(
            'BIDSLayout (already including derivatives) is required for report generation.'
        )
        return 1

    output_dir_path = Path(output_dir).absolute()

    # The reportlets are in the work directory under reportlets/
    reportlets_dir = None
    if work_dir is not None:
        reportlets_dir = Path(work_dir) / 'reportlets'

    if isinstance(subject_list, str):
        subject_list = [subject_list]

    reportlets_dir.mkdir(parents=True, exist_ok=True)

    # Create a BIDSLayout specifically for report generation,
    # ensuring it knows about the reportlets_dir.
    # We need a 'root' for BIDSLayout, even if we're primarily interested in derivatives.
    # Using the work_dir as a pseudo-root or the original layout's root.
    # Let's use the original layout's root and add our reportlets_dir as a derivative.

    report_layout_root = layout.root if layout else work_dir # Fallback if original layout is None

    # Ensure dataset_description.json exists in reportlets_dir for BIDSLayout
    # This is crucial for BIDSLayout to recognize it as a valid BIDS (derivative) dataset
    desc_file = reportlets_dir / 'dataset_description.json'
    if not desc_file.exists():
        config.loggers.cli.info(f'Creating dummy dataset_description.json in {reportlets_dir}')
        desc_content = {
            'Name': 'NCDLMUSE Reportlets',
            'BIDSVersion': '1.10.0', # Or a version appropriate for nireports
            'GeneratedBy': [{'Name': 'ncdlmuse'}]
        }
        with open(desc_file, 'w') as f:
            json.dump(desc_content, f, indent=2)

    try:
        report_specific_layout = BIDSLayout(
            root=str(report_layout_root), # or simply layout.root
            derivatives=str(reportlets_dir),
            validate=False, # Basic validation
            indexer=BIDSLayoutIndexer(validate=False, index_metadata=False)
        )
        config.loggers.cli.info(
            f"Initialized report_specific_layout with root '{report_layout_root}' and "
            f"derivatives '{reportlets_dir}'")
    except Exception as e:
        config.loggers.cli.error(f'Failed to create report_specific_layout: {e}')
        # Fallback to original layout if specific one fails, but log it.
        report_specific_layout = layout

    for subject_label_with_prefix in subject_list:
        subject_id_for_report = subject_label_with_prefix.lstrip('sub-')

        if boilerplate_only:
            config.loggers.cli.info(f'Generating boilerplate for {subject_label_with_prefix}...')
            Path(output_dir_path / f'{subject_label_with_prefix}_CITATION.md').write_text(
                f'# Boilerplate for {subject_label_with_prefix}\n'
                f'NCDLMUSE Version: {config.environment.version}'
            )
            continue

        # The number of sessions is intentionally not based on session_list but
        # on the total number of sessions, because we want the final derivatives
        # folder to be the same whether sessions were run one at a time or all-together.
        n_ses = len(layout.get_sessions(subject=subject_id_for_report))

        # Fallback for aggr_ses_reports if not defined in config
        aggr_ses_reports_threshold = getattr(config.execution, 'aggr_ses_reports', 3)

        if bootstrap_file is not None:
            # If a config file is specified, we do not override it
            html_report = f'sub-{subject_id_for_report}.html'
        elif n_ses <= aggr_ses_reports_threshold:
            # If there are only a few sessions for this subject,
            # we aggregate them in a single visual report.
            bootstrap_file = data.load('reports-spec.yml')
            html_report = f'sub-{subject_id_for_report}.html'
        else:
            # Beyond a threshold, we separate the reports
            bootstrap_file = data.load('reports-spec.yml')
            html_report = f'sub-{subject_id_for_report}.html'

        try:
            final_html_path = output_dir_path / html_report
            config.loggers.cli.info(f'Generating report for {subject_label_with_prefix}...')
            config.loggers.cli.info(f'HTML will be: {final_html_path}')
            if reportlets_dir:
                config.loggers.cli.info(f'Reportlets base dir for nireports: {reportlets_dir}')
            if isinstance(bootstrap_file, str | Path):
                config.loggers.cli.info(f'Bootstrap file for nireports: {bootstrap_file}')

            # Generate the report
            robj = Report(
                out_dir=str(output_dir_path),
                run_uuid=run_uuid,
                config=bootstrap_file,
                reportlets_dir=reportlets_dir,
                plugins=None,
                out_filename=html_report,
                subject=subject_id_for_report,
                layout=report_specific_layout,
            )

            robj.generate_report()
            config.loggers.cli.info(
                f'Successfully generated report for {subject_label_with_prefix} at '
                f'{final_html_path}'
            )

        except Exception as e:
            err_msg = f'Report generation failed for {subject_label_with_prefix}: {e}'
            config.loggers.cli.error(err_msg, exc_info=True)
            report_errors.append(subject_label_with_prefix)

        if n_ses > aggr_ses_reports_threshold:
            # Beyond a certain number of sessions per subject,
            # we separate the reports per session
            if session_list is None:
                all_filters = config.execution.bids_filters or {}
                filters = all_filters.get('t1w', {})  # Use t1w instead of asl for our case
                session_list = layout.get_sessions(
                    subject=subject_id_for_report, **filters
                )

            # Drop ses- prefixes
            session_list = [ses[4:] if ses.startswith('ses-') else ses for ses in session_list]

            for session_label in session_list:
                bootstrap_file = data.load('reports-spec.yml')
                html_report = f'sub-{subject_id_for_report}_ses-{session_label}.html'

                try:
                    final_html_path = output_dir_path / html_report
                    config.loggers.cli.info(
                        f'Generating session report for {subject_label_with_prefix} '
                        f'session {session_label}...')
                    config.loggers.cli.info(f'Session HTML will be: {final_html_path}')

                    # Generate the session report
                    robj = Report(
                        out_dir=str(output_dir_path),
                        run_uuid=run_uuid,
                        config=bootstrap_file,
                        reportlets_dir=reportlets_dir,
                        plugins=None,
                        out_filename=html_report,
                        subject=subject_id_for_report,
                        session=session_label,
                        layout=report_specific_layout,
                    )

                    robj.generate_report()
                    config.loggers.cli.info(
                        f'Successfully generated session report for {subject_label_with_prefix} '
                        f'session {session_label} at {final_html_path}'
                    )

                except Exception as e:
                    err_msg = (
                        f'Session report generation failed for {subject_label_with_prefix} '
                        f'session {session_label}: {e}'
                    )
                    config.loggers.cli.error(err_msg, exc_info=True)
                    report_errors.append(f'{subject_label_with_prefix}_ses-{session_label}')

    if report_errors:
        joined_error_subjects = ', '.join(report_errors)
        config.loggers.cli.error(
            f'Report generation failed for the following subjects: {joined_error_subjects}'
        )
        return 1

    return 0
