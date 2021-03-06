# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Sequence, Union

import numpy as np
import torch.nn as nn

from monai.networks.layers.convutils import same_padding
from monai.networks.layers.factories import Act, Conv, Dropout, Norm, split_args


class Convolution(nn.Sequential):
    """
    Constructs a convolution with normalization, optional dropout, and optional activation layers::

        -- (Conv|ConvTrans) -- Norm -- (Dropout) -- (Acti) --

    if ``conv_only`` set to ``True``::

        -- (Conv|ConvTrans) --

    Args:
        dimensions: number of spatial dimensions.
        in_channels: number of input channels.
        out_channels: number of output channels.
        strides: convolution stride. Defaults to 1.
        kernel_size: convolution kernel size. Defaults to 3.
        act: activation type and arguments. Defaults to PReLU.
        norm: feature normalization type and arguments. Defaults to instance norm.
        dropout: dropout ratio. Defaults to no dropout.
        dilation: dilation rate. Defaults to 1.
        bias: whether to have a bias term. Defaults to True.
        conv_only:  whether to use the convolutional layer only. Defaults to False.
        is_transposed: if True uses ConvTrans instead of Conv. Defaults to False.

    See also:

        :py:class:`monai.networks.layers.Conv`
        :py:class:`monai.networks.layers.Dropout`
        :py:class:`monai.networks.layers.Act`
        :py:class:`monai.networks.layers.Norm`
        :py:class:`monai.networks.layers.split_args`

    """

    def __init__(
        self,
        dimensions: int,
        in_channels: int,
        out_channels: int,
        strides: int = 1,
        kernel_size: Union[Sequence[int], int] = 3,
        act=Act.PRELU,
        norm=Norm.INSTANCE,
        dropout=None,
        dilation: Union[Sequence[int], int] = 1,
        bias: bool = True,
        conv_only: bool = False,
        is_transposed: bool = False,
    ) -> None:
        super().__init__()
        self.dimensions = dimensions
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.is_transposed = is_transposed

        padding = same_padding(kernel_size, dilation)
        conv_type = Conv[Conv.CONVTRANS if is_transposed else Conv.CONV, dimensions]

        # define the normalisation type and the arguments to the constructor
        norm_name, norm_args = split_args(norm)
        norm_type = Norm[norm_name, dimensions]

        # define the activation type and the arguments to the constructor
        if act is not None:
            act_name, act_args = split_args(act)
            act_type = Act[act_name]
        else:
            act_type = act_args = None

        if dropout:
            # if dropout was specified simply as a p value, use default name and make a keyword map with the value
            if isinstance(dropout, (int, float)):
                drop_name = Dropout.DROPOUT
                drop_args = {"p": dropout}
            else:
                drop_name, drop_args = split_args(dropout)

            drop_type = Dropout[drop_name, dimensions]

        if is_transposed:
            conv = conv_type(in_channels, out_channels, kernel_size, strides, padding, strides - 1, 1, bias, dilation)
        else:
            conv = conv_type(in_channels, out_channels, kernel_size, strides, padding, dilation, bias=bias)

        self.add_module("conv", conv)

        if not conv_only:
            self.add_module("norm", norm_type(out_channels, **norm_args))
            if dropout:
                self.add_module("dropout", drop_type(**drop_args))
            if act is not None:
                self.add_module("act", act_type(**act_args))


class ResidualUnit(nn.Module):
    """
    Residual module with multiple convolutions and a residual connection.

    See also:

        :py:class:`monai.networks.blocks.Convolution`

    """

    def __init__(
        self,
        dimensions: int,
        in_channels: int,
        out_channels: int,
        strides: int = 1,
        kernel_size: Union[Sequence[int], int] = 3,
        subunits: int = 2,
        act=Act.PRELU,
        norm=Norm.INSTANCE,
        dropout=None,
        dilation: Union[Sequence[int], int] = 1,
        bias: bool = True,
        last_conv_only: bool = False,
    ) -> None:
        super().__init__()
        self.dimensions = dimensions
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.conv = nn.Sequential()
        self.residual = nn.Identity()

        padding = same_padding(kernel_size, dilation)
        schannels = in_channels
        sstrides = strides
        subunits = max(1, subunits)

        for su in range(subunits):
            conv_only = last_conv_only and su == (subunits - 1)
            unit = Convolution(
                dimensions,
                schannels,
                out_channels,
                sstrides,
                kernel_size,
                act,
                norm,
                dropout,
                dilation,
                bias,
                conv_only,
            )

            self.conv.add_module(f"unit{su:d}", unit)

            # after first loop set channels and strides to what they should be for subsequent units
            schannels = out_channels
            sstrides = 1

        # apply convolution to input to change number of output channels and size to match that coming from self.conv
        if np.prod(strides) != 1 or in_channels != out_channels:
            rkernel_size = kernel_size
            rpadding = padding

            if np.prod(strides) == 1:  # if only adapting number of channels a 1x1 kernel is used with no padding
                rkernel_size = 1
                rpadding = 0

            conv_type = Conv[Conv.CONV, dimensions]
            self.residual = conv_type(in_channels, out_channels, rkernel_size, strides, rpadding, bias=bias)

    def forward(self, x):
        res = self.residual(x)  # create the additive residual from x
        cx = self.conv(x)  # apply x to sequence of operations
        return cx + res  # add the residual to the output
