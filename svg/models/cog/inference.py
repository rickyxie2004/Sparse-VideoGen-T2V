import torch
import torch.nn.functional as F

from diffusers.models.attention_processor import Attention
from diffusers.utils import export_to_video, load_image

from .attention import CogVideoX_SparseAttn_Processor2_0, prepare_flexattention
from .utils import sparsity_to_width, get_attention_mask
from .custom_models import replace_sparse_forward

import os


def sample_image(pipe, prompt, image_path, output_path, seed, version, num_step=50):
    print("\n" * 5)
    print(f"Prompt: {prompt}")

    image = load_image(image_path)
    print(f"Image Is Ready. Seed is {seed}")

    txt_path = "/home/xieruiqi/diffuser-dev/examples/cogvideox1.5/prompts_8.txt"
    output_dir = './SVG_0.25'
    os.makedirs(output_dir, exist_ok=True)

    with open(txt_path, 'r', encoding='utf-8') as f:
        prompts = [line.strip() for line in f if line.strip()]

    for i, prompt in enumerate(prompts):
        output_path = f"{output_dir}/output_{i}.mp4"
        
        if version == "v1.5":
            video = pipe(
                prompt=prompt, num_videos_per_prompt=1, num_inference_steps=num_step, num_frames=81, guidance_scale=6,
                height=480, width=720
            ).frames[0]
            
        export_to_video(video, output_path, fps=8)
        print(f"Video saved: {output_path}")
    
    # if version == "v1":
    #     video = pipe(
    #         image=image, prompt=prompt, guidance_scale=6, use_dynamic_cfg=True, num_inference_steps=num_step
    #     ).frames[0]
    # elif version == "v1.5":
    #     video = pipe(
    #         prompt=prompt, num_videos_per_prompt=1, num_inference_steps=num_step, num_frames=81, guidance_scale=6,
    #         height=480, width=720
    #     ).frames[0]

    # export_to_video(video, output_path, fps=8)


def replace_cog_attention(pipe, version, num_sampled_rows, sparsity, first_layers_fp, first_times_fp):

    masks = ["spatial", "temporal"]

    # For FlexAttention
    if version == "v1":
        context_length = 226
        num_frame = 13
        frame_size = 1350
    elif version == "v1.5":
        context_length = 226
        num_frame = 11
        frame_size = 1350
    else:
        raise ValueError(f"Unsupported version: {version}")
    
    dtype = torch.bfloat16

    AttnModule = CogVideoX_SparseAttn_Processor2_0
    AttnModule.num_sampled_rows = num_sampled_rows
    AttnModule.attention_masks = [get_attention_mask(mask_name, context_length, num_frame, frame_size) for mask_name in masks]
    AttnModule.version = version
    AttnModule.first_layers_fp = first_layers_fp
    AttnModule.first_times_fp = first_times_fp

    multiplier = diag_width = sparsity_to_width(sparsity, context_length, num_frame, frame_size)

    AttnModule.context_length = context_length
    AttnModule.num_frame = num_frame
    AttnModule.frame_size = frame_size
    
    # NOTE: ??? Prepare placement will strongly decrease PSNR
    # prepare_placement(2, 48, 64, dtype, "cuda", context_length, num_frame, frame_size)
    block_mask = prepare_flexattention(2, 48, 64, dtype, "cuda", context_length, num_frame, frame_size, diag_width, multiplier)
    AttnModule.block_mask = block_mask
    
    replace_sparse_forward()
    
    num_layers = len(pipe.transformer.transformer_blocks)

    for layer_idx, m in enumerate(pipe.transformer.transformer_blocks):
        m.attn1.processor.layer_idx = layer_idx
        
    for _ , m in pipe.transformer.named_modules():
        if isinstance(m, Attention):
            layer_idx = m.processor.layer_idx
            m.set_processor(AttnModule(layer_idx))
            m.processor.num_layers = num_layers
