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
    session_list=None,    # List of session labels (without 'ses-' prefix)
    bootstrap_file=None,  # Path to reports-spec.yml (string or Path)
    work_dir=None,
    boilerplate_only=False,
    layout: BIDSLayout = None,  # The BIDSLayout object (already includes derivatives)
):
    """
    Generate reports for a list of subjects using nireports.
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

    # Initial loading of spec_file_path_to_load (if bootstrap_file is a path or None)
    # This part was corrected for proper try/except/else structure.
    loaded_bootstrap_config = {} # This will hold the dict from yaml.safe_load
    if bootstrap_file is None:
        spec_file_path = data.load('reports-spec.yml')
        # try to load spec_file_path into loaded_bootstrap_config
    elif isinstance(bootstrap_file, str | Path):
        spec_file_path = Path(bootstrap_file)
        # try to load spec_file_path into loaded_bootstrap_config
    elif isinstance(bootstrap_file, dict):
        loaded_bootstrap_config = bootstrap_file
    else:
        # error
        return 1

    for subject_label_with_prefix in subject_list:
        subject_id_for_report = subject_label_with_prefix.lstrip('sub-')
        report_save_directory = Path(output_dir).absolute() # Ensure it's a Path
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
            config.loggers.cli.info(
                f'Reportlets base dir for nireports: {reportlets_dir_for_nireport}')
            config.loggers.cli.info(f'Bootstrap file for nireports: {spec_file_path}')

            # Prepare entities to be passed as keyword arguments to nireports.Report
            # These are used for BIDS filtering and for string formatting in the spec file.
            report_constructor_kwargs = {
                'bootstrap_file': loaded_bootstrap_config, # Use the loaded dict
                'out_filename': out_html_filename,
                'reportlets_dir': str(reportlets_dir_for_nireport),
                'subject': subject_id_for_report,
                'output_dir': str(report_save_directory), # For meta_repl
                'invalid_filters': 'allow',
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

            subject_figures_dir = output_dir_path / subject_label_with_prefix / 'figures'
            if subject_figures_dir.is_dir():
                figures_ds_desc = subject_figures_dir / 'dataset_description.json'
                if not figures_ds_desc.exists():
                     json.dump({
                        'Name': f'{subject_label_with_prefix} NCDLMUSE Reportlets',
                        'BIDSVersion': '1.4.1',
                        'DatasetType': 'derivative',
                        'GeneratedBy': [{
                            'Name': 'ncdlmuse',
                            'Version': config.environment.version
                            }]
                    }, figures_ds_desc.open('w'), indent=2)

            # === Instantiate Report with real BIDSLayout object ===
            robj = Report(
                str(report_save_directory),  # out_dir
                run_uuid,                    # run_uuid
                layout=layout,               # pass the BIDSLayout here
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
