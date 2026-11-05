# Frozen historical native audit fixture

`native_owned_cell_store_20261103.py.txt` is byte-for-byte the already-published file from commit `4e5712d` at `checkpoints/backend-capacity-source-20261031/source/audio_backend/native_owned_cell_store.py`. SHA256: `efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8`.

The November 3 read-only audit deliberately pins that historical source. Its synthetic tests must construct historical candidates from these bytes instead of copying the evolving current product file. The audit tool, production source and accepted qualification hashes are unchanged. A separate assertion confirms the current profile remains rejected by the historical audit. This fixture is not imported or packaged as product code and does not authorize any runtime.
