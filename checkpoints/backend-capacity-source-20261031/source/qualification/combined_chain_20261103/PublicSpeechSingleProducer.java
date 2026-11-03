package io.github.headmaster218.recorder.core;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Pinned public PCM replay through unchanged production writer, spool and envelope.
 * Observation timestamps are synthetic; this is not microphone/device evidence. */
public final class PublicSpeechSingleProducer {
    private static int checks;
    private static void need(boolean value) {
        checks++;
        if (!value) throw new AssertionError("check " + checks);
    }
    private static String hash(byte[] value) {
        return PcmSegmentWriter.hex(PcmSegmentWriter.sha256().digest(value));
    }
    public static void main(String[] args) throws Exception {
        if (args.length < 3 || args.length > 4) throw new IllegalArgumentException("fresh output, raw PCM, SHA-256, optional terminal reason required");
        String terminal = args.length == 4 ? args[3] : "STOP";
        need(terminal.equals("STOP"));
        Path input = Paths.get(args[1]);
        need(!Files.isSymbolicLink(input) && Files.isRegularFile(input));
        need(Files.size(input) == 9 * 32000);
        byte[] publicPcm = Files.readAllBytes(input);
        need(hash(publicPcm).equals(args[2]));
        Path root = Files.createDirectory(Paths.get(args[0]));
        Path spool = Files.createDirectory(root.resolve("spool"));
        Path deliveries = Files.createDirectory(root.resolve("deliveries"));
        String capture = "host-java-public-nine-second-wire-span", run = "host-java-public-replay-run";
        CaptureTimeline.Epoch epoch = new CaptureTimeline.Epoch(capture, run, 7,
            PcmFormat.DEFAULT, "public-ami-replay-fixture", "public-ami-replay-fixture",
            CaptureTimeline.Gap.START);
        // Generate a genuine nine-second span at host replay origin zero; public source coordinates remain separately bound.
        try (DirectorySpool store = new DirectorySpool(spool, new CachePolicy(4000000, 0),
                new DirectorySpool.NioProtocol(), (op, path) -> {})) {
            PcmSegmentWriter writer = PcmSegmentWriter.terminalAware(store, epoch, 144000, 0, p -> {});
            byte[] pcm = new byte[32000];
            for (int second = 0; second < 9; second++) {
                System.arraycopy(publicPcm, second * 32000, pcm, 0, 32000);
                long start = second * 16000L, ns = 1 + second * 1000000000L;
                CaptureAnchors.Read read = new CaptureAnchors.Read(
                    "00000000-0000-0000-0000-000000000009", run, second, start, start + 16000,
                    ns, ns + 1, ns + 2, 1700000000000L + second * 1000L, ns + 3,
                    second * 1000L, 0, 0, CaptureAnchors.Timestamp.notPolled(),
                    second == 0 ? CaptureAnchors.RUN_START : 0);
                writer.append(pcm, 0, pcm.length, read);
            }
            // Call the real terminal API after the last full segment. Never
            // manufacture or rewrite a close reason in serialized metadata.
            if (terminal.equals("STOP")) writer.stop();
            else writer.interrupt();
        }
        CommittedSegments catalog = new CommittedSegments(spool);
        CommittedSegments.Page page = catalog.page(null);
        need(page.rejected == 0 && !page.hasMore && page.entries.size() == 1);
        List<CommittedSegments.Entry> entries = new ArrayList<>(page.entries);
        entries.sort(Comparator.comparingLong(entry -> entry.metadata.intent.sequence));
        for (int sequence = 0; sequence < 1; sequence++) {
            CommittedSegments.Entry entry = entries.get(sequence);
            catalog.verify(entry, () -> false);
            SegmentMetadata m = entry.metadata;
            need(m.schemaVersion == 2);
            need(m.intent.sequence == sequence && m.intent.frameStart == sequence * 16000L);
            need(m.intent.epoch.number == 7 && m.frameCount == 144000 && m.intent.targetFrames == 144000);
            need(m.closeReason == CaptureTimeline.Close.valueOf(terminal));
            need(m.trustedPreviousSeam == (sequence > 0));
            need(m.captureAnchors.runSampleStart == sequence * 16000L);
            need(m.captureAnchors.runSampleEnd == 144000L);
            String revision = "host-java-public-speech-span-v1";
            String binding = hash((entry.id + "\n" + m.wavSha256 + "\n" + revision).getBytes(StandardCharsets.UTF_8));
            String attempt = "r-" + binding + "-00000000000000000000000000000009";
            FtpsDelivery envelope = FtpsDelivery.encode(entry, revision, attempt);
            Path destination = Files.createDirectory(deliveries.resolve(String.format(java.util.Locale.ROOT, "%03d", sequence)));
            Files.copy(spool.resolve(entry.id).resolve("audio.wav"), destination.resolve("audio.wav"));
            Files.write(destination.resolve("segment.v1.bin"), envelope.metadataBytes());
            Files.write(destination.resolve("delivery.v1.txt"), envelope.markerBytes());
            catalog.unchanged(entry);
            System.out.println("DELIVERY\t" + sequence + "\t" + destination.toAbsolutePath());
        }
        System.out.println("PASS " + checks + " assertions; 1 produced, 1 selected; public PCM with synthetic timing only");
    }
}
