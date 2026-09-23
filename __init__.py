"""ComfyUI entry point for the SpanSynth-Edit custom nodes."""

WEB_DIRECTORY = "./comfyui"


async def comfy_entrypoint():
    from .spansynth.comfyui import SpanSynthExtension

    return SpanSynthExtension()
