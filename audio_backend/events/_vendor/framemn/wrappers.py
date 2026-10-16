"""Fixed inference-only wrapper adaptations from upstream MIT source.

Only the no-sequence-model linear-head branch is retained. Module names and
state keys are unchanged. All checkpoint I/O belongs to the strict adapter.
"""
import torch
import torch.nn as nn
from .preprocess import AugmentMelSTFT
from .model import get_model

class FrameMNWrapper(nn.Module):
    def __init__(self, width_mult=1.0) -> None:
        super().__init__()
        self.mel = AugmentMelSTFT(
            n_mels=128,
            sr=16_000,
            win_length=400,
            hopsize=160,
            n_fft=512,
            freqm=0,
            timem=0,
            htk=False,
            fmin=0.0,
            fmax=None,
            norm=1,
            fmin_aug_range=10,
            fmax_aug_range=2000,
            fast_norm=True,
            preamp=True,
            padding="center",
            periodic_window=False,
        )

        self.frame_mn = get_model(
            width_mult=width_mult
        )

    def mel_forward(self, x):
        return self.mel(x)

    def forward(self, x):
        return self.frame_mn(x)



class PredictionsWrapper(nn.Module):
    def __init__(self, base_model, embed_dim):
        super().__init__()
        self.model = base_model
        self.seq_len = 250
        self.embed_dim = embed_dim
        self.n_classes_strong = 447
        self.n_classes_weak = 447
        self.seq_model = nn.Identity()
        self.strong_head = nn.Linear(embed_dim, 447)
        self.weak_head = nn.Linear(embed_dim, 447)

    def mel_forward(self, x):
        return self.model.mel_forward(x)

    def forward(self, x):
        x = self.model(x)
        if x.ndim != 3:
            raise ValueError("framemn_embedding_shape")
        if x.size(-2) > self.seq_len:
            x = nn.functional.adaptive_avg_pool1d(x.transpose(1, 2), self.seq_len).transpose(1, 2)
        elif x.size(-2) < self.seq_len:
            x = nn.functional.interpolate(x.transpose(1, 2), size=self.seq_len,
                                          mode="linear").transpose(1, 2)
        x = self.seq_model(x)
        strong = self.strong_head(x)
        weak = self.weak_head(x.mean(dim=1))
        return strong.transpose(1, 2), weak
