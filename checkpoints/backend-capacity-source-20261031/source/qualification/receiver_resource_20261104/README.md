# Bounded receiver resource study

This source-only harness exercises the production ReceiverCompletion owned-writer,
publication and full-inventory APIs. It creates synthetic PCM, not microphone
recordings or recognition results. It never connects to a server or starts models.

Use an empty private output directory outside the repository. Keep its raw/ready
files and JSONL observations private; they are runtime evidence, not source backup.
Set RESOURCE_TRENDS_SOURCE to the restored checkpoint source root and
RESOURCE_TRENDS_OUTPUT to that output directory. Use Python 3.12 with `-B`.
Run `receiver_resource_workload.py 0 128`, wait for successful process exit, then
run `receiver_resource_workload.py 128 256` with the same environment.

The two processes create 256 segments of 120 seconds, PCM16LE/16 kHz/mono.
This represents 8 h 32 min of synthetic audio bytes, not elapsed uptime or ASR.
The second process checks retained admission, refuses attempt 257 under unchanged
production bounds, and repeats full inventory 32 times at a fixed retained size.
Each successful segment records publication/inventory duration, RSS and open-file
count. A separate thread samples RSS, filesystem allocation and free disk each
second. Sampling is best effort, not a kernel resource quota; measured latency
includes this observer's overhead and competing cloud activity.

Safety checks stop between operations at 2 GiB process RSS, 6 GiB output allocation,
10 GiB free disk, or 30 minutes per invocation. Production per-operation and file
limits remain unchanged. Failed outputs are preserved. Do not reuse a failed output
as a clean run, silently discard failed results, or change limits to claim success.

The generator's producer metadata is a synthetic valid v1 declaration, not genuine
Android capture provenance. Results do not qualify FTPS, server authentication,
intake, inference, acoustic accuracy, stable speaker identity, power-loss recovery,
physical-phone behavior, or unattended day-long retention. A successful completed
process restart is not interruption of an active upload. Fixed-size repeated reads
only qualify the sampled bounded observation window, not an indefinite leak test.
