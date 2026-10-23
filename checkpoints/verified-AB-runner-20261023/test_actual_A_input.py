"""ROOT ONLY: finite real input byte control, no models/SQL/service."""
import io
from pathlib import Path
import time
import wave

from a_input import AInput, plan, START


def test_all_eight_fixed_pieces_match_original_pcm_exactly():
    path = Path('/workspace/scratch/12ec3121e93f/ami-recovery-20261023b/EN2002a.Mix-Headset.wav')
    total, count = 0, 0
    with AInput(path, time.monotonic()+30) as source, wave.open(str(path), 'rb') as original:
        for page in plan():
            for part in page['sources']:
                encoded = source.piece(part)
                with wave.open(io.BytesIO(encoded), 'rb') as item:
                    assert (item.getframerate(), item.getnchannels(), item.getsampwidth(), item.getnframes()) == (16000, 1, 2, part['frames'])
                    pcm = item.readframes(part['frames']+1)
                original.setpos(START+part['offset'])
                assert pcm == original.readframes(part['frames'])
                assert len(pcm) == part['frames']*2
                total += part['frames']; count += 1
    assert total == 11520000 and count == 8
