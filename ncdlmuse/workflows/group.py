import json
import logging
from pathlib import Path
import pandas as pd
from bids import BIDSLayout

LOGGER = logging.getLogger('ncdlmuse.workflow.base')

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
    LOGGER.info(f"Aggregating volumes from: {derivatives_dir}")

    try:
        # Use BIDSLayout to find the output JSON files
        layout = BIDSLayout(derivatives_dir, validate=False)
        # Adjust filters if needed to be more specific
        json_files = layout.get(suffix='T1w', extension='json', return_type='file')

        if not json_files:
            LOGGER.warning(f"No T1w JSON files found in {derivatives_dir}")
            return

        all_data_rows = []
        all_volume_keys = set() # Keep track of all unique volume keys

        for json_path in json_files:
            try:
                LOGGER.debug(f"Processing: {json_path}")
                entities = layout.parse_file_entities(json_path)
                subject_id = f"sub-{entities['subject']}"
                session_id = f"ses-{entities['session']}" if 'session' in entities else None

                with open(json_path) as f:
                    data = json.load(f)

                if 'volumes' not in data or not isinstance(data['volumes'], dict):
                    LOGGER.warning(f"No 'volumes' dict found in {json_path}. Skipping.")
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
                 LOGGER.error(f"File not found during aggregation: {json_path}")
            except json.JSONDecodeError:
                 LOGGER.warning(f"Could not decode JSON: {json_path}")
            except Exception as e:
                 LOGGER.warning(f"Error processing {json_path}: {e!r}")

        if not all_data_rows:
            LOGGER.warning("No valid volume data collected.")
            return

        # Create DataFrame
        df = pd.DataFrame(all_data_rows)

        # Define column order: IDs first, then sorted volume keys
        id_cols = ['subject']
        if 'session' in df.columns:
            id_cols.append('session')
        # Place 'mrid' next, if present, then remaining volumes sorted
        volume_cols = sorted(list(all_volume_keys))
        if 'mrid' in volume_cols:
            volume_cols.remove('mrid')
            final_cols = id_cols + ['mrid'] + volume_cols
        else:
            final_cols = id_cols + volume_cols

        # Reorder and save
        df = df.reindex(columns=final_cols)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_file, sep='\t', index=False, na_rep='n/a')
        LOGGER.info(f"Aggregated volumes saved to {output_file}")

    except Exception as e:
        LOGGER.error(f"Volume aggregation failed: {e!r}") 