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
import shutil
from pathlib import Path

from bids.layout import BIDSLayout, BIDSLayoutIndexer
from nireports.assembler.report import Report as NireportsReport

from ncdlmuse import config, data


# Custom Report class to safely handle the layout object
class SafeReport(NireportsReport):
    def __init__(self, out_dir, run_uuid, layout=None, reportlets_dir=None, **kwargs):
        self._safe_layout = layout
        self._reportlets_dir = reportlets_dir

        # Set reportlets_dir first as the parent class needs it during initialization
        if reportlets_dir:
            kwargs['reportlets_dir'] = str(reportlets_dir)

        super().__init__(out_dir, run_uuid, **kwargs)

    def index(self, settings=None):
        from pathlib import Path

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

        # Log the reportlets directory and settings
        if hasattr(self, 'reportlets_dir'):
            config.loggers.cli.info(f'SafeReport.index: reportlets_dir = {self.reportlets_dir}')
        elif hasattr(self, '_reportlets_dir'):
            config.loggers.cli.info(f'SafeReport.index: reportlets_dir = {self._reportlets_dir}')
            # Set the reportlets_dir attribute that nireports expects
            self.reportlets_dir = self._reportlets_dir
        else:
            config.loggers.cli.warning('SafeReport.index: No reportlets_dir provided')

        if settings:
            config.loggers.cli.info(f'SafeReport.index: settings = {settings}')

        # We'll do a manual check for reportlets to ensure they're found
        reportlets = []
        reportlets_path = Path(self.reportlets_dir)

        # Check that settings has the expected structure
        if settings and 'sections' in settings:
            for section in settings['sections']:
                if 'reportlets' in section:
                    for reportlet_spec in section['reportlets']:
                        if 'bids' in reportlet_spec:
                            # Match files based on BIDS entities
                            bids_spec = reportlet_spec['bids']
                            extension = bids_spec.get('extension')
                            desc = bids_spec.get('desc')

                            # Handle different extension formats
                            if extension:
                                if isinstance(extension, list):
                                    extensions = extension
                                else:
                                    extensions = [extension]
                            else:
                                extensions = ['.svg', '.html']

                            # Look for matching files
                            for ext in extensions:
                                # Use glob to find matching files
                                if desc:
                                    pattern = f'*{desc}*{ext}'
                                else:
                                    pattern = f'*{ext}'

                                matches = list(reportlets_path.glob(pattern))
                                for match in matches:
                                    reportlet_path = str(match)
                                    config.loggers.cli.info(f'Found reportlet: {reportlet_path}')
                                    reportlets.append(reportlet_path)

        # Store the manually found reportlets
        self._manual_reportlets = reportlets
        config.loggers.cli.info(f'Manually found {len(reportlets)} reportlets')

        # Call parent's index method
        try:
            result = super().index(settings)
        except Exception as e:
            config.loggers.cli.error(f'Error in parent index method: {e}')
            if hasattr(self, '_manual_reportlets') and self._manual_reportlets:
                config.loggers.cli.info('Using manually found reportlets')
                self.reportlets = self._manual_reportlets
            result = None

        # Log the reportlets that were found
        if hasattr(self, 'reportlets'):
            config.loggers.cli.info(f'SafeReport.index: Found {len(self.reportlets)} reportlets')
            for r in self.reportlets:
                config.loggers.cli.info(f'  - {r}')
        else:
            config.loggers.cli.warning(
                'SafeReport.index: No reportlets attribute found after indexing')
            # If the parent didn't set reportlets, use our manual finding
            if hasattr(self, '_manual_reportlets') and self._manual_reportlets:
                config.loggers.cli.info("Setting manually found reportlets")
                self.reportlets = self._manual_reportlets

        return result

    def generate_report(self):
        """Override generate_report to add more logging and ensure reportlets are used."""
        config.loggers.cli.info('SafeReport.generate_report: Starting report generation')
        config.loggers.cli.info(
            f'SafeReport.generate_report: reportlets_dir = {self.reportlets_dir}')

        # Make sure we have reportlets to use
        if not hasattr(self, 'reportlets') or not self.reportlets:
            if hasattr(self, '_manual_reportlets') and self._manual_reportlets:
                config.loggers.cli.info('Using manually found reportlets for report generation')
                self.reportlets = self._manual_reportlets

        # Log what reportlets we'll use
        if hasattr(self, 'reportlets'):
            config.loggers.cli.info(
                f'Using {len(self.reportlets)} reportlets for report generation:')
            for r in self.reportlets:
                config.loggers.cli.info(f'  - {r}')

        # Call parent's generate_report method
        result = super().generate_report()

        config.loggers.cli.info('SafeReport.generate_report: Finished report generation')
        return result


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

    output_dir_path = Path(output_dir).absolute()

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
            "The BIDSLayout provided to generate_reports does not have a standard config attribute"
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
                    f'Original layout derivatives format not recognized or empty. Using output_dir'
                    f" '{new_layout_derivatives}' as derivative for re-created layout."
                )

            # Add subject figures directory to derivatives if it exists
            for subject_label_with_prefix in subject_list:
                subject_id = subject_label_with_prefix.lstrip('sub-')
                subject_figures_dir = Path(output_dir).absolute() / f'sub-{subject_id}' / 'figures'
                if subject_figures_dir.exists():
                    if isinstance(new_layout_derivatives, dict):
                        new_layout_derivatives[f'sub-{subject_id}'] = \
                            str(subject_figures_dir.parent)
                    elif isinstance(new_layout_derivatives, list):
                        new_layout_derivatives.append(str(subject_figures_dir.parent))
                    else:
                        # If it's a string, convert to list and add
                        new_layout_derivatives = \
                            [new_layout_derivatives, str(subject_figures_dir.parent)]
                    config.loggers.cli.info(
                        f'Added subject figures parent directory to layout: '
                        f'{subject_figures_dir.parent}'
                    )

            # If new_layout_derivatives is empty or still a string referring to out_dir
            if (isinstance(new_layout_derivatives, dict) and not new_layout_derivatives) or \
               (isinstance(new_layout_derivatives, str) and \
                str(Path(output_dir).absolute()) in new_layout_derivatives):
                # Add the output_dir explicitly to ensure it's included
                subject_dirs = []
                for subject_label_with_prefix in subject_list:
                    subject_id = subject_label_with_prefix.lstrip('sub-')
                    subject_dir = Path(output_dir).absolute() / f'sub-{subject_id}'
                    if subject_dir.exists():
                        subject_dirs.append(str(subject_dir))

                # Add all subject directories found
                if subject_dirs:
                    if isinstance(new_layout_derivatives, dict):
                        for i, dir_path in enumerate(subject_dirs):
                            new_layout_derivatives[f'subdir_{i}'] = dir_path
                    elif isinstance(new_layout_derivatives, list):
                        new_layout_derivatives.extend(subject_dirs)
                    else:
                        # Convert to list
                        new_layout_derivatives = [new_layout_derivatives] + subject_dirs

                config.loggers.cli.info(
                    f'Added subject directories to layout derivatives: {subject_dirs}'
                )

            layout = BIDSLayout(
                root=str(original_root),
                derivatives=new_layout_derivatives,
                invalid_filters='allow',
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

        # Update reportlets_dir to point to the subject's figures directory
        subject_figures_dir = output_dir_path / f'sub-{subject_id_for_report}' / 'figures'
        if subject_figures_dir.exists():
            reportlets_dir_for_nireports = subject_figures_dir
            config.loggers.cli.info(
                f'Using figures from: {reportlets_dir_for_nireports}'
            )
        else:
            config.loggers.cli.warning(
                f'Figures directory not found at: {subject_figures_dir}'
            )

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

            try:
                # List reportlets to verify they exist
                svg_reportlets = list(reportlets_dir_for_nireports.glob('*.svg'))
                html_reportlets = list(reportlets_dir_for_nireports.glob('*.html'))
                if not svg_reportlets and not html_reportlets:
                    config.loggers.cli.error(
                        f'No SVG or HTML reportlets found in {reportlets_dir_for_nireports}'
                    )
                    raise FileNotFoundError(
                        f'No reportlets found in {reportlets_dir_for_nireports}')

                config.loggers.cli.info(
                    f'Found {len(svg_reportlets)} SVG reportlets and '
                    f'{len(html_reportlets)} HTML reportlets in {reportlets_dir_for_nireports}'
                )

                # Log the actual reportlet filenames for debugging
                if svg_reportlets:
                    config.loggers.cli.info('SVG reportlets:')
                    for r in svg_reportlets:
                        config.loggers.cli.info(f'  - {r.name}')
                if html_reportlets:
                    config.loggers.cli.info('HTML reportlets:')
                    for r in html_reportlets:
                        config.loggers.cli.info(f'  - {r.name}')

                # Ensure the reportlets directory exists and is accessible
                if not reportlets_dir_for_nireports.exists():
                    config.loggers.cli.error(
                        f'Reportlets directory does not exist: {reportlets_dir_for_nireports}'
                    )
                    raise FileNotFoundError(
                        f'Reportlets directory not found: {reportlets_dir_for_nireports}')

                # Create the report object with absolute paths
                robj = SafeReport(
                    out_dir=str(output_dir_path.absolute()),  # Use absolute path
                    run_uuid=run_uuid,
                    bootstrap_file=current_bootstrap_file,
                    reportlets_dir=str(reportlets_dir_for_nireports.absolute()),
                    plugins=None,
                    out_filename=html_report_filename,
                    subject=subject_id_for_report,
                    session=None,
                    layout=layout,  # Use the main, pre-configured layout
                )

                # Generate the report
                robj.generate_report()

                # Verify the report was generated
                if not final_html_path.exists():
                    config.loggers.cli.error(
                        f'Report generation completed but file not found: {final_html_path}'
                    )
                    raise FileNotFoundError(f'Report file not found: {final_html_path}')

                # Verify the report has content
                if final_html_path.stat().st_size == 0:
                    config.loggers.cli.error(
                        f'Report file is empty: {final_html_path}'
                    )
                    raise RuntimeError(f'Generated report is empty: {final_html_path}')

                # Verify the report contains the expected content
                report_content = final_html_path.read_text()
                if not report_content.strip():
                    config.loggers.cli.error(
                        f'Report file exists but has no content: {final_html_path}'
                    )
                    raise RuntimeError(f'Generated report has no content: {final_html_path}')

                config.loggers.cli.info(
                    f'Successfully generated report for {subject_label_with_prefix} at '
                    f'{final_html_path}'
                )
            except Exception as e:
                err_msg = f'Report generation failed for {subject_label_with_prefix}: {e}'
                config.loggers.cli.error(err_msg, exc_info=True)
                report_errors.append(subject_label_with_prefix)
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
