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
import os
import time
from pathlib import Path

import jinja2
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
        from pathlib import Path

        reportlet_path = Path(reportlet_path)

        if not reportlet_path.exists():
            config.loggers.cli.error(f'Reportlet not found: {reportlet_path}')
            return None

        if reportlet_path.suffix == '.html':
            try:
                return reportlet_path.read_text()
            except (OSError, UnicodeDecodeError) as e:
                config.loggers.cli.error(f'Error reading HTML reportlet: {e}')
                return None
        elif reportlet_path.suffix == '.svg':
            try:
                # We should NOT create a new figures directory in the output dir
                # The SVG files should already be in the subject's figures directory
                # Just return the relative path to the SVG file
                return str(reportlet_path.name)
            except (OSError, UnicodeDecodeError) as e:
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
                                    reportlets.append(str(match))

        self._manual_reportlets = reportlets

        try:
            super().index(settings)
        except (OSError, ValueError, RuntimeError) as e:
            config.loggers.cli.error(f'Error in parent index method: {e}')

        if not hasattr(self, 'reportlets') or not self.reportlets:
            if self._manual_reportlets:
                self.reportlets = self._manual_reportlets

        return self.reportlets

    def generate_report(self):
        """Generate a custom HTML report using the reportlets."""
        from pathlib import Path

        if not hasattr(self, 'reportlets') or not self.reportlets:
            if hasattr(self, '_manual_reportlets') and self._manual_reportlets:
                self.reportlets = self._manual_reportlets

        if not hasattr(self, 'reportlets') or not self.reportlets:
            config.loggers.cli.error('No reportlets available for report generation')
            return None

        # Determine output directory and subject ID
        output_dir = None
        for attr in ['output_dir', 'out_dir']:
            if hasattr(self, attr):
                output_dir = Path(getattr(self, attr))
                break

        if not output_dir:
            if hasattr(self, 'out_filename'):
                # Use parent dir of out_filename if available
                output_file = self.out_filename
                output_dir = Path(os.path.dirname(output_file))
            else:
                # Fallback to current working directory
                output_dir = Path(os.getcwd())

        # Get subject ID from the reportlets directory path if not set
        subject_id = 'Unknown'
        if hasattr(self, 'subject'):
            subject_id = self.subject
        elif hasattr(self, '_reportlets_dir'):
            # Try to extract subject ID from reportlets directory path
            reportlets_path = Path(self._reportlets_dir)
            if 'sub-' in str(reportlets_path):
                subject_id = reportlets_path.parent.name.replace('sub-', '')

        # Prepare the HTML template
        template_str = r"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="generator" content="NiReports: https://www.nipreps.org/" />
    <title>NCDLMUSE: sub-{{subject_id}}</title>
    <link rel="stylesheet"
          href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css"
          integrity="sha384-JcKb8q3iqJ61gNV9KGb8thSsNjpSL0n8PARn9HuZOnIxN0hoP+VmmDGMN5t9UJ0Z"
          crossorigin="anonymous">
    <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"
            integrity="sha384-DfXdz2htPH0lsSSs5nCTpuj/zy4C+OGpamoFVy38MVBnE+IbbVYUew+OrCXaRkfj"
            crossorigin="anonymous"></script>
    <script src="https://cdn.jsdelivr.net/npm/popper.js@1.16.1/dist/umd/popper.min.js"
            integrity="sha384-9/reFTGAW83EW2RDu2S0VKaIzap3H66lZH81PoYlFhbGU+6BZp6G7niu735Sk7lN"
            crossorigin="anonymous"></script>
    <script src="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/js/bootstrap.min.js"
            integrity="sha384-B4gt1jrGC7Jh4AgTPSdUtOBvfO8shuf57BaghqFfPlYxofvL8/KUEfYiJOMMV+rV"
            crossorigin="anonymous"></script>
    <style type="text/css">
    @import url(https://fonts.googleapis.com/css?family=Lato:400,700,400italic,700italic);
    body {
        font-family: 'Lato', 'Helvetica Neue', Helvetica, Arial, sans-serif;
        background-color: #fff;
    }
    a {
        color: #258AAF;
    }
    .navbar {
        border-bottom: 1px solid #ddd;
        margin-bottom: 20px;
    }
    .navbar-brand {
        margin-right: 20px;
        margin-left: 10px;
    }
    .nipreps-brand {
        color: #3A3A3B;
        font-weight: 400;
    }
    .nipreps-version {
        color: #999999;
        font-size: small;
    }
    .ncdlmuse-brand {
        color: #258AAF;
        font-weight: 500;
    }
    .ncdlmuse-version {
        color: #bbbbbb;
        font-size: small;
    }
    .navbar-toggle {
        margin-top: 15px;
        margin-bottom: 15px;
    }
    h1.section-heading {
        padding-top: 20px;
        margin-top: 20px;
        margin-bottom: 20px;
        color: #258AAF;
    }
    h2.sub-heading {
        padding-top: 10px;
        margin-top: 10px;
        color: #5C9EBA;
    }
    .reportlet-title {
        font-weight: 500;
        color: #333;
        margin-top: 25px;
    }
    .reportlet-description {
        font-style: italic;
        color: #666;
        margin-bottom: 10px;
    }
    .reportlet-container {
        margin-bottom: 30px;
    }
    .reportlet img {
        max-width: 100%;
        margin: 10px 0;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        border-radius: 4px;
    }
    .boiler-html h1 {
        font-size: 1.6em;
        margin-bottom: 16px;
    }
    .boiler-html h2 {
        font-size: 1.4em;
        margin-bottom: 12px;
    }
    .boiler-html h3 {
        font-size: 1.2em;
        margin-bottom: 8px;
    }
    .boiler-html p {
        margin-bottom: 14px;
    }
    footer {
        margin-top: 30px;
        border-top: 1px solid #ddd;
        padding-top: 10px;
        font-size: small;
        color: #999999;
    }
    </style>
</head>
<body>
    <!-- Navigation Bar -->
    <nav class="navbar navbar-expand-lg navbar-light bg-light fixed-top">
        <div class="container">
            <a class="navbar-brand" href="#">
                <span class="ncdlmuse-brand">NCDLMUSE</span>
                <span class="ncdlmuse-version">{{version}}</span>
            </a>
            <button class="navbar-toggler" type="button"
                    data-toggle="collapse"
                    data-target="#navbarSupportedContent"
                    aria-controls="navbarSupportedContent"
                    aria-expanded="false"
                    aria-label="Toggle navigation">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarSupportedContent">
                <ul class="navbar-nav ml-auto">
                    {% for section in sections %}
                    <li class="nav-item">
                        <a class="nav-link" href="#{{section.id}}">{{section.name}}</a>
                    </li>
                    {% endfor %}
                </ul>
            </div>
        </div>
    </nav>

    <div class="container" style="padding-top: 100px;">
        <h1>NCDLMUSE Report: {{subject_id}}</h1>

        <noscript>
            <div class="alert alert-danger">
                The navigation menu at the top uses JavaScript.
                Without it this report might not work as expected.
            </div>
        </noscript>

        {% for section in sections %}
        <section id="{{section.id}}">
            <h1 class="section-heading">{{section.name}}</h1>
            {% for reportlet in section.reportlets %}
                <div class="reportlet-container">
                    {% if reportlet.title %}
                    <h2 class="reportlet-title">{{ reportlet.title }}</h2>
                    {% endif %}
                    {% if reportlet.description %}
                    <p class="reportlet-description">{{ reportlet.description }}</p>
                    {% endif %}
                    <div class="reportlet">
                        {{ reportlet.content | safe }}
                    </div>
                </div>
            {% endfor %}
        </section>
        {% endfor %}

        <footer>
            <div class="row">
                <div class="col-md-12">
                    <p>Report generated by BIDS_NiChart_DLMUSE
                       v{{ version }} on {{ timestamp }}.</p>
                </div>
            </div>
        </footer>
    </div>

    <script type="text/javascript">
    $(function() {
        $('a[href*="#"]:not([href="#"])').click(function() {
            if (location.pathname.replace(/^\//,'') == this.pathname.replace(/^\//,'') &&
                location.hostname == this.hostname) {
                var target = $(this.hash);
                target = target.length ? target : $('[name=' + this.hash.slice(1) +']');
                if (target.length) {
                    $('html, body').animate({
                        scrollTop: target.offset().top - 100
                    }, 500);
                    return false;
                }
            }
        });
    });
    </script>
</body>
</html>
"""

        # Get version from config
        version = getattr(config.environment, 'version', 'unknown')
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        # Process reportlets and organize by section
        sections = []

        # Add Summary section
        summary_section = {'id': 'summary', 'name': 'Summary', 'reportlets': []}

        # Add Anatomical section
        anatomical_section = {
            'id': 'anatomical',
            'name': 'Anatomical Processing',
            'reportlets': [],
        }

        # Add Processing Details section
        processing_section = {'id': 'processing', 'name': 'Processing Details', 'reportlets': []}

        # Add About section
        about_section = {'id': 'about', 'name': 'About', 'reportlets': []}

        # Process each reportlet and add to appropriate section
        for reportlet in self.reportlets:
            reportlet_path = Path(reportlet)
            if reportlet_path.suffix.lower() == '.svg':
                # Extract subject ID from the SVG filename if it contains one
                svg_subject_id = subject_id
                if 'sub-' in reportlet_path.name:
                    svg_subject_id = reportlet_path.name.split('_')[0].replace('sub-', '')

                content = (
                    f'<img src="sub-{svg_subject_id}/figures/{reportlet_path.name}" '
                    f'alt="{reportlet_path.stem}" '
                    f'class="img-fluid">'
                )

                # Determine which section based on filename
                if 'brain' in reportlet_path.name.lower() or 'mask' in reportlet_path.name.lower():
                    anatomical_section['reportlets'].append(
                        {
                            'title': 'DLICV Brain Mask',
                            'description': 'DLICV Brain mask overlaid on the T1w image.',
                            'content': content,
                        }
                    )
                elif (
                    'segmentation' in reportlet_path.name.lower()
                    or 'dlmuse' in reportlet_path.name.lower()
                ):
                    anatomical_section['reportlets'].append(
                        {
                            'title': 'DLMUSE Segmentation',
                            'description': 'DLMUSE segmentation overlaid on the T1w image.',
                            'content': content,
                        }
                    )
                else:
                    anatomical_section['reportlets'].append(
                        {'title': reportlet_path.stem, 'content': content}
                    )

            elif reportlet_path.suffix.lower() == '.html':
                try:
                    content = reportlet_path.read_text()

                    # Determine section based on filename
                    if 'summary' in reportlet_path.name.lower():
                        summary_section['reportlets'].append(
                            {'title': 'Processing Summary', 'content': content}
                        )
                    elif 'workflowprovenance' in reportlet_path.name.lower():
                        processing_section['reportlets'].append({'content': content})
                    elif 'processingerrors' in reportlet_path.name.lower():
                        processing_section['reportlets'].append({'content': content})
                    elif 'about' in reportlet_path.name.lower():
                        about_section['reportlets'].append(
                            {'title': 'About this Run', 'content': content}
                        )
                    else:
                        processing_section['reportlets'].append(
                            {'title': reportlet_path.stem, 'content': content}
                        )
                except (OSError, UnicodeDecodeError) as e:
                    config.loggers.cli.warning(
                        f'Error reading HTML reportlet {reportlet_path}: {e}'
                    )

        # Add non-empty sections to the list
        if summary_section['reportlets']:
            sections.append(summary_section)
        if anatomical_section['reportlets']:
            sections.append(anatomical_section)
        if processing_section['reportlets']:
            sections.append(processing_section)
        if about_section['reportlets']:
            sections.append(about_section)

        # Render the template
        try:
            template = jinja2.Template(template_str)
            # Format subject_id with sub- prefix if it's not already there
            formatted_subject_id = (
                f'sub-{subject_id}' if not subject_id.startswith('sub-') else subject_id
            )
            html_content = template.render(
                subject_id=formatted_subject_id,
                sections=sections,
                version=version,
                timestamp=timestamp,
            )

            # Write the HTML file
            output_file = output_dir / self.out_filename
            with open(output_file, 'w') as f:
                f.write(html_content)

            return str(output_file)
        except (OSError, jinja2.TemplateError, UnicodeEncodeError) as e:
            config.loggers.cli.error(f'Error generating HTML report: {e}')
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
                    k: str(v.path) for k, v in original_derivatives.items() if hasattr(v, 'path')
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
                subject_figures_dir = Path(output_dir).absolute() / f'sub-{subject_id}' / 'figures'
                if subject_figures_dir.exists():
                    if isinstance(new_layout_derivatives, dict):
                        new_layout_derivatives[f'sub-{subject_id}'] = str(
                            subject_figures_dir.parent
                        )
                    elif isinstance(new_layout_derivatives, list):
                        new_layout_derivatives.append(str(subject_figures_dir.parent))
                    else:
                        new_layout_derivatives = [
                            new_layout_derivatives,
                            str(subject_figures_dir.parent),
                        ]

            # If new_layout_derivatives is empty or still a string referring to out_dir
            is_empty_or_self_ref = (
                isinstance(new_layout_derivatives, dict) and not new_layout_derivatives
            ) or (
                isinstance(new_layout_derivatives, str)
                and str(Path(output_dir).absolute()) in new_layout_derivatives
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
                validate=False,
                indexer=BIDSLayoutIndexer(
                    validate=False, index_metadata=False, invalid_filters='allow'
                ),
            )
        except (OSError, ValueError, RuntimeError) as e:
            config.loggers.cli.error(f'Failed to re-create BIDSLayout: {e}')
            return 1

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
            except (OSError, jinja2.TemplateError, UnicodeEncodeError) as e:
                config.loggers.cli.error(f'Report generation failed: {e}', exc_info=True)
                report_errors.append(subject_label_with_prefix)
        except (OSError, jinja2.TemplateError, UnicodeEncodeError) as e:
            config.loggers.cli.error(f'Report generation failed: {e}', exc_info=True)
            report_errors.append(subject_label_with_prefix)

        if n_ses > aggr_ses_reports_threshold:
            active_session_list = session_list
            if active_session_list is None:
                all_filters = config.execution.bids_filters or {}
                filters = all_filters.get('t1w', {})
                active_session_list = layout.get_sessions(subject=subject_id_for_report, **filters)
            active_session_list = [
                ses[4:] if ses.startswith('ses-') else ses for ses in active_session_list
            ]

            for session_label in active_session_list:
                session_bootstrap_file = bootstrap_file
                if session_bootstrap_file is None:
                    session_bootstrap_file = data.load('reports-spec.yml')

                session_html_report_filename = (
                    f'sub-{subject_id_for_report}_ses-{session_label}.html'
                )
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
                except (OSError, jinja2.TemplateError, UnicodeEncodeError) as e:
                    config.loggers.cli.error(
                        f'Session report generation failed for {subject_label_with_prefix} '
                        f'session {session_label}: {e}'
                    )
                    report_errors.append(f'{subject_label_with_prefix}_ses-{session_label}')

    if report_errors:
        config.loggers.cli.error(f'Report generation failed for: {", ".join(report_errors)}')
        return 1
    return 0
