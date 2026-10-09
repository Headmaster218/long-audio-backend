"""CPU window-level event tagging. Scores are uncalibrated; bounds are support windows."""
from .detector import detect_events, category, postprocess, window_starts
__all__ = ['detect_events', 'category', 'postprocess', 'window_starts']
