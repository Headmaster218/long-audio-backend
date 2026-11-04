"""Synthetic tests only: no model, corpus audio, or product imports."""
import io
import math
import unittest
import wave

import numpy as np

from diagnose_frozen import cosine, pcm, signal_metrics


class DiagnosticTests(unittest.TestCase):
    def test_digital_silence_is_not_nonfinite(self):
        m = signal_metrics(np.zeros(32000, dtype=np.int16))
        self.assertIsNone(m["rms_dbfs"])
        self.assertEqual(m["digital_zero_fraction"], 1)
        self.assertEqual(m["low_energy_fraction_lt_minus50dbfs"], 1)
        self.assertEqual(m["endpoint_sample_count"], 0)

    def test_pcm_endpoints_are_counted_without_int16_abs_overflow(self):
        x = np.tile(np.array([-32768, 32767], dtype=np.int16), 16000)
        m = signal_metrics(x)
        self.assertEqual(m["endpoint_sample_count"], 32000)
        self.assertEqual(m["near_full_scale_fraction"], 1)
        self.assertEqual(m["low_energy_fraction_lt_minus50dbfs"], 0)

    def test_frame_threshold_tail_and_constant_level(self):
        x = np.full(321, 327, dtype=np.int16)
        m = signal_metrics(x)
        self.assertEqual(m["complete_20ms_frames"], 1)
        self.assertEqual(m["excluded_tail_samples_for_frames"], 1)
        self.assertAlmostEqual(m["rms_dbfs"], 20 * math.log10(327/32768))
        self.assertEqual(m["low_energy_fraction_lt_p95_minus30db"], 0)

    def test_stereo_pcm_preserves_channel_zero(self):
        x = np.arange(640, dtype=np.int16).reshape(320, 2)
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(x.astype("<i2").tobytes())
        self.assertTrue(np.array_equal(pcm(b.getvalue()), x))

    def test_cosine_geometry_and_rejection(self):
        x, y = np.zeros(512), np.zeros(512)
        x[0], y[1] = 1, 1
        self.assertEqual(cosine(x, x * 2), 1)
        self.assertEqual(cosine(x, -x), -1)
        self.assertEqual(cosine(x, y), 0)
        with self.assertRaises(ValueError):
            cosine(x, np.zeros(512))


if __name__ == "__main__":
    unittest.main()
