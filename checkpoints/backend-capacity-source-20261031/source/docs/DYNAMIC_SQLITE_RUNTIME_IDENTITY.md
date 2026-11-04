# Strict observed dynamic SQLite identity

The default qualified runtime record is unchanged. Supporting another observed
binding does not activate it or approve a target host. Independent physical,
real-verifier and acceptance gates remain required before a fresh isolated smoke.

The existing built-in binding retains its exact fingerprint output and checks.
The additional binding is named
`observed-dynamic-elf64-x86_64-sqlite-relocations-v1`. It is deliberately limited
to Linux, ELF64 little-endian x86-64 CPython extension modules using supported
unversioned global function relocations. Other architectures, imported IFUNC
symbols, nonglobal or versioned SQLite imports, unsupported relocation formats
or unresolved lazy slots are refused. Library export symbol types are not
independently parsed: exact dladdr name/address checks reject ordinary resolved
IFUNC targets, but this is not a universal detector of library-export IFUNC forms.

Verification includes:

- The original interpreter file identity/content/path checks, SQLite package
  `.py`/existing `.pyc` inventory and budgets, SQLite source ID and compile options.
- An ExtensionFileLoader `_sqlite3` with matching spec origin/module filename;
  bounded extension hashing and stable file identity before/after reading ELF.
- Loader-consumed PT_DYNAMIC RELA tables, not an external `readelf` invocation or
  a fixed expected symbol count. Every `sqlite3_*` relocation must be an imported,
  global unversioned function in a supported JUMP_SLOT or GLOB_DAT slot. ELF bounds,
  PT_LOAD alignment and actual mapping file offsets/load bias are checked.
- Exact live mapping device/inode and canonical file identity for the extension
  and the single SQLite library. Deleted mappings, anonymous or ambiguous origins,
  and additional separately mapped SQLite objects are refused. Normal system
  symlink aliases such as `/lib` are resolved to the exact canonical mapped inode;
  alias text alone is never accepted as identity.
- Every actual resolved relocation slot is read through bounded `/proc/self/mem`
  access wholly inside the extension's readable mapping. The pointer must land
  in the selected library's executable mapping, have the exact dladdr symbol
  address/name, and equal lookup through both the already-loaded extension and
  already-loaded library handles. A dlsym lookup alone would not prove the actual
  extension's call target under symbol interposition.
- NOLOAD|LAZY handles only, with balanced dlclose on success and failure. The
  verifier never loads a missing library or calls functions to resolve lazy slots.
  Selected mappings, file identities and all actual slots are rechecked.

Dynamic records require nonnull extension content hash, library content hash and
canonical library-path hash; built-in records continue requiring all three null.
The original binary/read/SQL/time budgets, allocation bounds, production allowlist,
source selector and durable qualification binding remain in force.

Current developer validation uses the existing same-host `/usr/bin/python3.13`
(Python 3.13.5, SQLite 3.46.1), not a downloaded interpreter or independent server.
Its extension has 88 observed SQLite function relocations; 88 is a measured count,
not a hardcoded acceptance criterion. A real subprocess-only LD_PRELOAD shim for
`sqlite3_open_v2` is rejected by actual relocation-origin checks. Twenty repeated
observations check balanced handles and stable file-descriptor count.

Tests create only temporary synthetic ELF/map fixtures and the self-authored
interposition shim; no native service/model or physical crash workload is launched
by the fingerprint controls. Successful fingerprinting establishes current runtime
identity, not crash recovery, power-loss durability, source authenticity, acoustic
accuracy, or deployment readiness. It is not a defense against hostile concurrent
same-process code, private executable-page modification or adversarial filesystem
races outside the existing trusted-owner threat model.
