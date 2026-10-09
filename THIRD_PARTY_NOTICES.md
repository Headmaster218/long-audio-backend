# Third-party notices

No project-wide license has been selected for the original project code. Public visibility alone is not a blanket license grant.

Model and runtime manifests identify public upstream URLs, exact pinned versions, sizes, SHA256 values and their license references. Those upstream terms remain applicable when downloading, using or redistributing their assets. This repository contains no model weights or downloaded/compiled upstream implementations.

- `model-manifest.json`: base ASR, diarization, speaker and event assets; SenseVoice weights have custom FunASR model terms.
- `native-runtime-manifest.json`: NeMo-Speech.cpp runtime and Nemotron diarization model. Preserve runtime dependency notices; retained model terms are in `licenses/native-diarization/OpenMDW-1.1.txt`.
- `overlap-model-manifest.json`, `dicow-model-manifest.json`, `moss-td-model-manifest.json`, `moss-td-cpp-model-manifest.json`: separately selected experimental dependencies and their terms.
- `licenses/clap-model-note.md`: retained research-model notice.
- `docs/DEPENDENCY_SOURCE_CLOSURE.json`: public upstream source pin metadata used to check the MOSS CPP preparation closure; no upstream source is included.

Research datasets and recordings have separate licenses, potentially including noncommercial or share-alike restrictions. They are not distributed here. Do not assume model, code and dataset licenses are interchangeable.
