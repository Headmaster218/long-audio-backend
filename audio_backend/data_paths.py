"""Read-only pre-cleanup guard against relocated or unsafe retained paths."""
from pathlib import Path


def verify_owned_data_paths(store):
    root=store.settings.data_dir.resolve()
    uploads=(root/'uploads').resolve()
    error=('Stored audio paths do not belong to this data directory. Do not start '
           'a copied database before offline migration; see docs/DATA_MIGRATION.md. '
           'No temporary/orphan audio cleanup was performed.')
    if not uploads.is_relative_to(root):
        raise RuntimeError(error)
    with store.db() as db:
        # Cursor iteration stays bounded even with years of retained receipts.
        for table in ('chunks','session_file_cleanup'):
            for row in db.execute(f'SELECT path FROM {table}'):
                raw=row['path']
                try:
                    path=Path(raw)
                    valid=(path.is_absolute() and path.suffix=='.audio'
                           and path.parent.resolve()==uploads and str(path.resolve())==raw)
                except (TypeError,ValueError,OSError,RuntimeError):
                    valid=False
                if not valid:raise RuntimeError(error)
