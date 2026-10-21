# Candidate identity

The candidate is called dev67 for this recovery change. The qualified package's
metadata and module version remain `0.2.0rc3.dev66`. No version bump was made or
implied. Identify this candidate by its exact payload hashes, not the inherited
version label, and do not confuse it with the earlier dev66 package.

Exactly three production files differ from the qualified dev66 baseline:

| Repository-relative file | SHA256 |
| --- | --- |
| audio_backend/app.py | fcc9a4e37ca9c2551fb591b72a80bbbaa0b56e29b9629dfbd92a4761e3edf183 |
| audio_backend/moss_handy_jobs.py | 30f1d48d4357b256af320465656327a82099fd7c66ec3d127d23078a5673481d |
| audio_backend/recording_speaker_history.py | b1d3a4a365d048d576e4d9b005aa026ea15c05206a04b7583b7b5ed199870dac |

Qualified installed wheel SHA256:
`09f98748367d223ef545df7d40251fabe55935c8b772e9a8cc528d55a6ed914b`

All 138 production payload files matched source, wheel and installed bytes.
The installed inventory contained 145 files, and all 70 loaded module origins
in the installed-test qualification were verified under that installed payload.
These are package/source identities, not fingerprints of private user data.

The existing `tests/test_recording_speaker_history.py` fixture was changed to
acquire a real instance lock and establish recovery before binding its success
owner. Its existing test bodies were preserved. Four new modules cover startup
recovery, process/commit boundaries, real application ordering and authenticated
admission boundaries. Their exact file hashes are in `files.sha256`.
