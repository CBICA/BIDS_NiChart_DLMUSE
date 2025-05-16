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
import json

from nireports.assembler.report import Report
from bids.layout import BIDSLayout

from ncdlmuse import config, data
from ncdlmuse.utils import bids as bids_utils


def generate_reports(
    subject_list: list[str] | str,
    output_dir: str | Path, # Main output directory (e.g., <bids_root>/derivatives/)
    run_uuid: str,
    session_list: list[str] | None = None,    # List of session labels (WITHOUT 'ses-' prefix)
    bootstrap_file: str | Path | None = None, # Path to reports-spec.yml
    work_dir: str | Path | None = None,       # Not actively used by ncdlmuse reports currently
    boilerplate_only: bool = False,
    layout: BIDSLayout | None = None, # The BIDSLayout object (already includes derivatives)
) -> int:
    """
    Generate reports for a list of subjects using nireports.
    """
    report_errors: list[str] = []

    if not layout:
        config.loggers.cli.error(
            'BIDSLayout (already including derivatives) is required for report generation.'
        )
        return 1

    # This is the NCDLMUSE derivatives directory, e.g. <output_dir>/ncdlmuse
    # It's where subject-level reports are saved and the base for finding reportlet SVGs.
    ncdlmuse_derivatives_dir = Path(output_dir).absolute()
    
    # For nireports, reportlets_dir is the base from which <subject_label>/figures/... is resolved
    reportlets_dir_for_nireports = ncdlmuse_derivatives_dir

    if isinstance(subject_list, str):
        subject_list = [subject_list]

    for subject_label_with_prefix in subject_list: # e.g., "sub-01"
        # Ensure subject_label_with_prefix starts with 'sub-'
        if not subject_label_with_prefix.startswith('sub-'):
            subject_label_with_prefix = f'sub-{subject_label_with_prefix}'
            config.loggers.cli.info(f"Added 'sub-' prefix: {subject_label_with_prefix}")
            
        subject_id_for_report = subject_label_with_prefix.lstrip('sub-') # "01"

        # Determine the bootstrap file to use
        current_bootstrap_file_path: Path | None = None
        if bootstrap_file is not None:
            current_bootstrap_file_path = Path(bootstrap_file)
        else:
            current_bootstrap_file_path = data.load('reports-spec.yml')

        if not current_bootstrap_file_path or not current_bootstrap_file_path.exists():
            config.loggers.cli.error(
                f'Bootstrap file not found: {current_bootstrap_file_path}. '
                f'Skipping report for {subject_label_with_prefix}.'
            )
            report_errors.append(subject_label_with_prefix)
            continue
            
        # Define the main HTML report filename for this subject
        # Reports are saved in the ncdlmuse_derivatives, e.g. <ncdlmuse_derivatives>/sub-01.html
        # Make sure to keep the "sub-" prefix in the filename
        out_html_filename = f'{subject_label_with_prefix}.html'
        final_html_path = ncdlmuse_derivatives_dir / out_html_filename

        if boilerplate_only:
            # Generate boilerplate for NCDLMUSE
            # Using ncdlmuse_derivatives_dir as the save location for boilerplate
            boilerplate_path = ncdlmuse_derivatives_dir / \
                f'{subject_label_with_prefix}_CITATION.md'
            config.loggers.cli.info(
                f'Generating boilerplate for {subject_label_with_prefix} at {boilerplate_path}'
            )
            # Basic boilerplate content, can be expanded
            boilerplate_content = (
                f'# Boilerplate for {subject_label_with_prefix}\n\n'
                f'NCDLMUSE Version: {config.environment.version}\n'
                # Add more details as needed, e.g., from config.execution or citations
            )
            try:
                boilerplate_path.parent.mkdir(parents=True, exist_ok=True)
                boilerplate_path.write_text(boilerplate_content)
            except Exception as e:
                config.loggers.cli.error(
                    f'Failed to write boilerplate for {subject_label_with_prefix}: {e}'
                )
                report_errors.append(subject_label_with_prefix)
            continue

        config.loggers.cli.info(f'Generating report for {subject_label_with_prefix}...')
        config.loggers.cli.info(f'  Output HTML: {final_html_path}')
        config.loggers.cli.info(f'  Reportlets base: {reportlets_dir_for_nireports}')
        config.loggers.cli.info(f'  Bootstrap specification: {current_bootstrap_file_path}')

        try:
            # Ensure dataset_description.json for subject figures dir
            # Figures are expected at <ncdlmuse_derivatives>/<subject_label_with_prefix>/figures/
            subject_figures_dir = ncdlmuse_derivatives_dir / subject_label_with_prefix / 'figures'
            if subject_figures_dir.is_dir(): # Only create if figures dir itself exists
                figures_ds_desc_path = subject_figures_dir / 'dataset_description.json'
                if not figures_ds_desc_path.exists():
                    ds_desc_content = {
                        'Name': f'{subject_label_with_prefix} NCDLMUSE Reportlets',
                        'BIDSVersion': '1.10.0',  # Using the standard BIDS version from utils.bids
                        'DatasetType': 'derivative',
                        'GeneratedBy': [{
                            'Name': 'ncdlmuse',
                            'Version': config.environment.version
                        }]
                    }
                    figures_ds_desc_path.parent.mkdir(parents=True, exist_ok=True)
                    with figures_ds_desc_path.open('w') as f:
                        json.dump(ds_desc_content, f, indent=2)
                        
                # Log the figures that are available
                config.loggers.cli.info(f"Figures directory: {subject_figures_dir}")
                if subject_figures_dir.exists():
                    figure_files = list(subject_figures_dir.glob("**/*.svg"))
                    config.loggers.cli.info(f"Found {len(figure_files)} figure files")
                    for fig in figure_files[:5]:  # Log first 5 figures to help with debugging
                        config.loggers.cli.info(f"Figure: {fig.relative_to(subject_figures_dir)}")
            
            # Prepare NAMED configuration arguments for nireports.Report
            report_named_config_args = {
                'out_filename': out_html_filename, # Name of html file, saved in out_dir
                'reportlets_dir': str(reportlets_dir_for_nireports),
                'bootstrap_file': current_bootstrap_file_path,
            }

            # Prepare BIDS ENTITY filters and other settings for nireports.Report
            bids_entity_filters_and_settings = {
                'subject': subject_id_for_report,
                # output_dir is for {output_dir} replacement in reports-spec.yml.
                # It refers to the ncdlmuse derivatives directory.
                'output_dir': str(ncdlmuse_derivatives_dir),
                'invalid_filters': 'allow', 
            }
            
            # Handle session if a single session is specified
            processed_session_list = \
                [s.lstrip('ses-') for s in session_list] if session_list else []
            if len(processed_session_list) == 1:
                session_id_for_entity = processed_session_list[0]
                # Validate session existence for the subject
                if layout.get_sessions(
                    subject=subject_id_for_report, 
                    session=session_id_for_entity
                    ):
                    bids_entity_filters_and_settings['session'] = session_id_for_entity
                    config.loggers.cli.info(
                        f'  Processing report for session: {session_id_for_entity}'
                    )
                else:
                    config.loggers.cli.warning(
                        f"Specified session '{session_id_for_entity}' not found for subject "
                        f"'{subject_id_for_report}'. Generating subject-level report."
                    )
            elif len(processed_session_list) > 1:
                config.loggers.cli.warning(
                    f'Multiple sessions provided ({processed_session_list}) for subject '
                    f"'{subject_id_for_report}'. Generating subject-level report. "
                    f'Session-specific information depends on spec file queries if any.'
                )

            try:      
                # Create a subclass of Report that handles the BIDS layout properly
                class SafeReport(Report):
                    def __init__(self, out_dir, subject_id, **kwargs):
                        # Store layout separately, not letting it pass to SQL queries
                        self._safe_layout = kwargs.pop('layout', None)
                        super().__init__(out_dir, subject_id, **kwargs)
                    
                    # Override any methods that might use layout in SQLAlchemy queries
                    def index(self, settings=None):
                        # Make layout available during index operation
                        if hasattr(self, '_safe_layout') and self._safe_layout is not None:
                            self.layout = self._safe_layout
                        return super().index(settings)
                    
                    # Override to ensure segmentation files are properly found
                    def _load_json_metadata(self, filepath):
                        metadata = super()._load_json_metadata(filepath)
                        return metadata
                    
                    # Override to directly add figures to the report
                    def _register_figures(self, svg_files, html_files):
                        """Ensure all figures are registered and available for the report."""
                        from nireports.assembler.elements import SVGFigure
                        
                        # Initialize figures section if needed
                        if not hasattr(self, 'sections'):
                            self.sections = []
                        
                        # Create a figures section to hold all SVGs
                        figures_section = {
                            'name': 'NCDLMUSE Figures',
                            'reportlets': []
                        }
                        
                        # Register the figures directly
                        for svg_file in svg_files:
                            # Extract subject, task, desc, etc. from filename 
                            filename = svg_file.name
                            components = filename.split('_')
                            
                            # Create a simple ID for the SVG
                            svg_id = filename.replace('.svg', '').replace('-', '_').replace('.', '_')
                            
                            # Extract description from filename if available
                            desc = next((comp.replace('desc-', '') for comp in components if comp.startswith('desc-')), None)
                            if not desc:
                                desc = "Visualization"
                            
                            # Create a title
                            title = f"Figure: {desc}"
                            
                            # Load SVG content directly
                            try:
                                with open(svg_file, 'r') as f:
                                    svg_content = f.read()
                                
                                # Create a reportlet dictionary
                                reportlet = {
                                    'name': title,
                                    'file_id': svg_id,
                                    'description': f"NCDLMUSE {desc}",
                                    'raw_content': svg_content
                                }
                                
                                # Add to the figures section
                                figures_section['reportlets'].append(reportlet)
                                config.loggers.cli.info(f"Added SVG to figures section: {title}")
                            except Exception as e:
                                config.loggers.cli.error(f"Error loading SVG {svg_file}: {e}")
                        
                        # Add the figures section to the report
                        if figures_section['reportlets']:
                            self.sections.append(figures_section)
                            config.loggers.cli.info(f"Added figures section with {len(figures_section['reportlets'])} reportlets")
                    
                    def generate_report(self):
                        """Generate the report with figures properly included."""
                        config.loggers.cli.info("Starting custom report generation with figure embedding")
                        
                        # First, find all SVG files in the figures directory
                        subject_figures_dir = Path(self.out_dir) / f"sub-{self.subject_id}" / "figures"
                        if subject_figures_dir.exists():
                            svg_files = list(subject_figures_dir.glob("**/*.svg"))
                            html_files = list(subject_figures_dir.glob("**/*.html"))
                            config.loggers.cli.info(f"Found {len(svg_files)} SVG files and {len(html_files)} HTML files to include")
                            
                            # Process and register these files
                            self._register_figures(svg_files, html_files)
                        
                        # Custom HTML generation that ensures figures are embedded
                        try:
                            # Run the standard report generation process
                            super().generate_report()
                            
                            # After generation, check if the HTML has figures content
                            html_file = Path(self.out_dir) / f"{self.out_filename}"
                            if html_file.exists():
                                with open(html_file, 'r') as f:
                                    html_content = f.read()
                                
                                # If HTML seems empty, directly embed SVGs
                                if 'nireports-figure' not in html_content.lower() and svg_files:
                                    config.loggers.cli.info("HTML file missing figures, adding them directly")
                                    
                                    # Create simple HTML with embedded SVGs
                                    html_content_parts = html_content.split('</body>')
                                    svg_html = '<div class="container"><h2>NCDLMUSE Figures</h2>'
                                    
                                    for svg_file in svg_files:
                                        try:
                                            with open(svg_file, 'r') as f:
                                                svg_content = f.read()
                                            
                                            # Extract description from filename
                                            desc = next((comp.replace('desc-', '') for comp in svg_file.name.split('_') 
                                                        if comp.startswith('desc-')), "Visualization")
                                            
                                            svg_html += f'<div class="card mb-3"><div class="card-header">{desc}</div>'
                                            svg_html += f'<div class="card-body">{svg_content}</div></div>'
                                            
                                        except Exception as e:
                                            config.loggers.cli.error(f"Error embedding SVG {svg_file}: {e}")
                                    
                                    svg_html += '</div>'
                                    
                                    # Insert before the closing body tag
                                    if len(html_content_parts) > 1:
                                        new_html = html_content_parts[0] + svg_html + '</body>' + html_content_parts[1]
                                    else:
                                        # If no closing body tag found, add content to the end with proper tags
                                        new_html = html_content.rstrip() + svg_html + '</body></html>'
                                    
                                    # Write the updated HTML
                                    with open(html_file, 'w') as f:
                                        f.write(new_html)
                                    
                                    config.loggers.cli.info(f"Successfully embedded {len(svg_files)} SVGs directly into HTML")
                        
                        except Exception as e:
                            config.loggers.cli.error(f"Error in custom report generation: {e}", exc_info=True)
                            # Try standard report generation as a fallback
                            super().generate_report()
                
                # Now create the safe report instance with layout
                report_named_config_args['layout'] = layout  # Put layout back in args
                
                # Log important information for debugging
                config.loggers.cli.info(f"Subject ID for report: {subject_id_for_report}")
                config.loggers.cli.info(f"Output HTML filename: {out_html_filename}")
                
                robj = SafeReport(
                    str(ncdlmuse_derivatives_dir),  # Directory to save the main HTML report
                    run_uuid,
                    **report_named_config_args,
                    **bids_entity_filters_and_settings
                )
                
                config.loggers.cli.info("Report object created with custom SafeReport class")
                
                # Log more detailed info about the report object
                config.loggers.cli.info(f"Report will be saved to: {final_html_path}")
                if hasattr(robj, '_safe_layout') and robj._safe_layout is not None:
                    config.loggers.cli.info("Layout is available in the report object")
                
                robj.generate_report()
                config.loggers.cli.info(
                    f"Successfully generated report for {subject_label_with_prefix} at "
                    f"{final_html_path}"
                )
            except Exception as e:
                # Handle Report-specific exceptions
                err_msg = f"Report generation failed for {subject_label_with_prefix}: {e}"
                config.loggers.cli.error(err_msg, exc_info=True) # Log full traceback
                report_errors.append(subject_label_with_prefix)

        except Exception as e:
            err_msg = f"Report generation failed for {subject_label_with_prefix}: {e}"
            config.loggers.cli.error(err_msg, exc_info=True) # Log full traceback
            report_errors.append(subject_label_with_prefix)

    if report_errors:
        joined_error_subjects = ', '.join(report_errors)
        config.loggers.cli.error(
            f"Report generation failed for the following subjects: {joined_error_subjects}"
        )
        return 1

    config.loggers.cli.info("All reports generated successfully.")
    return 0
