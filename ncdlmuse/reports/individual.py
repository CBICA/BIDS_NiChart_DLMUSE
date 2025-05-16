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
from nireports.assembler.report import Report as NireportsReport

from ncdlmuse import config, data


# Custom Report class to safely handle the layout object
class SafeReport(NireportsReport):
    def __init__(self, out_dir, run_uuid, layout=None, **kwargs):
        self._safe_layout = layout
        super().__init__(out_dir, run_uuid, **kwargs)

    def index(self, settings=None):
        if hasattr(self, '_safe_layout') and self._safe_layout is not None:
            self.layout = self._safe_layout
        else:
            config.loggers.cli.warning('SafeReport.index called without a _safe_layout.')
            if settings and 'layout' in settings and isinstance(settings['layout'], BIDSLayout):
                 self.layout = settings['layout']
            elif self.layout is None: # If self.layout wasn't set by super().__init__ either
                 config.loggers.cli.error('No BIDSLayout available for SafeReport.index.')
                 # Potentially raise an error or try to create a default one, though risky.
                 # For now, this will likely cause issues in super().index(settings)
        return super().index(settings)


def generate_reports(
    subject_list,
    output_dir,  # This is the main derivatives output directory (e.g., .../ncdlmuse/)
    run_uuid,
    session_list=None,  # List of session labels (without 'ses-' prefix)
    bootstrap_file=None,  # Path to reports-spec.yml (string or Path)
    work_dir=None, # Not directly used for reportlet location anymore
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

    # Ensure the provided layout has invalid_filters='allow'
    # This is crucial. The layout from cli/run.py must be created with this.
    layout_needs_recreation = False
    if hasattr(layout, 'config') and isinstance(layout.config, dict):
        if layout.config.get('invalid_filters') != 'allow':
            layout_needs_recreation = True
            config.loggers.cli.warning(
                "The BIDSLayout provided to generate_reports does not have invalid_filters='allow'"
                " set in its config. Attempting to re-create it with this setting."
            )
    else: # No config attribute, or not a dict, assume it needs invalid_filters='allow'
        layout_needs_recreation = True
        config.loggers.cli.warning(
            "The BIDSLayout provided to generate_reports does not have a standard config attribute
            " or it is not a dict. Attempting to re-create it with invalid_filters='allow'."
        )

    if layout_needs_recreation:
        try:
            # Preserve original derivatives and root if possible
            original_derivatives = layout.derivatives if hasattr(layout, 'derivatives') else None
            original_root = layout.root if hasattr(layout, 'root') else None

            if original_root is None:
                config.loggers.cli.error(
                    'Original layout root is None, cannot re-create layout for report generation.'
                )
                return 1 # Cannot proceed without a root

            new_layout_derivatives = {}
            if isinstance(original_derivatives, dict):
                new_layout_derivatives = {k: str(v.path) for k, v in original_derivatives.items() \
                    if hasattr(v, 'path')}
            elif isinstance(original_derivatives, list):
                new_layout_derivatives = [str(p) for p in original_derivatives]
            elif isinstance(original_derivatives, str | Path):
                new_layout_derivatives = str(original_derivatives)
            else:
                 # Fallback if derivatives format is unknown, use output_dir_path
                 # This might be too simplistic if multiple derivative paths were orig configured.
                new_layout_derivatives = str(Path(output_dir).absolute())
                config.loggers.cli.warning(
                    f"Original layout derivatives format not recognized or empty. Using output_dir"
                    f" '{new_layout_derivatives}' as derivative for re-created layout."
                )


            layout = BIDSLayout(
                root=str(original_root),
                derivatives=new_layout_derivatives,
                config={'invalid_filters': 'allow'},
                validate=False, # Keep validation off for performance/flexibility
                indexer=BIDSLayoutIndexer(validate=False, index_metadata=False)
            )
            config.loggers.cli.info(
                f"Successfully re-created BIDSLayout for reports with invalid_filters='allow'. "
                f"Root: {layout.root}, Derivatives: {layout.derivatives}"
            )
        except Exception as e:
            config.loggers.cli.error(
                f"Failed to re-create BIDSLayout with invalid_filters='allow': {e}. "
                "Report generation may fail."
            )
            # Continue with the original layout, the error might persist or manifest differently

    output_dir_path = Path(output_dir).absolute()
    reportlets_dir_for_nireports = output_dir_path

    if isinstance(subject_list, str):
        subject_list = [subject_list]

    for subject_label_with_prefix in subject_list:
        subject_id_for_report = subject_label_with_prefix.lstrip('sub-')

        if boilerplate_only:
            Path(output_dir_path / f'{subject_label_with_prefix}_CITATION.md').write_text(
                f'# Boilerplate for {subject_label_with_prefix}\\n'
                f'NCDLMUSE Version: {config.environment.version}'
            )
            continue

        n_ses = len(layout.get_sessions(subject=subject_id_for_report))
        aggr_ses_reports_threshold = getattr(config.execution, 'aggr_ses_reports', 3)

        current_bootstrap_file = bootstrap_file
        if current_bootstrap_file is None:
            current_bootstrap_file = data.load('reports-spec.yml')

        if n_ses <= aggr_ses_reports_threshold:
            html_report_filename = f'sub-{subject_id_for_report}.html'
        else:
            # Main subject report still named this way, session reports will be separate
            html_report_filename = f'sub-{subject_id_for_report}.html' 

        try:
            final_html_path = output_dir_path / html_report_filename
            config.loggers.cli.info(f'Generating report for {subject_label_with_prefix}...')
            config.loggers.cli.info(f'HTML will be: {final_html_path}')
            config.loggers.cli.info(
                f'Reportlets dir for nireports: {reportlets_dir_for_nireports}')
            if isinstance(current_bootstrap_file, str | Path):
                config.loggers.cli.info(
                    f'  Bootstrap file for nireports: {current_bootstrap_file}')

            robj = SafeReport(
                out_dir=str(output_dir_path), # Where the html_report_filename is saved
                run_uuid=run_uuid,
                bootstrap_file=current_bootstrap_file,
                reportlets_dir=str(reportlets_dir_for_nireports),
                plugins=None,
                out_filename=html_report_filename,
                subject=subject_id_for_report,
                session=None,
                layout=layout, # Use the main, pre-configured layout
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
            active_session_list = session_list
            if active_session_list is None:
                all_filters = config.execution.bids_filters or {}
                filters = all_filters.get('t1w', {})
                active_session_list = layout.get_sessions(
                    subject=subject_id_for_report, **filters
                )
            active_session_list = [ses[4:] if ses.startswith('ses-') \
                else ses for ses in active_session_list]

            for session_label in active_session_list:
                session_bootstrap_file = bootstrap_file
                if session_bootstrap_file is None:
                    session_bootstrap_file = data.load('reports-spec.yml')

                session_html_report_filename = \
                    f'sub-{subject_id_for_report}_ses-{session_label}.html'
                try:
                    final_session_html_path = output_dir_path / session_html_report_filename
                    config.loggers.cli.info(
                        f'Generating session report for {subject_label_with_prefix} '
                        f'session {session_label}...'
                    )
                    config.loggers.cli.info(f'  Session HTML will be: {final_session_html_path}')

                    srobj = SafeReport(
                        out_dir=str(output_dir_path),
                        run_uuid=run_uuid,
                        bootstrap_file=session_bootstrap_file,
                        reportlets_dir=str(reportlets_dir_for_nireports),
                        plugins=None,
                        out_filename=session_html_report_filename,
                        subject=subject_id_for_report,
                        session=session_label,
                        layout=layout, # Use the main, pre-configured layout
                    )
                    srobj.generate_report()
                    config.loggers.cli.info(
                        f'Successfully generated session report for {subject_label_with_prefix} '
                        f'session {session_label} at {final_session_html_path}'
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
