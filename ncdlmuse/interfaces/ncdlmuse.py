# interfaces/ncdlmuse.py
"""Interface to NiChart_DLMUSE."""

import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
from nipype.interfaces.base import (
    BaseInterfaceInputSpec,
    File,
    SimpleInterface,
    TraitedSpec,
    traits,
)

from .. import config

# Use standardized logger
logger = config.loggers.getLogger('ncdlmuse.interfaces.ncdlmuse')

# --- Constants for filenames and directories ---
_RAW_OUT_SUBDIR = 'ncdlmuse_raw_out'
_INPUT_COPY_SUBDIR_PREFIX = '_input_files'
_S2_DLICV_SUBDIR = 's2_dlicv'
_DLMUSE_SUFFIX = '_DLMUSE.nii.gz'
_DLICV_SUFFIX = '_DLICV.nii.gz'
_VOLUMES_CSV_SUFFIX = '_DLMUSE_Volumes.csv'
_PROCESSED_VOLUMES_TSV = 'dlmuse_volumes.tsv'

# Log level constants
_IMPORTANT_LEVEL = 25  # matches logging.addLevelName(25, 'IMPORTANT') in config
# ---------------------------------------------


class NiChartDLMUSEInputSpec(BaseInterfaceInputSpec):
    """Input specification for NiChart_DLMUSE."""

    input_image = File(exists=True, mandatory=True, desc='Input T1w image')
    device = traits.Enum('cpu', 'cuda', 'mps', usedefault=True, desc='Device to use')
    model_folder = traits.Str(desc='Path to custom model folder')
    derived_roi_mappings_file = traits.Str(desc='Path to derived ROI mappings file')
    muse_roi_mappings_file = traits.Str(desc='Path to MUSE ROI mappings file')
    all_in_gpu = traits.Bool(False, usedefault=True, desc='Run all operations on GPU')
    disable_tta = traits.Bool(False, usedefault=True, desc='Disable Test-Time Augmentation')
    clear_cache = traits.Bool(False, usedefault=True, desc='Clear model cache')
    save_all_outputs = traits.Bool(
        False, usedefault=True, desc='Save all intermediate outputs including raw outputs dir'
    )
    # Dummy input to force re-run by invalidating cache
    _timestamp = traits.Float(desc='Timestamp for cache invalidation')
    # Dummy input to enforce dependency on workdir clearing
    _depends_on_workdir_clear = traits.Any(desc='Dummy input for workflow graph dependency')


class NiChartDLMUSEOutputSpec(TraitedSpec):
    """Output specification for NiChart_DLMUSE."""

    dlmuse_segmentation = File(desc='DLMUSE segmentation file (NIfTI)')
    dlicv_mask = File(desc='DLICV brain mask file (NIfTI)')
    dlmuse_volumes = File(
        desc='DLMUSE volumes TSV file (converted from CSV) or original CSV as fallback'
    )
    dlmuse_volumes_csv = File(desc='Original DLMUSE volumes CSV file (copied to output dir)')
    raw_outputs_dir = traits.Directory(
        desc='Directory containing all raw NiChart_DLMUSE outputs (when save_all_outputs=True)'
    )


class NiChartDLMUSE(SimpleInterface):
    """Nipype interface for running the NiChart_DLMUSE command-line tool.

    This interface wraps the ``NiChart_DLMUSE`` executable.

    **Execution Logic:**

    1.  **Directory Setup:**
        *   Creates a subdirectory within the Nipype node's working directory (`cwd`) to
            store the raw outputs from `NiChart_DLMUSE` (``{cwd}/ncdlmuse_raw_out``).
        *   Creates a *sibling* directory to `cwd` (e.g., ``{cwd.parent}/{cwd.name}_input_files``)
            to store a copy of the input image. This separation might be necessary due to
            how `NiChart_DLMUSE` handles input/output paths or potential path length issues.
            The `-i` argument for `NiChart_DLMUSE` points to this separated input directory.
        *   The `-o` argument points to the ``ncdlmuse_raw_out`` subdirectory.
    2.  **Command Execution:** Runs ``NiChart_DLMUSE`` with appropriate arguments.
    3.  **Output Handling:**
        *   Checks if essential raw output files (segmentation, mask, volumes CSV) exist
            in the ``ncdlmuse_raw_out`` directory. Raises an error if not found.
        *   Copies these essential files from ``ncdlmuse_raw_out`` to the main ``cwd``.
        *   The DLICV mask is placed within a subdirectory ``{cwd}/s2_dlicv`` mirroring the
            raw output structure.
    4.  **Volume Processing:**
        *   Reads the *copied* volumes CSV file from ``cwd``.
        *   Saves the volumes as a TSV file (``dlmuse_volumes.tsv``) in ``cwd``.
    5.  **Output Assignment:** The ``_list_outputs`` method identifies the final output files
        within ``cwd`` (prioritizing the processed TSV for volumes) and returns their paths.

    **Outputs:**

    *   Outputs are found within the interface's working directory (runtime.cwd).
    """

    input_spec = NiChartDLMUSEInputSpec
    output_spec = NiChartDLMUSEOutputSpec
    _cwd = None  # Stores runtime.cwd for use in _list_outputs

    def _run_interface(self, runtime):
        """Execute the NiChart_DLMUSE command and process outputs."""
        # --- 1. Setup Paths and Directories --- #
        input_image_path = Path(self.inputs.input_image)
        base_name = input_image_path.name.replace('.nii.gz', '').replace('.nii', '')

        # Node's working directory (final output location)
        self._cwd = Path(runtime.cwd).resolve()
        logger.info(f'Node working directory (cwd): {self._cwd}')

        # Subdirectory for raw tool outputs (within cwd)
        raw_output_dir = self._cwd / _RAW_OUT_SUBDIR

        # Separate directory for input copy (sibling to cwd)
        # Rationale: Necessary if NiChart_DLMUSE has issues with inputs/outputs
        # in the same deep path, or requires specific relative locations.
        internal_in_dir = self._cwd.parent / f'{self._cwd.name}{_INPUT_COPY_SUBDIR_PREFIX}'

        try:
            raw_output_dir.mkdir(exist_ok=True, parents=True)
            logger.info(f'Created/Ensured raw output dir: {raw_output_dir}')
            internal_in_dir.mkdir(exist_ok=True, parents=True)
            logger.info(f'Created/Ensured internal input dir: {internal_in_dir}')
        except Exception as e:
            logger.error(f'Error creating execution directories: {e}')
            raise

        # Copy input file to the separated input directory
        input_file_copy = internal_in_dir / input_image_path.name
        try:
            shutil.copy2(self.inputs.input_image, input_file_copy)
            logger.info(f'Copied input {self.inputs.input_image} to {input_file_copy}')
            if not input_file_copy.is_file():
                raise FileNotFoundError(f'Failed to verify input copy at {input_file_copy}')
        except Exception as e:
            logger.error(f'Error copying input file to {internal_in_dir}: {e}')
            raise

        # --- 2. Build and Run Command --- #
        cmd = [
            'NiChart_DLMUSE',
            '-i',
            str(internal_in_dir.resolve()),  # Point -i to the directory containing the copy
            '-o',
            str(raw_output_dir.resolve()),  # Point -o to the raw output subdir
            '-d',
            self.inputs.device,
        ]

        # Add optional arguments
        if self.inputs.model_folder:
            cmd.extend(['--model_folder', self.inputs.model_folder])
        if self.inputs.derived_roi_mappings_file:
            cmd.extend(['--derived_ROI_mappings_file', self.inputs.derived_roi_mappings_file])
        if self.inputs.muse_roi_mappings_file:
            cmd.extend(['--MUSE_ROI_mappings_file', self.inputs.muse_roi_mappings_file])
        if self.inputs.all_in_gpu:
            cmd.append('--all_in_gpu')
        if self.inputs.disable_tta:
            cmd.append('--disable_tta')
        if self.inputs.clear_cache:
            cmd.append('--clear_cache')

        logger.log(_IMPORTANT_LEVEL, f'Running command: {" ".join(cmd)}')
        try:
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)

            # Always report on stdout, even if empty
            if process.stdout:
                logger.log(_IMPORTANT_LEVEL, f'NiChart_DLMUSE stdout:\n{process.stdout.strip()}')
            else:
                logger.log(_IMPORTANT_LEVEL, 'NiChart_DLMUSE stdout: (empty)')

            # Report stderr (which often contains progress bars)
            if process.stderr:
                logger.log(_IMPORTANT_LEVEL, f'NiChart_DLMUSE stderr:\n{process.stderr.strip()}')
            else:
                logger.log(_IMPORTANT_LEVEL, 'NiChart_DLMUSE stderr: (empty)')

        except subprocess.CalledProcessError as e:
            logger.error(
                f'NiChart_DLMUSE command failed (exit code {e.returncode}): {" ".join(e.cmd)}'
            )
            if e.stdout:
                logger.error(f'  Stdout:\n{e.stdout.strip()}')
            if e.stderr:
                logger.error(f'  Stderr:\n{e.stderr.strip()}')
            # Log contents of raw output dir for debugging
            self._log_dir_contents(raw_output_dir, 'raw output')
            raise RuntimeError('NiChart_DLMUSE execution failed.') from e
        except FileNotFoundError:
            logger.error('NiChart_DLMUSE command not found. Is it installed and in PATH?')
            raise
        except Exception as e:
            logger.error(f'An unexpected error occurred running NiChart_DLMUSE: {e}')
            raise

        # --- 3. Locate Raw Outputs (robust to nested temp directories) --- #
        # Search recursively within raw_output_dir for the expected filenames.
        seg_candidates = list(raw_output_dir.rglob(f'{base_name}{_DLMUSE_SUFFIX}'))
        mask_candidates = [
            p
            for p in raw_output_dir.rglob(f'{base_name}{_DLICV_SUFFIX}')
            if p.parent.name == _S2_DLICV_SUBDIR
        ]
        vol_csv_candidates = list(raw_output_dir.rglob(f'{base_name}{_VOLUMES_CSV_SUFFIX}'))

        raw_seg_path = seg_candidates[0] if seg_candidates else None
        raw_mask_path = mask_candidates[0] if mask_candidates else None
        raw_volumes_csv_path = vol_csv_candidates[0] if vol_csv_candidates else None

        missing_raw_files = []
        if raw_seg_path is None or not raw_seg_path.exists():
            missing_raw_files.append(f'{base_name}{_DLMUSE_SUFFIX}')
        if raw_mask_path is None or not raw_mask_path.exists():
            missing_raw_files.append(f'{_S2_DLICV_SUBDIR}/{base_name}{_DLICV_SUFFIX}')
        if raw_volumes_csv_path is None or not raw_volumes_csv_path.exists():
            missing_raw_files.append(f'{base_name}{_VOLUMES_CSV_SUFFIX}')

        if missing_raw_files:
            error_msg = (
                'NiChart_DLMUSE finished but essential raw output files are missing under '
                f'{raw_output_dir}: ' + ', '.join(missing_raw_files)
            )
            logger.error(error_msg)
            self._log_dir_contents(raw_output_dir, 'raw output')
            raise FileNotFoundError(error_msg)
        else:
            logger.info('Essential raw output files located. Copying to final location.')

        # Define final paths in cwd
        final_seg_path = self._cwd / raw_seg_path.name
        final_mask_subdir = self._cwd / _S2_DLICV_SUBDIR
        final_mask_path = final_mask_subdir / raw_mask_path.name
        final_volumes_csv_path = self._cwd / raw_volumes_csv_path.name  # Copy of original CSV

        # Copy files from raw_output_dir to cwd
        try:
            final_mask_subdir.mkdir(exist_ok=True)
            shutil.copy2(raw_seg_path, final_seg_path)
            logger.info(f'Copied {raw_seg_path.name} to {final_seg_path}')
            shutil.copy2(raw_mask_path, final_mask_path)
            logger.info(f'Copied {raw_mask_path.name} to {final_mask_path}')
            shutil.copy2(raw_volumes_csv_path, final_volumes_csv_path)
            logger.info(
                f'Copied {raw_volumes_csv_path.name} to {final_volumes_csv_path} (original CSV)'
            )
        except Exception as e:
            logger.error(f'Error copying files from {raw_output_dir} to {self._cwd}: {e}')
            raise

        # --- 4. Keep raw outputs and temp_working_dir when save_all_outputs is True --- #
        # Intentionally do nothing here. We never remove or relocate NiChart_DLMUSE raw outputs
        # (including temp_working_dir) when save_all_outputs=True.

        # --- 5. Optionally prune temporary working directory when not keeping everything --- #
        # If not saving all outputs, remove ONLY the temp_working_dir, keep other raw outputs.
        if not getattr(self.inputs, 'save_all_outputs', False):
            temp_working_dir = raw_output_dir / 'temp_working_dir'
            try:
                if temp_working_dir.exists():
                    shutil.rmtree(temp_working_dir)
                    logger.info(
                        f'Removed temp working directory {temp_working_dir} '
                        f'(save_all_outputs=False).'
                    )
                else:
                    logger.info(
                        f'temp_working_dir not found at {temp_working_dir}, nothing to remove.'
                    )
            except OSError as e:
                logger.warning(f'Could not remove temp working directory {temp_working_dir}: {e}')

        # --- 6. Process Volumes CSV --- #
        final_volumes_tsv_path = self._cwd / _PROCESSED_VOLUMES_TSV
        self._process_volumes(final_volumes_csv_path, final_volumes_tsv_path)

        logger.log(
            _IMPORTANT_LEVEL, 'NiChartDLMUSE interface (_run_interface) completed successfully.'
        )
        # _list_outputs will handle finding files and setting self._results
        return runtime

    def _process_volumes(self, input_csv_path, output_tsv_path):
        """Load volumes CSV and save as TSV without renaming headers."""
        logger.info(f'Processing volumes: {input_csv_path} -> {output_tsv_path}')

        # Read and process volumes CSV
        try:
            volumes_df = pd.read_csv(input_csv_path)
            # Save as TSV without renaming columns
            volumes_df.to_csv(output_tsv_path, sep='\t', index=False)
            logger.log(
                _IMPORTANT_LEVEL, f'Successfully wrote processed volumes TSV to: {output_tsv_path}'
            )

        except pd.errors.EmptyDataError:
            logger.error(f'Input volumes CSV is empty: {input_csv_path}. Cannot generate TSV.')
            # Do not raise error here, _list_outputs will handle fallback
        except (pd.errors.ParserError, OSError) as e:
            logger.error(f'Error processing volumes file {input_csv_path}: {e}')
            # Do not raise error here, _list_outputs will handle fallback

    def _log_dir_contents(self, directory, label='directory'):
        """Helper to log the contents of a directory, especially on error."""
        try:
            if Path(directory).is_dir():
                dir_contents = os.listdir(directory)
                logger.info(f'Contents of {label} ({directory}): {dir_contents}')
            else:
                logger.warning(
                    f'{label.capitalize()} directory not found or not a directory: {directory}'
                )
        except OSError as list_e:
            logger.error(f'Could not list contents of {label} directory {directory}: {list_e}')

    def _list_outputs(self):
        """Find output files in the working directory (cwd) and return paths."""
        outputs = self.output_spec().get()  # Initialize outputs dictionary

        if not self._cwd or not self._cwd.is_dir():
            # This case should ideally not happen if _run_interface succeeded
            logger.critical('[_list_outputs] Working directory (self._cwd) not set or invalid!')
            # Attempt fallback, but this indicates a problem
            self._cwd = Path(os.getcwd()).resolve()
            logger.warning(f'[_list_outputs] Falling back to os.getcwd(): {self._cwd}')
        else:
            logger.info(f'[_list_outputs] Using working directory: {self._cwd}')

        # Determine base name from input image path
        try:
            input_image_path = Path(self.inputs.input_image)
            base_name = input_image_path.name.replace('.nii.gz', '').replace('.nii', '')
        except Exception as e:
            logger.error(f'[_list_outputs] Could not determine base_name from input: {e}')
            # Cannot proceed without base_name to find files
            raise ValueError('Could not determine base_name for finding output files.') from e

        # --- Define FINAL expected paths in self._cwd --- #
        final_seg_path = self._cwd / f'{base_name}{_DLMUSE_SUFFIX}'
        final_mask_path = self._cwd / _S2_DLICV_SUBDIR / f'{base_name}{_DLICV_SUFFIX}'
        # This is the copy of the original CSV in cwd
        final_volumes_csv_path = self._cwd / f'{base_name}{_VOLUMES_CSV_SUFFIX}'
        # This is the potentially processed TSV in cwd
        processed_volumes_tsv_path = self._cwd / _PROCESSED_VOLUMES_TSV

        # --- Check for and assign mandatory outputs --- #

        # Segmentation
        if final_seg_path.exists():
            outputs['dlmuse_segmentation'] = str(final_seg_path.resolve())
            logger.info(f'[_list_outputs] Found segmentation: {outputs["dlmuse_segmentation"]}')
        else:
            self._log_dir_contents(self._cwd, 'final working')
            raise FileNotFoundError(
                f'DLMUSE segmentation output not found in working directory: {final_seg_path}'
            )

        # Mask
        if final_mask_path.exists():
            outputs['dlicv_mask'] = str(final_mask_path.resolve())
            logger.info(f'[_list_outputs] Found mask: {outputs["dlicv_mask"]}')
        else:
            self._log_dir_contents(self._cwd / _S2_DLICV_SUBDIR, 'final mask')
            raise FileNotFoundError(
                f'DLICV mask output not found in working directory: {final_mask_path}'
            )

        # --- Check for and assign primary volumes output (TSV preferred, fallback to CSV) --- #
        volumes_assigned = False
        if processed_volumes_tsv_path.exists():
            outputs['dlmuse_volumes'] = str(processed_volumes_tsv_path.resolve())
            volumes_assigned = True
            logger.info(
                f'[_list_outputs] Found processed volumes TSV: {outputs["dlmuse_volumes"]}'
            )
        elif final_volumes_csv_path.exists():  # Check for the *copied* original CSV in cwd
            outputs['dlmuse_volumes'] = str(final_volumes_csv_path.resolve())
            volumes_assigned = True
            logger.warning(
                f'[_list_outputs] Processed TSV not found, using copied original CSV as fallback: '
                f'{outputs["dlmuse_volumes"]}'
            )

        if not volumes_assigned:
            self._log_dir_contents(self._cwd, 'final working')
            raise FileNotFoundError(
                f'Neither processed volumes TSV ({processed_volumes_tsv_path.name}) '
                f'nor copied original CSV ({final_volumes_csv_path.name}) were found in '
                f'{self._cwd}.'
            )

        # --- Assign original volumes CSV output (points to the copy in cwd) --- #
        if final_volumes_csv_path.exists():
            outputs['dlmuse_volumes_csv'] = str(final_volumes_csv_path.resolve())
            logger.info(
                f'[_list_outputs] Found copied original CSV: {outputs["dlmuse_volumes_csv"]}'
            )
        else:
            # This is unexpected if the fallback above worked, but log just in case
            logger.warning(
                f'[_list_outputs] Copied original CSV not found: {final_volumes_csv_path}'
            )
            # Don't assign if not found

        # --- Assign raw outputs directory if save_all_outputs is enabled --- #
        if getattr(self.inputs, 'save_all_outputs', False):
            raw_output_dir = self._cwd / _RAW_OUT_SUBDIR
            if raw_output_dir.exists() and raw_output_dir.is_dir():
                outputs['raw_outputs_dir'] = str(raw_output_dir.resolve())
                logger.info(
                    f'[_list_outputs] Found raw outputs directory: {outputs["raw_outputs_dir"]}'
                )
            else:
                logger.warning(
                    f'[_list_outputs] Raw outputs directory not found: {raw_output_dir}'
                )

        # Log final state before returning
        logger.info(f'[_list_outputs] Returning outputs: {outputs}')
        return outputs
