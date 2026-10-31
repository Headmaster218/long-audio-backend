"""Model-free budget arithmetic for one pinned MOSS GGUF/runtime only.
Not a general model capability claim or production profile override.
"""
def budget(frames, n_ctx=2048):
    if type(frames) is not int or not 0 < frames <= 731648:
        raise ValueError('isolated diagnostic bound is 45.728 seconds')
    audio=(frames+1279)//1280
    markers=sum(len(str(s)) for s in range(5,int(audio/12.5)+1,5))
    prompt=86+audio+markers
    generation=min(n_ctx-prompt,max(256,2*audio+128))
    if n_ctx>131072 or prompt+256>n_ctx:raise ValueError('context insufficient')
    return dict(frames=frames,audio_tokens=audio,marker_tokens=markers,prompt_tokens=prompt,n_ctx=n_ctx,generation_budget=generation,total_budget=prompt+generation,eos_observation='not exposed',generated_token_count=None)
