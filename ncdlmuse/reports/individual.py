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
from pathlib import Path
import yaml
import re
import json

from nireports.assembler.report import Report
from bids.layout import BIDSLayout

from ncdlmuse import config, data


def generate_reports(
    subject_list,
    output_dir, # This is the main derivatives output directory (e.g., .../ncdlmuse/)
    run_uuid,
    session_list=None, # List of session labels (without 'ses-' prefix)
    bootstrap_file=None, # Path to reports-spec.yml (string or Path)
    work_dir=None,
    boilerplate_only=False,
    layout: BIDSLayout = None, # The BIDSLayout object (already includes derivatives)
):
    """
    Generate reports for a list of subjects using nireports.
    Adopts the fMRIPrep pattern for instantiating nireports.Report.
    """
    report_errors = []

    if not layout:
        config.loggers.cli.error(
            'BIDSLayout (already including derivatives) is required for report generation.'
        )
        return 1

    output_dir_path = Path(output_dir).absolute()
    reportlets_dir_for_nireport = output_dir_path

    if isinstance(subject_list, str):
        subject_list = [subject_list]

    # Load bootstrap file
    loaded_bootstrap_config = {}
    if bootstrap_file is None:
        spec_file_path = data.load('reports-spec.yml')
        try:
            with open(spec_file_path, 'r') as f:
                loaded_bootstrap_config = yaml.safe_load(f)
        except Exception as e:
            config.loggers.cli.error(f'Error loading bootstrap file: {e}')
            return 1
    elif isinstance(bootstrap_file, str | Path):
        spec_file_path = Path(bootstrap_file)
        try:
            with open(spec_file_path, 'r') as f:
                loaded_bootstrap_config = yaml.safe_load(f)
        except Exception as e:
            config.loggers.cli.error(f'Error loading bootstrap file: {e}')
            return 1
    elif isinstance(bootstrap_file, dict):
        loaded_bootstrap_config = bootstrap_file
    else:
        config.loggers.cli.error('Invalid bootstrap_file type')
        return 1

    for subject_label_with_prefix in subject_list:
        subject_id_for_report = subject_label_with_prefix.lstrip('sub-')
        report_save_directory = output_dir_path
        out_html_filename = f'{subject_label_with_prefix}.html'

        if boilerplate_only:
            config.loggers.cli.info(f'Generating boilerplate for {subject_label_with_prefix}...')
            Path(report_save_directory / f'{subject_label_with_prefix}_CITATION.md').write_text(
                f'# Boilerplate for {subject_label_with_prefix}\n'
                f'NCDLMUSE Version: {config.environment.version}'
            )
            continue

        try:
            final_html_path = report_save_directory / out_html_filename
            config.loggers.cli.info(f'Generating report for {subject_label_with_prefix}...')
            config.loggers.cli.info(f'Main HTML will be: {final_html_path}')
            config.loggers.cli.info(f'Reportlets base dir for nireports: {reportlets_dir_for_nireport}')
            if isinstance(bootstrap_file, (str, Path)):
                config.loggers.cli.info(f'Bootstrap file for nireports: {spec_file_path}')

            # Prepare entities to be passed as keyword arguments to nireports.Report
            report_constructor_kwargs = {
                'bootstrap_file': loaded_bootstrap_config,
                'out_filename': out_html_filename,
                'reportlets_dir': str(reportlets_dir_for_nireport),
                'layout': layout,
                'subject': subject_id_for_report,
                'output_dir': str(report_save_directory),
            }

            if session_list and len(session_list) == 1:
                session_id_for_entity = session_list[0].lstrip('ses-')
                if layout.get_sessions(
                    subject=subject_id_for_report, session=session_id_for_entity):
                    report_constructor_kwargs['session'] = session_id_for_entity
                else:
                    config.loggers.cli.warning(
                        f"Specified session '{session_id_for_entity}' not found for subject "
                        f"'{subject_id_for_report}'. Not passing session to Report constructor."
                    )
            elif session_list and len(session_list) > 1:
                config.loggers.cli.warning(
                    f"Multiple sessions provided ({session_list}) for subject "
                    f"'{subject_id_for_report}'. Report will be subject-level. "
                    f"Session-specific components depend on spec file."
                )

            # Generate the report
            robj = Report(
                str(report_save_directory),    # 1. Positional: out_dir
                run_uuid,                      # 2. Positional: run_uuid
                **report_constructor_kwargs
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

    if report_errors:
        joined_error_subjects = ', '.join(report_errors)
        config.loggers.cli.error(
            f'Report generation failed for the following subjects: {joined_error_subjects}'
        )
        return 1

    return 0
