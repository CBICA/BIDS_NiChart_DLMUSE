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

        if reportlets_dir:
            kwargs['reportlets_dir'] = str(reportlets_dir)

        super().__init__(out_dir, run_uuid, **kwargs)

    def _load_reportlet(self, reportlet_path):
        """Override _load_reportlet to properly load and include reportlets."""
        import shutil
        import os
        from pathlib import Path

        config.loggers.cli.info(f'Loading reportlet: {reportlet_path}')

        reportlet_path = Path(reportlet_path)

        if not reportlet_path.exists():
            config.loggers.cli.error(f'Reportlet not found: {reportlet_path}')
            return None

        if reportlet_path.suffix == '.html':
            try:
                return reportlet_path.read_text()
            except Exception as e:
                config.loggers.cli.error(f'Error reading HTML reportlet: {e}')
                return None
        elif reportlet_path.suffix == '.svg':
            try:
                # We should NOT create a new figures directory in the output dir
                # The SVG files should already be in the subject's figures directory
                # Just return the relative path to the SVG file
                return str(reportlet_path.name)
            except Exception as e:
                config.loggers.cli.error(f'Error handling SVG reportlet: {e}')
                return None

        return None

    def index(self, settings=None):
        if hasattr(self, '_safe_layout') and self._safe_layout is not None:
            self.layout = self._safe_layout
        elif settings and 'layout' in settings and isinstance(settings['layout'], BIDSLayout):
            self.layout = settings['layout']
        elif self.layout is None:
            config.loggers.cli.error('No BIDSLayout available for SafeReport.index.')

        if hasattr(self, '_reportlets_dir'):
            self.reportlets_dir = self._reportlets_dir

        reportlets = []
        reportlets_path = Path(self.reportlets_dir)

        if settings and 'sections' in settings:
            for section in settings['sections']:
                if 'reportlets' in section:
                    for reportlet_spec in section['reportlets']:
                        if 'bids' in reportlet_spec:
                            bids_spec = reportlet_spec['bids']
                            extension = bids_spec.get('extension', ['.svg', '.html'])
                            desc = bids_spec.get('desc')

                            if not isinstance(extension, list):
                                extensions = [extension]
                            else:
                                extensions = extension

                            for ext in extensions:
                                pattern = f'*{desc}*{ext}' if desc else f'*{ext}'
                                matches = list(reportlets_path.glob(pattern))
                                for match in matches:
                                    config.loggers.cli.info(f'Found reportlet: {match}')
                                    reportlets.append(str(match))

        self._manual_reportlets = reportlets
        config.loggers.cli.info(f'Manually found {len(reportlets)} reportlets')

        try:
            super().index(settings)
        except Exception as e:
            config.loggers.cli.error(f'Error in parent index method: {e}')

        if not hasattr(self, 'reportlets') or not self.reportlets:
            if self._manual_reportlets:
                self.reportlets = self._manual_reportlets

        return self.reportlets

    def generate_report(self):
        """Let parent class handle the report generation."""
        import inspect
        from pathlib import Path

        if not hasattr(self, 'reportlets') or not self.reportlets:
            if hasattr(self, '_manual_reportlets') and self._manual_reportlets:
                self.reportlets = self._manual_reportlets

        if not hasattr(self, 'reportlets') or not self.reportlets:
            config.loggers.cli.error('No reportlets available for report generation')
            return None

        # Debug: Print all instance variables
        instance_vars = vars(self)
        config.loggers.cli.info(f'Available attributes: {list(instance_vars.keys())}')

        # We'll let the parent class handle the report generation
        # This will use the template from nireports properly
        try:
            # Call parent generate_report method which handles template rendering
            result = super().generate_report()
            if result:
                config.loggers.cli.info(f'Report successfully generated at: {result}')
            return result
        except Exception as e:
            config.loggers.cli.error(f'Error in parent generate_report method: {e}')
            return None


def generate_reports(
    subject_list,
    output_dir,
    run_uuid,
    session_list=None,
    bootstrap_file=None,
    work_dir=None,
    boilerplate_only=False,
    layout: BIDSLayout = None,
):
    """Generate reports for a list of subjects using nireports."""
    report_errors = []

    output_dir_path = Path(output_dir).absolute()

    if not layout:
        config.loggers.cli.error('BIDSLayout is required for report generation.')
        return 1

    # Ensure the provided layout has invalid_filters='allow'
    layout_needs_recreation = False
    if hasattr(layout, 'config') and isinstance(layout.config, dict):
        if layout.config.get('invalid_filters') != 'allow':
            layout_needs_recreation = True
    else:
        layout_needs_recreation = True

    if layout_needs_recreation:
        try:
            original_derivatives = layout.derivatives if hasattr(layout, 'derivatives') else None
            original_root = layout.root if hasattr(layout, 'root') else None

            if original_root is None:
                config.loggers.cli.error('Original layout root is None, cannot re-create layout.')
                return 1

            new_layout_derivatives = {}
            if isinstance(original_derivatives, dict):
                new_layout_derivatives = {
                    k: str(v.path) for k, v in original_derivatives.items()
                    if hasattr(v, 'path')
                }
            elif isinstance(original_derivatives, list):
                new_layout_derivatives = [str(p) for p in original_derivatives]
            elif isinstance(original_derivatives, str | Path):
                new_layout_derivatives = str(original_derivatives)
            else:
                new_layout_derivatives = str(Path(output_dir).absolute())

            # Add subject figures directory to derivatives if it exists
            for subject_label_with_prefix in subject_list:
                subject_id = subject_label_with_prefix.lstrip('sub-')
                subject_figures_dir = (
                    Path(output_dir).absolute() / f'sub-{subject_id}' / 'figures'
                )
                if subject_figures_dir.exists():
                    if isinstance(new_layout_derivatives, dict):
                        new_layout_derivatives[f'sub-{subject_id}'] = \
                            str(subject_figures_dir.parent)
                    elif isinstance(new_layout_derivatives, list):
                        new_layout_derivatives.append(str(subject_figures_dir.parent))
                    else:
                        new_layout_derivatives = \
                            [new_layout_derivatives, str(subject_figures_dir.parent)]

            # If new_layout_derivatives is empty or still a string referring to out_dir
            is_empty_or_self_ref = (
                (isinstance(new_layout_derivatives, dict) and not new_layout_derivatives) or
                (isinstance(new_layout_derivatives, str) and
                 str(Path(output_dir).absolute()) in new_layout_derivatives)
            )

            if is_empty_or_self_ref:
                subject_dirs = []
                for subject_label_with_prefix in subject_list:
                    subject_id = subject_label_with_prefix.lstrip('sub-')
                    subject_dir = Path(output_dir).absolute() / f'sub-{subject_id}'
                    if subject_dir.exists():
                        subject_dirs.append(str(subject_dir))

                if subject_dirs:
                    if isinstance(new_layout_derivatives, dict):
                        for i, dir_path in enumerate(subject_dirs):
                            new_layout_derivatives[f'subdir_{i}'] = dir_path
                    elif isinstance(new_layout_derivatives, list):
                        new_layout_derivatives.extend(subject_dirs)
                    else:
                        new_layout_derivatives = [new_layout_derivatives] + subject_dirs

            layout = BIDSLayout(
                root=str(original_root),
                derivatives=new_layout_derivatives,
                invalid_filters='allow',
                validate=False,
                indexer=BIDSLayoutIndexer(validate=False, index_metadata=False)
            )
        except Exception as e:
            config.loggers.cli.error(f'Failed to re-create BIDSLayout: {e}')

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

        n_ses = len(layout.get_sessions(subject=subject_id_for_report))
        aggr_ses_reports_threshold = getattr(config.execution, 'aggr_ses_reports', 3)

        current_bootstrap_file = bootstrap_file
        if current_bootstrap_file is None:
            current_bootstrap_file = data.load('reports-spec.yml')

        if n_ses <= aggr_ses_reports_threshold:
            html_report_filename = f'sub-{subject_id_for_report}.html'
        else:
            html_report_filename = f'sub-{subject_id_for_report}.html' 

        try:
            final_html_path = output_dir_path / html_report_filename
            config.loggers.cli.info(f'Generating report for {subject_label_with_prefix}...')
            config.loggers.cli.info(f'HTML will be: {final_html_path}')
            config.loggers.cli.info(
                f'Reportlets dir for nireports: {reportlets_dir_for_nireports}'
            )

            try:
                # List reportlets to verify they exist
                svg_reportlets = list(reportlets_dir_for_nireports.glob('*.svg'))
                html_reportlets = list(reportlets_dir_for_nireports.glob('*.html'))
                if not svg_reportlets and not html_reportlets:
                    config.loggers.cli.error(
                        f'No reportlets found in {reportlets_dir_for_nireports}'
                    )
                    raise FileNotFoundError(
                        f'No reportlets found in {reportlets_dir_for_nireports}'
                    )

                config.loggers.cli.info(
                    f'Found {len(svg_reportlets)} SVG reportlets and '
                    f'{len(html_reportlets)} HTML reportlets in {reportlets_dir_for_nireports}'
                )

                # Log reportlet filenames
                if svg_reportlets:
                    config.loggers.cli.info('SVG reportlets:')
                    for r in svg_reportlets:
                        config.loggers.cli.info(f'  - {r.name}')
                if html_reportlets:
                    config.loggers.cli.info('HTML reportlets:')
                    for r in html_reportlets:
                        config.loggers.cli.info(f'  - {r.name}')

                # Create the report object with absolute paths
                robj = SafeReport(
                    out_dir=str(output_dir_path.absolute()),
                    run_uuid=run_uuid,
                    bootstrap_file=current_bootstrap_file,
                    reportlets_dir=str(reportlets_dir_for_nireports.absolute()),
                    plugins=None,
                    out_filename=html_report_filename,
                    subject=subject_id_for_report,
                    session=None,
                    layout=layout,
                )

                # Generate the report
                robj.generate_report()

                # Verify the report was generated
                if not final_html_path.exists():
                    config.loggers.cli.error(f'Report file not found: {final_html_path}')
                    raise FileNotFoundError(f'Report file not found: {final_html_path}')

                config.loggers.cli.info(f'Successfully generated report at {final_html_path}')
            except Exception as e:
                config.loggers.cli.error(f'Report generation failed: {e}', exc_info=True)
                report_errors.append(subject_label_with_prefix)
        except Exception as e:
            config.loggers.cli.error(f'Report generation failed: {e}', exc_info=True)
            report_errors.append(subject_label_with_prefix)

        if n_ses > aggr_ses_reports_threshold:
            active_session_list = session_list
            if active_session_list is None:
                all_filters = config.execution.bids_filters or {}
                filters = all_filters.get('t1w', {})
                active_session_list = layout.get_sessions(
                    subject=subject_id_for_report, **filters
                )
            active_session_list = [
                ses[4:] if ses.startswith('ses-') else ses for ses in active_session_list
            ]

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

                    srobj = SafeReport(
                        out_dir=str(output_dir_path),
                        run_uuid=run_uuid,
                        bootstrap_file=session_bootstrap_file,
                        reportlets_dir=str(reportlets_dir_for_nireports),
                        plugins=None,
                        out_filename=session_html_report_filename,
                        subject=subject_id_for_report,
                        session=session_label,
                        layout=layout,
                    )
                    srobj.generate_report()
                    config.loggers.cli.info(
                        f'Successfully generated session report for {subject_label_with_prefix} '
                        f'session {session_label} at {final_session_html_path}'
                    )
                except Exception as e:
                    config.loggers.cli.error(
                        f'Session report generation failed for {subject_label_with_prefix} '
                        f'session {session_label}: {e}'
                    )
                    report_errors.append(f'{subject_label_with_prefix}_ses-{session_label}')

    if report_errors:
        config.loggers.cli.error(
            f'Report generation failed for: {", ".join(report_errors)}'
        )
        return 1
    return 0
