"""Adapted offline FrameMN architecture; upstream MIT notice in LICENSE.

Removed every alternate checkpoint, pretrained download, and diagnostic print path.
"""
from functools import partial
from typing import Any, Callable, List, Optional, Sequence, Tuple
import torch
from torch import nn, Tensor
from torchvision.ops.misc import ConvNormActivation
from .block_types import InvertedResidualConfig, InvertedResidual
from .utils import cnn_out_size

class MN(nn.Module):
    def __init__(
            self,
            inverted_residual_setting: List[InvertedResidualConfig],
            block: Optional[Callable[..., nn.Module]] = None,
            norm_layer: Optional[Callable[..., nn.Module]] = None,
            in_conv_kernel: int = 3,
            in_conv_stride: int = 2,
            in_channels: int = 1,
            **kwargs: Any,
    ) -> None:
        """
        MobileNet V3 main class

        Args:
            inverted_residual_setting (List[InvertedResidualConfig]): Network structure
            block (Optional[Callable[..., nn.Module]]): Module specifying inverted residual building block for models
            norm_layer (Optional[Callable[..., nn.Module]]): Module specifying the normalization layer to use
            in_conv_kernel (int): Size of kernel for first convolution
            in_conv_stride (int): Size of stride for first convolution
            in_channels (int): Number of input channels
        """
        super(MN, self).__init__()

        if not inverted_residual_setting:
            raise ValueError("The inverted_residual_setting should not be empty")
        elif not (
                isinstance(inverted_residual_setting, Sequence)
                and all([isinstance(s, InvertedResidualConfig) for s in inverted_residual_setting])
        ):
            raise TypeError("The inverted_residual_setting should be List[InvertedResidualConfig]")

        if block is None:
            block = InvertedResidual

        depthwise_norm_layer = norm_layer = \
            norm_layer if norm_layer is not None else partial(nn.BatchNorm2d, eps=0.001, momentum=0.01)

        layers: List[nn.Module] = []

        kernel_sizes = [in_conv_kernel]
        strides = [in_conv_stride]

        # building first layer
        firstconv_output_channels = inverted_residual_setting[0].input_channels
        layers.append(
            ConvNormActivation(
                in_channels,
                firstconv_output_channels,
                kernel_size=in_conv_kernel,
                stride=in_conv_stride,
                norm_layer=norm_layer,
                activation_layer=nn.Hardswish,
            )
        )

        # get squeeze excitation config
        se_cnf = kwargs.get('se_conf', None)

        # building inverted residual blocks
        # - keep track of size of frequency and time dimensions for possible application of Squeeze-and-Excitation
        # on the frequency/time dimension
        # - applying Squeeze-and-Excitation on the time dimension is not recommended as this constrains the network to
        # a particular length of the audio clip, whereas Squeeze-and-Excitation on the frequency bands is fine,
        # as the number of frequency bands is usually not changing
        f_dim, t_dim = kwargs.get('input_dims', (128, 1000))
        # take into account first conv layer
        f_dim = cnn_out_size(f_dim, 1, 1, 3, 2)
        t_dim = cnn_out_size(t_dim, 1, 1, 3, 2)
        for cnf in inverted_residual_setting:
            f_dim = cnf.out_size(f_dim, idx=0)
            t_dim = cnf.out_size(t_dim, idx=1)
            cnf.f_dim, cnf.t_dim = f_dim, t_dim  # update dimensions in block config
            layers.append(block(cnf, se_cnf, norm_layer, depthwise_norm_layer))
            kernel_sizes.append(cnf.kernel)
            strides.append(cnf.stride)

        # building last several layers
        lastconv_input_channels = inverted_residual_setting[-1].out_channels
        lastconv_output_channels = 6 * lastconv_input_channels
        self.lastconv_output_channels = lastconv_output_channels
        layers.append(
            ConvNormActivation(
                lastconv_input_channels,
                lastconv_output_channels,
                kernel_size=1,
                norm_layer=norm_layer,
                activation_layer=nn.Hardswish,
            )
        )

        self.features = nn.Sequential(*layers)

        # no prediction head needed - we want to use Frame-MobileNet to extract a 3D sequence
        #  i.e.: batch size x sequence length x channel dimension

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm, nn.LayerNorm)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def _forward_impl(self, x: Tensor, return_fmaps: bool = False) -> Tensor:
        fmaps = []

        for i, layer in enumerate(self.features):
            x = layer(x)
            if return_fmaps:
                fmaps.append(x)

        # reshape: batch size x channels x frequency bands x time -> batch size x time x channels
        #  works, because frequency dimension is exactly 1
        x = x.squeeze(2).permute(0, 2, 1)
        return x

    def forward(self, x: Tensor) -> Tensor:
        return self._forward_impl(x)


def _mobilenet_v3_conf(
        width_mult: float = 1.0,
        reduced_tail: bool = False,
        dilated: bool = False,
        strides: Tuple[int] = None,
        dilation_list_t_dim: Optional[List[int]] = None,
        **kwargs
):
    reduce_divider = 2 if reduced_tail else 1
    if dilation_list_t_dim is None:
        dilation_list_t_dim = [1] * 15
    if dilated:
        dilation_list_t_dim[-3:] = [2] * 3


    bneck_conf = partial(InvertedResidualConfig, width_mult=width_mult)
    adjust_channels = partial(InvertedResidualConfig.adjust_channels, width_mult=width_mult)

    if strides is None:
        #            0  1  2  3  4  5  6  7  8  9 10 11 12 13 14
        f_strides = (1, 2, 2, 1, 1, 2, 1, 2, 1, 1, 2, 1, 1, 1, 2)
        t_strides = (1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)

        strides = tuple(zip(f_strides, t_strides))

    # InvertedResidualConfig:
    # input_channels, kernel, expanded_channels, out_channels, use_se, activation, stride, dilation
    inverted_residual_setting = [
        bneck_conf(16, 3, 16, 16, False, "RE", strides[0], (1, dilation_list_t_dim[0])),  # 0
        bneck_conf(16, 3, 64, 24, False, "RE", strides[1], (1, dilation_list_t_dim[1])),  # 1 - C1
        bneck_conf(24, 3, 72, 24, False, "RE", strides[2], (1, dilation_list_t_dim[2])),  # 2
        bneck_conf(24, 5, 72, 40, True, "RE", strides[3], (1, dilation_list_t_dim[3])),  # 3 - C2
        bneck_conf(40, 5, 120, 40, True, "RE", strides[4], (1, dilation_list_t_dim[4])),  # 4
        bneck_conf(40, 5, 120, 40, True, "RE", strides[5], (1, dilation_list_t_dim[5])),  # 5
        bneck_conf(40, 3, 240, 80, False, "HS", strides[6], (1, dilation_list_t_dim[6])),  # 6 - C3
        bneck_conf(80, 3, 200, 80, False, "HS", strides[7], (1, dilation_list_t_dim[7])),  # 7
        bneck_conf(80, 3, 184, 80, False, "HS", strides[8], (1, dilation_list_t_dim[8])),  # 8
        bneck_conf(80, 3, 184, 80, False, "HS", strides[9], (1, dilation_list_t_dim[9])),  # 9
        bneck_conf(80, 3, 480, 112, True, "HS", strides[10], (1, dilation_list_t_dim[10])),  # 10
        bneck_conf(112, 3, 672, 112, True, "HS", strides[11], (1, dilation_list_t_dim[11])),  # 11
        bneck_conf(112, 5, 672, 160 // reduce_divider, True, "HS", strides[12], (1, dilation_list_t_dim[12])),
        # 12 - C4 # dilation
        bneck_conf(160 // reduce_divider, 5, 960 // reduce_divider, 160 // reduce_divider, True, "HS", strides[13],
                   (1, dilation_list_t_dim[13])),  # 13  # dilation
        bneck_conf(160 // reduce_divider, 5, 960 // reduce_divider, 160 // reduce_divider, True, "HS", strides[14],
                   (1, dilation_list_t_dim[14])),  # 14  # dilation
    ]
    last_channel = adjust_channels(1280 // reduce_divider)

    return inverted_residual_setting, last_channel


def get_model(width_mult=1.0):
    if width_mult != 1.0:
        raise ValueError("framemn_fixed_width")
    settings, _ = _mobilenet_v3_conf(width_mult=1.0)
    return MN(settings, input_dims=(128, 1000),
              se_conf={"se_dims": [1], "se_agg": "max", "se_r": 4})
