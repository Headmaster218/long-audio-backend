# Isolated capacity resume CLI portability

The standalone public source checkpoint b33172a reproduced a direct CLI failure:
`python -I -B /absolute/path/tools/capacity_resume.py --help` raised
`ModuleNotFoundError: No module named 'capacity_run'` before argument parsing.
Python isolated mode does not add the script directory to the import path.

The harness now adds its own resolved tools directory before importing
`capacity_run`, matching the existing fresh-run harness convention. The same
isolated command works from a directory outside the repository. No runpy launcher
is required. This changes imports only: no product module, budget, retry policy,
clean-pause proof, receipt, or recovery transition is changed.

Tests launch the actual isolated CLI with --help and an invalid option from an
unrelated cwd, with a hostile same-name module in cwd/PYTHONPATH. They also evaluate
only the actual child Popen expression with a recording constructor, checking the
service command, repository cwd, inherited lock FD, and new-session setting.
That last check is a narrow launch-contract test, not an executed service or
simulated Store recovery. No test invokes a model or opens a runtime database.

The checkpoint does not include or reconstruct lost capacity-run data or private
runtime receipts. CLI success is not a capacity, accuracy, or crash-recovery pass.

Selected validation: all capacity harness test files, including the three new
CLI/launch checks, passed: 148 tests. This is a model-free selected suite, not the
full repository suite. Existing runtime assets were not executed or uploaded.
