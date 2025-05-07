# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# Copyright 2024 The NiPreps Developers <nipreps@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the \"License\");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an \"AS IS\" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# We support and encourage derived works from this project, please read
# about our expectations at
#
#     https://www.nipreps.org/community/licensing/
#
"""Utility interfaces for NCDLMUSE."""

import os
import shutil

import pandas as pd
from nipype.interfaces.base import (
    BaseInterfaceInputSpec,
    Directory,
    File,
    SimpleInterface,
    TraitedSpec,
    traits,
)
from nipype.utils.filemanip import fname_presuffix


class CopyFileInputSpec(BaseInterfaceInputSpec):
    """Input specification for CopyFile Interface."""

    source_file = File(exists=True, mandatory=True, desc='Source file')
    destination = Directory(exists=True, mandatory=True, desc='Destination directory')
    destination_filename = traits.Str(desc='New filename (if different from source)')


class CopyFileOutputSpec(TraitedSpec):
    """Output specification for CopyFile Interface."""

    copied_file = File(exists=True, desc='Copied file')


class CopyFile(SimpleInterface):
    """Copy a file to a destination directory."""

    input_spec = CopyFileInputSpec
    output_spec = CopyFileOutputSpec

    def _run_interface(self, runtime):
        src = self.inputs.source_file
        dest_dir = self.inputs.destination

        # Create destination directory if it doesn't exist
        os.makedirs(dest_dir, exist_ok=True)

        # Use provided filename or original filename
        if hasattr(self.inputs, 'destination_filename') and self.inputs.destination_filename:
            dest_filename = self.inputs.destination_filename
        else:
            dest_filename = os.path.basename(src)

        dest_path = os.path.join(dest_dir, dest_filename)

        # Copy the file
        shutil.copy2(src, dest_path)

        # Store output
        self._results['copied_file'] = dest_path

        return runtime


# Add the new interface definition
class _CSVToTSVInputSpec(BaseInterfaceInputSpec):
    in_csv = File(exists=True, mandatory=True, desc='Input CSV file')
    output_filename = traits.Str('output.tsv', usedefault=True, desc='Filename for the output TSV')


class _CSVToTSVOutputSpec(TraitedSpec):
    out_tsv = File(exists=True, desc='Output TSV file')


class CSVToTSV(SimpleInterface):
    """Converts a CSV file to a TSV file using pandas."""

    input_spec = _CSVToTSVInputSpec
    output_spec = _CSVToTSVOutputSpec

    def _run_interface(self, runtime):
        df = pd.read_csv(self.inputs.in_csv)
        # Generate output filename based on input or default
        if self.inputs.output_filename == 'output.tsv':
            out_file_path = fname_presuffix(
                self.inputs.in_csv, suffix='.tsv', newpath=runtime.cwd, use_ext=False
            )
        else:
            out_file_path = os.path.join(runtime.cwd, self.inputs.output_filename)

        df.to_csv(out_file_path, sep='\\t', index=False)
        self._results['out_tsv'] = out_file_path
        return runtime
