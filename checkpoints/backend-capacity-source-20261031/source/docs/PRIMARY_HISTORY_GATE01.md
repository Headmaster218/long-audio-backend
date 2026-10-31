# First model-free integrated history gate

Scope: two exact pytest nodes in tests/test_recording_speaker_history_integration.py:

- test_three_natural_groups_aab_real_cleanup_and_passive_read
- test_two_natural_groups_abstain_without_competitor

The production path uses finalized recording ingestion, owned-copy capture, acknowledged primary commit, ordinary cohort cleanup, automatic text/primary fairness, a fresh ProbeOperation with a synthetic backend driver, positive operation cleanup, atomic history certificates, complete revision-bound comparison and the normal HTTP transcript read. Native/ASR/backend model edges are synthetic. The positive fixture uses five real work chunks (10.5, 10.5, 10.5, 10.5 and 8 seconds); the negative uses three (10.5, 10.5 and 9 seconds). No product recording geometry or retry floor was changed.

First dispatch: two failures before primary admission. An inherited one-chunk fixture allowed only four seconds for receipt publication, while these recordings produced five/three work chunks. The failure evidence was preserved. The correction uses actual work IDs and per-chunk final offsets, a finite chunk-count-based ingestion wait respecting normal ticks, bounded mapped PCM copying, and duration-correct synthetic ASR tails.

Corrected dispatch: 2 passed, 1 warning in 20.56 seconds; supervised 21.246523062 seconds. Previous failed dispatch 11.790318350 seconds remains included in the 120-second cumulative budget. Peak sampled process-tree RSS 129585152 bytes; retained artifacts across both dispatches 16654336 bytes. CPU 3, nice 19, 256 MiB sampled tree RSS, 1 GiB per-process address space, 256 MiB/4096-entry cumulative artifact ceiling. Positive child reap and exact pre/post source/tool equality passed.

Evidence pins:

- Corrected source manifest: 77eab2c9833c57688083ba0f0174a47ffa4dd8e1dc7961d3bee999d755e99daa
- Corrected control: d6e1d0bb514a2a4bd13df5550b14063819af88884433eb235b5bcdfd8a5552a6
- Corrected terminal JSON: 73a5efec85797e19ce895113cd914b310e16a2223ba33a7bfad69d4f11cd35e4
- Corrected JUnit XML: 6c81a5a25818c91f168ef737ee655ca7f4bd8a30af6df9ed5297b3e6b8d92bf3
- Corrected pytest log: 1e9de0686f2a8b8b871410ca3783883185a0d14be93e036120bd0bbb19ee8557
- Initial failed terminal JSON: 53f288ac892f6177ef97da677f5fc404c1ad1c033efae97802b32b6643fed990

This gate qualifies the stated synthetic integrated semantics only. It does not qualify acoustics, actual model execution, concurrent CPU/GIL latency, sustained-capture fairness, full failure/restart/retirement coverage, or production deployment.
