"""Synthetic metric checks; no audio, model, network, or third-party imports."""
import unittest
import metrics as p


def ev(a, b, label='door'):
    return {'start': a, 'end': b, 'event': label}


class Metrics(unittest.TestCase):
    def test_half_open_integer_boundaries(self):
        self.assertEqual(p.segments([ev(0, 1), ev(2, 3)], 4, 'door'), {0, 2})

    def test_subsecond_overlap(self):
        self.assertEqual(p.segments([ev(.999, 1.001)], 3, 'door'), {0, 1})

    def test_duplicate_and_adjacent_support(self):
        self.assertEqual(p.segments([ev(0, 1), ev(0, 1), ev(1, 2)], 3, 'door'), {0, 1})

    def test_counts(self):
        got = p.segment_score([ev(0, 2)], [ev(1, 3)], 4, exhaustive=True)
        self.assertEqual({k: got[k] for k in ('tp', 'fp', 'fn', 'tn')},
                         {'tp': 1, 'fp': 1, 'fn': 1, 'tn': 1})
        self.assertEqual(got['f1'], .5)

    def test_weak_negatives_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Weak tags'):
            p.segment_score([], [], 120)

    def test_empty_exhaustive(self):
        got = p.segment_score([], [], 120, exhaustive=True)
        self.assertEqual(got['tn'], 120)
        self.assertIsNone(got['precision'])
        self.assertIsNone(got['recall'])
        self.assertIsNone(got['f1'])

    def test_bounds_rejected(self):
        for a, b in [(-1, 1), (0, 121), (2, 1), (1, float('nan')), (1, 1)]:
            with self.assertRaises(ValueError):
                p.segments([ev(a, b)], 120, 'door')

    def test_full_final_second(self):
        self.assertEqual(p.segments([ev(119, 120)], 120, 'door'), {119})

    def test_other_labels_do_not_mark_door(self):
        self.assertEqual(p.segments([ev(0, 120, 'drawer')], 120, 'door'), set())

    def test_weak_mapping(self):
        self.assertTrue(p.weak_hit('rain', [{'label': 'rain'}])['hit'])
        self.assertTrue(p.weak_hit('door_wood_knock', [{'label': 'door'}])['hit'])
        self.assertTrue(p.weak_hit('door_wood_creaks', [{'label': 'door'}])['hit'])
        self.assertFalse(p.weak_hit('door_wood_creaks', [])['hit'])

    def test_confuser_labels_never_authorize_negatives(self):
        for category in ('engine', 'train', 'pouring_water', 'wind', 'keyboard_typing', 'clapping'):
            got = p.weak_hit(category, [{'label': 'driving'}])
            self.assertIsNone(got['hit'])
            self.assertIsNone(got['precision'])
            self.assertFalse(got['negative_truth'])


if __name__ == '__main__':
    unittest.main()
