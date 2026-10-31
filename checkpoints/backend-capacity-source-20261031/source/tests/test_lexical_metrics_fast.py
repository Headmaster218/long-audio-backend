"""Exact accelerated metrics must agree with the transparent fallback."""
import importlib.util
from pathlib import Path
import random

spec = importlib.util.spec_from_file_location('lexical_metric_test_module', Path(__file__).parents[1] / 'scripts/benchmarks/score_predictions.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def test_exact_acceleration_matches_fallback(monkeypatch):
    fast = metrics._levenshtein
    if fast is None:
        import pytest
        pytest.skip('Install benchmark extra to test accelerated implementation')
    rng = random.Random(7382)
    for _ in range(300):
        a = [rng.choice(['中', '文', 'word', 'é']) for _ in range(rng.randrange(30))]
        b = [rng.choice(['中', '文', 'word', 'é']) for _ in range(rng.randrange(30))]
        monkeypatch.setattr(metrics, '_levenshtein', fast)
        expected = metrics.edit(a, b)
        monkeypatch.setattr(metrics, '_levenshtein', None)
        assert metrics.edit(a, b) == expected


def test_lexical_counts_and_empty_reference():
    got = metrics.lexical('Hello, world! 中文', 'hello world 中')
    assert got['word_errors'] == 1
    assert got['character_errors'] == 1
    assert got['reference_words'] == 3
    empty = metrics.lexical('', 'text')
    assert empty['wer'] is None
    assert empty['word_errors'] == 1
    assert empty['character_errors'] == 4
