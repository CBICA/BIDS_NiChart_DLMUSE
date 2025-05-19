import json
import sys
from pathlib import Path

import pandas as pd
from bids import BIDSLayout


def aggregate_volumes(derivatives_dir, output_file):
    """Aggregates volumetric data from individual *_T1w.json files.

    Parameters
    ----------
    derivatives_dir : str or Path
        Path to the NCDLMUSE derivatives directory.
    output_file : str or Path
        Path where the output TSV `group_ncdlmuse_volumes.tsv` should be saved.
    """
    derivatives_dir = Path(derivatives_dir)
    output_file = Path(output_file)
    print(f'Aggregating json files with ROI volumes from: {derivatives_dir}')

    try:
        # Use BIDSLayout to find the output JSON files
        layout = BIDSLayout(derivatives_dir, validate=False)
        # Adjust filters if needed to be more specific
        json_files = layout.get(suffix='T1w', extension='json', return_type='file')

        if not json_files:
            print(f'WARNING: No T1w JSON files found in {derivatives_dir}', file=sys.stderr)
            return

        all_data_rows = []
        all_volume_keys = set() # Keep track of all unique volume keys

        for json_path in json_files:
            try:
                entities = layout.parse_file_entities(json_path)
                subject_id = f"sub-{entities['subject']}"
                session_id = f"ses-{entities['session']}" if 'session' in entities else None

                with open(json_path) as f:
                    data = json.load(f)

                if 'volumes' not in data or not isinstance(data['volumes'], dict):
                    print(
                        f"WARNING: No 'volumes' dict found in {json_path}. "
                        f"Skipping.", file=sys.stderr
                        )
                    continue

                # Prepare row data
                row = {'subject': subject_id}
                if session_id:
                    row['session'] = session_id

                # Add volumes data
                row.update(data['volumes'])
                all_data_rows.append(row)
                all_volume_keys.update(data['volumes'].keys())

            except FileNotFoundError:
                 print(f'ERROR: File not found during aggregation: {json_path}', file=sys.stderr)
            except json.JSONDecodeError:
                 print(f'WARNING: Could not decode JSON: {json_path}', file=sys.stderr)
            except (KeyError, TypeError, ValueError, OSError) as e:
                 print(f'WARNING: Error processing {json_path}: {e!r}', file=sys.stderr)

        if not all_data_rows:
            # LOGGER.warning('No valid volume data collected.')
            print('WARNING: No valid volume data collected.', file=sys.stderr)
            return

        # Create DataFrame
        df = pd.DataFrame(all_data_rows)

        # Define column order: IDs first, then sorted volume keys
        id_cols = ['subject']
        if 'session' in df.columns:
            id_cols.append('session')
        # Place 'mrid' next, if present, then remaining volumes sorted
        volume_cols = sorted(all_volume_keys)
        if 'mrid' in volume_cols:
            volume_cols.remove('mrid')
            final_cols = id_cols + ['mrid'] + volume_cols
        else:
            final_cols = id_cols + volume_cols

        # Reorder and save
        df = df.reindex(columns=final_cols)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_file, sep='\t', index=False, na_rep='n/a')
        print(f'Aggregated volumes saved to {output_file}')

    except (OSError, pd.errors.PandasError, MemoryError) as e:
        print(f'ERROR: Volume aggregation failed: {e!r}', file=sys.stderr)
