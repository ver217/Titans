import torch
import torch.nn as nn

from colossalai.context import ParallelMode
from colossalai.nn.layer import VanillaPatchEmbedding, VanillaClassifier, \
    WrappedDropout as Dropout, WrappedDropPath as DropPath
from colossalai.nn.layer.moe import build_ffn_experts, MoeLayer, Top2Router, NormalNoiseGenerator
from .util import moe_sa_args
from ..helper import TransformerLayer
from colossalai.context.moe_context import MOE_CONTEXT

from titans.layer.attention import SelfAttentionForMoe


class Widenet(nn.Module):

    def __init__(self,
                 num_experts: int,
                 capacity_factor_train: float = 1.25,
                 capacity_factor_eval: float = 2.0,
                 drop_tks: bool = True,
                 img_size: int = 224,
                 patch_size: int = 16,
                 in_chans: int = 3,
                 num_classes: int = 1000,
                 depth: int = 12,
                 d_model: int = 768,
                 num_heads: int = 12,
                 d_kv: int = 64,
                 d_ff: int = 4096,
                 attention_drop: float = 0.,
                 drop_rate: float = 0.1,
                 drop_path: float = 0.):
        super().__init__()

        embedding = VanillaPatchEmbedding(img_size=img_size,
                                          patch_size=patch_size,
                                          in_chans=in_chans,
                                          embed_size=d_model)
        embed_dropout = Dropout(p=drop_rate, mode=ParallelMode.TENSOR)

        shared_sa = SelfAttentionForMoe(**moe_sa_args(
            d_model=d_model, n_heads=num_heads, d_kv=d_kv, attention_drop=attention_drop, drop_rate=drop_rate))

        noisy_func = NormalNoiseGenerator(num_experts)
        shared_router = Top2Router(capacity_factor_train=capacity_factor_train,
                                   capacity_factor_eval=capacity_factor_eval,
                                   noisy_func=noisy_func,
                                   drop_tks=drop_tks)
        shared_experts = build_ffn_experts(num_experts, d_model, d_ff, drop_rate=drop_rate)

        # stochastic depth decay rule
        dpr = [x.item() for x in torch.linspace(0, drop_path, depth)]
        blocks = [
            TransformerLayer(att=shared_sa,
                             ffn=MoeLayer(dim_model=d_model,
                                          num_experts=num_experts,
                                          router=shared_router,
                                          experts=shared_experts),
                             norm1=nn.LayerNorm(d_model, eps=1e-6),
                             norm2=nn.LayerNorm(d_model, eps=1e-6),
                             droppath=DropPath(p=dpr[i], mode=ParallelMode.TENSOR)) for i in range(depth)
        ]
        norm = nn.LayerNorm(d_model, eps=1e-6)
        self.linear = VanillaClassifier(in_features=d_model, num_classes=num_classes)
        nn.init.zeros_(self.linear.weight)
        nn.init.zeros_(self.linear.bias)
        self.widenet = nn.Sequential(embedding, embed_dropout, *blocks, norm)

    def forward(self, x):
        MOE_CONTEXT.reset_loss()
        x = self.widenet(x)
        x = torch.mean(x, dim=1)
        x = self.linear(x)
        return x