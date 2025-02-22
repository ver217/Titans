import torch
from torch import nn as nn, Tensor
from torch import dtype
from typing import Callable
from colossalai import nn as col_nn
from colossalai.core import global_context as gpc
from colossalai.utils.activation_checkpoint import checkpoint
from colossalai.nn.layer.utils import CheckpointModule
from colossalai.nn.layer.base_layer import ParallelLayer
from colossalai import kernel
from titans.decorator import support_tp_pp_only
from titans.layer.attention import GPTSelfAttention
from titans.layer.mlp import TransformerMLP


@support_tp_pp_only()
class DeepNetBlock(CheckpointModule):

    def __init__(self,
                 dim: int,
                 num_heads: int,
                 mlp_ratio: float,
                 activation: Callable,
                 attention_dropout: float = 0.,
                 dropout: float = 0.,
                 alpha: float = 1.0,
                 layernorm_epsilon: float = 1e-5,
                 dtype: dtype = None,
                 bias: bool = True,
                 fuse_scale_mask_softmax: bool = False,
                 checkpoint: bool = False,
                 activation_offload: bool = False):
        super().__init__(checkpoint, activation_offload)
        self.norm1 = col_nn.LayerNorm(normalized_shape=dim, eps=layernorm_epsilon, dtype=dtype)
        self.attn = GPTSelfAttention(dim=dim,
                                     num_heads=num_heads,
                                     attention_dropout=attention_dropout,
                                     dropout=dropout,
                                     bias=bias,
                                     fuse_scale_mask_softmax=fuse_scale_mask_softmax,
                                     dtype=dtype)
        self.alpha = alpha
        self.norm2 = col_nn.LayerNorm(normalized_shape=dim, eps=layernorm_epsilon, dtype=dtype)
        self.mlp = TransformerMLP(hidden_size=dim,
                                  mlp_ratio=mlp_ratio,
                                  act_func=activation,
                                  dropout_prob=dropout,
                                  dtype=dtype,
                                  bias=bias)

    def _forward(self, x, attention_mask=None):
        if attention_mask is not None and attention_mask.dtype != x.dtype:
            attention_mask = attention_mask.to(x.dtype)

        residual = x
        x = residual * self.alpha + self.attn(x, attention_mask)
        x = self.norm1(x)

        residual = x
        x = residual * self.alpha + self.mlp(x)
        x = self.norm2(x)

        return x, attention_mask
