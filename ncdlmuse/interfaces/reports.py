# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""Interfaces to generate reportlets."""

import os

from nipype.interfaces.base import (
    BaseInterfaceInputSpec,
    File,
    SimpleInterface,
    Str,
    TraitedSpec,
    isdefined,
    traits,
)


class SummaryInterface(SimpleInterface):
    """Base class for summary interfaces."""

    def _run_interface(self, runtime):
        segment = self._generate_segment()
        fname = os.path.join(runtime.cwd, 'report.html')
        with open(fname, 'w') as fobj:
            fobj.write(segment)
        self._results['out_report'] = fname
        return runtime

    def _generate_segment(self):
        """Generate the summary segment."""
        raise NotImplementedError


SUBJECT_TEMPLATE = """\
\t<ul class="elem-desc">
\t\t<li>Subject ID: {subject_id}</li>
\t\t<li>Session ID: {session_id}</li>
\t\t<li>Input T1w images: {n_t1s:d}</li>
\t\t<li>Brain mask: {brain_mask_status}</li>
\t\t<li>DLMUSE segmentation: {seg_status}</li>
\t</ul>
"""

# ABOUT_TEMPLATE for original NCDLMUSE AboutSummary is now removed.


class _SummaryOutputSpec(TraitedSpec):
    out_report = File(exists=True, desc='HTML segment containing summary')


class _SubjectSummaryInputSpec(BaseInterfaceInputSpec):
    t1w = traits.List(File(exists=True), desc='T1w structural images')
    subject_id = traits.Str(desc='Subject ID')
    session_id = traits.Str(desc='Session ID', mandatory=False)
    brain_mask_file = File(exists=True, desc='Brain mask file', mandatory=False)
    dlmuse_seg_file = File(exists=True, desc='DLMUSE segmentation file', mandatory=False)


class _SubjectSummaryOutputSpec(_SummaryOutputSpec):
    # This exists to ensure that the summary is run prior to the first ReconAll
    # call, allowing a determination whether there is a pre-existing directory
    subject_id = Str(desc='Subject ID')


class SubjectSummary(SummaryInterface):
    """A summary describing the subject's data as a whole."""

    input_spec = _SubjectSummaryInputSpec
    output_spec = _SubjectSummaryOutputSpec

    def _run_interface(self, runtime):
        if isdefined(self.inputs.subject_id):
            self._results['subject_id'] = self.inputs.subject_id
        return super()._run_interface(runtime)

    def _generate_segment(self):
        brain_mask_status = (
            'Available' if isdefined(self.inputs.brain_mask_file) else 'Not available'
        )
        seg_status = 'Available' if isdefined(self.inputs.dlmuse_seg_file) else 'Not available'
        return SUBJECT_TEMPLATE.format(
            subject_id=self.inputs.subject_id,
            session_id=getattr(self.inputs, 'session_id', 'N/A'),
            n_t1s=len(self.inputs.t1w),
            brain_mask_status=brain_mask_status,
            seg_status=seg_status,
        )


# New UPDATED_ABOUT_TEMPLATE to be used by ExecutionProvenanceReportlet
UPDATED_ABOUT_TEMPLATE = """
\t<ul class="elem-desc">
\t\t<li>{pipeline_name} version: {version}</li>
\t\t<li>{pipeline_name} command: <code>{command}</code></li>
\t\t<li>Date processed: {timestamp}</li>
\t</ul>
</div>
"""  # Retaining the </div> from aslprep's example


class _ExecutionProvenanceInputSpec(BaseInterfaceInputSpec):
    pipeline_name = Str(mandatory=True, desc='Name of the pipeline tool (e.g., qsiprep, ncdlmuse)')
    version = Str(mandatory=True, desc='Version of the pipeline tool')
    command = Str(mandatory=True, desc='The exact command line call used')
    timestamp = Str(mandatory=True, desc='Processing timestamp')


class _ExecutionProvenanceOutputSpec(_SummaryOutputSpec):
    pass


class ExecutionProvenanceReportlet(SummaryInterface):
    """Generates a reportlet summarizing execution provenance details (now serves as 'About')."""

    input_spec = _ExecutionProvenanceInputSpec
    output_spec = _ExecutionProvenanceOutputSpec

    def _generate_segment(self):
        return UPDATED_ABOUT_TEMPLATE.format(
            pipeline_name=self.inputs.pipeline_name,
            version=self.inputs.version,
            command=self.inputs.command,
            timestamp=self.inputs.timestamp,
        )


ERROR_REPORT_TEMPLATE = """
<div>
\t<h4>Processing Errors & Warnings</h4>
{content}
</div>
"""

ERROR_CONTENT_TEMPLATE_MESSAGES = """\
\t<p>The following issues were reported:</p>
\t<ul>
\t\t<li>{error_list_html}</li>
\t</ul>
"""

ERROR_CONTENT_TEMPLATE_NO_ERRORS = """\
\t<p>No specific errors were reported for this subject.</p>
"""


class _ErrorReportletInputSpec(BaseInterfaceInputSpec):
    error_messages = traits.List(
        Str,
        value=[],
        usedefault=True,
        mandatory=False,
        desc='A list of error/status messages. Defaults to empty list.',
    )


class _ErrorReportletOutputSpec(_SummaryOutputSpec):
    pass


class ErrorReportlet(SummaryInterface):
    """Generates an HTML reportlet from a list of error/status messages."""

    input_spec = _ErrorReportletInputSpec
    output_spec = _ErrorReportletOutputSpec

    def _generate_segment(self):
        content_html = ''
        if self.inputs.error_messages:
            # Filter out empty strings that might have been passed if no errors
            actual_messages = [msg for msg in self.inputs.error_messages if msg and msg.strip()]
            if actual_messages:
                errors_html = '</li>\n\t\t<li>'.join(actual_messages)
                content_html = ERROR_CONTENT_TEMPLATE_MESSAGES.format(error_list_html=errors_html)
            else:
                content_html = ERROR_CONTENT_TEMPLATE_NO_ERRORS.format()
        else:
            # This case should be avoided if mandatory=True and we always provide a default list
            content_html = ERROR_CONTENT_TEMPLATE_NO_ERRORS.format()

        return ERROR_REPORT_TEMPLATE.format(content=content_html)


WORKFLOW_PROVENANCE_TEMPLATE = """\
<div>
\t<h4>Workflow & System Provenance</h4>
\t<ul class="elem-desc">
{provenance_items}
\t</ul>
</div>
"""

WORKFLOW_PROVENANCE_ITEM_TEMPLATE = '<li><strong>{key}:</strong> {value}</li>'


class _WorkflowProvenanceReportletInputSpec(BaseInterfaceInputSpec):
    provenance_json_file = File(
        exists=True,
        mandatory=True,
        desc="Path to the JSON file containing the 'provenance' dictionary.",
    )


class _WorkflowProvenanceReportletOutputSpec(_SummaryOutputSpec):
    pass


class WorkflowProvenanceReportlet(SummaryInterface):
    """Generates a reportlet from a 'provenance' dictionary within a JSON file."""

    input_spec = _WorkflowProvenanceReportletInputSpec
    output_spec = _WorkflowProvenanceReportletOutputSpec

    # Define preferred order and display names for known keys
    KNOWN_PROVENANCE_KEYS = {
        'bids_ncdlmuse_version': 'NCDLMUSE Version',
        'nichartdlmuse_version': 'NiChart_DLMUSE Version',
        'torch_version': 'PyTorch Version',
        'cuda_version': 'CUDA Version',
        'cudnn_version': 'cuDNN Version',
        'device_used': 'Device Used',
        'compute_node': 'Compute Node',
        'gpu_driver_version': 'GPU Driver Version',
        # Add other known keys here if they appear in your provenance dict
    }

    def _generate_segment(self):
        import json

        try:
            with open(self.inputs.provenance_json_file) as f:
                data = json.load(f)

            provenance_dict = data.get('provenance')
            if not provenance_dict or not isinstance(provenance_dict, dict):
                return '<p>Provenance information not found or is not in the expected '
                'format in the JSON file.</p>'

            items_html_list = []

            # Add known keys in preferred order
            for key, display_name in self.KNOWN_PROVENANCE_KEYS.items():
                if key in provenance_dict:
                    value = provenance_dict.pop(key)  # Remove to avoid re-adding
                    items_html_list.append(
                        WORKFLOW_PROVENANCE_ITEM_TEMPLATE.format(
                            key=display_name, value=str(value) if value is not None else 'N/A'
                        )
                    )

            # Add any remaining keys from the provenance_dict (sorted for consistency)
            for key, value in sorted(provenance_dict.items()):
                display_key = key.replace('_', ' ').title()
                items_html_list.append(
                    WORKFLOW_PROVENANCE_ITEM_TEMPLATE.format(
                        key=display_key, value=str(value) if value is not None else 'N/A'
                    )
                )

            if not items_html_list:
                return "<p>No provenance items found in the 'provenance' dictionary.</p>"

            provenance_items_html = '\n'.join(items_html_list)
            return WORKFLOW_PROVENANCE_TEMPLATE.format(provenance_items=provenance_items_html)

        except FileNotFoundError:
            return (
                f'<p>Error: Provenance JSON file not found at '
                f'{self.inputs.provenance_json_file}</p>'
            )
        except json.JSONDecodeError:
            return f'<p>Error: Could not decode JSON from {self.inputs.provenance_json_file}</p>'
        except (OSError, ValueError, TypeError) as e:
            return (
                f'<p>An unexpected error occurred while generating '
                f'workflow provenance reportlet: {e}</p>'
            )


SEGMENTATION_QC_TEMPLATE = """\
<div>
\t<h4>Segmentation Volume Summary (mm&sup3;)</h4>
\t<ul class="elem-desc">
{volume_items}
\t</ul>
</div>
"""

SEGMENTATION_QC_ITEM_TEMPLATE = '<li><strong>{name}:</strong> {value:.2f}</li>'

# Define a list of commonly interesting volume keys from _create_volumes_json_file
# These should match the keys in the 'volumes' dictionary within the JSON file
# which uses the original column names from NiChart_DLMUSE.
DEFAULT_DISPLAY_VOLUMES = [
    'total_gray_matter',
    'total_white_matter',
    'total_csf',
    'subcortical_gray_matter',
    'cortical_gray_matter',
    'supratentorial_brain_volume',
    'intracranial_volume_icv',
    # Add or modify these based on actual keys in your 'volumes' dict
]


class _SegmentationQCSummaryInputSpec(BaseInterfaceInputSpec):
    segmentation_qc_json_file = File(
        exists=True,
        mandatory=True,
        desc="Path to the JSON file containing the 'volumes' dictionary.",
    )
    # Optional: could allow user to specify which volume keys to display
    # display_volume_keys = traits.List(Str, value=DEFAULT_DISPLAY_VOLUMES, usedefault=True,
    #                                   desc="List of volume keys to display.")


class _SegmentationQCSummaryOutputSpec(_SummaryOutputSpec):
    pass


class SegmentationQCSummary(SummaryInterface):
    """Generates a reportlet summarizing key segmentation volumes from a JSON file."""

    input_spec = _SegmentationQCSummaryInputSpec
    output_spec = _SegmentationQCSummaryOutputSpec

    def _generate_segment(self):
        import json

        try:
            with open(self.inputs.segmentation_qc_json_file) as f:
                data = json.load(f)

            volumes_dict = data.get('volumes')
            if not volumes_dict or not isinstance(volumes_dict, dict):
                return (
                    '<p>Segmentation volumes not found or not in expected '
                    'format in the JSON file.</p>'
                )

            items_html_list = []
            # display_keys = self.inputs.display_volume_keys
            # For now, use the hardcoded DEFAULT_DISPLAY_VOLUMES
            display_keys = DEFAULT_DISPLAY_VOLUMES

            for key in display_keys:
                if key in volumes_dict:
                    value = volumes_dict[key]
                    try:
                        # Attempt to convert value to float for formatting
                        value_float = float(value)
                        display_name = key.replace('_', ' ').title()
                        items_html_list.append(
                            SEGMENTATION_QC_ITEM_TEMPLATE.format(
                                name=display_name, value=value_float
                            )
                        )
                    except (ValueError, TypeError):
                        # If value can't be float, display as is (or skip/log)
                        display_name = key.replace('_', ' ').title()
                        items_html_list.append(
                            f'<li><strong>{display_name}:</strong> {value} '
                            f'(could not format as number)</li>'
                        )

            if not items_html_list:
                return '<p>No specified segmentation volumes found in the JSON file.</p>'

            volume_items_html = '\n'.join(items_html_list)
            return SEGMENTATION_QC_TEMPLATE.format(volume_items=volume_items_html)

        except FileNotFoundError:
            return (
                f'<p>Error: Segmentation QC JSON file not found at '
                f'{self.inputs.segmentation_qc_json_file}</p>'
            )
        except json.JSONDecodeError:
            return (
                f'<p>Error: Could not decode JSON from {self.inputs.segmentation_qc_json_file}</p>'
            )
        except (OSError, ValueError, TypeError) as e:
            return (
                f'<p>An unexpected error occurred while generating '
                f'segmentation QC reportlet: {e}</p>'
            )
