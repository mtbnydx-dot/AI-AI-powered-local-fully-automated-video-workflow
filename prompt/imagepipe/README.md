# imagepipe prompts

The six English templates assemble existing authored descriptions; imagepipe does not call a prompt-rewriting model or assess generated images. Placeholders are `{style}`, `{description}`, `{reference_guide}`, `{width}` and `{height}`. `reference_guide` identifies references in upload order, using `<image1>`, `<image2>`, etc. for multiple images and “the reference image” for one.

- Character masters use one clear view; wardrobe edits retain identity while replacing the requested clothing.
- Location and prop references expose the features needed for reuse. State edits preserve the underlying prop.
- First frames combine references into the authored opening scene. An environment anchor contributes the setting; the current shot determines the cast, pose, crop and action state.
- Captions and overlays are excluded without banning physical documents, inscriptions or signs required by the story. Resolution and steps remain API configuration; the current run uses standard Qwen-Image-2.1, 20 steps and 720×1280.

The approach adapts the official guidance on concrete descriptions, named image roles and preserving attributes outside an edit. It does not adopt the official enhancer's extra inference stage or invent additional story details.

Sources consulted 2026-11-01:

- [Qwen text-to-image prompt guidance](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_t2i.txt)
- [Qwen editing prompt guidance](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_edit.txt)
- [ComfyUI Qwen-Image-2.1 reference ordering and editing workflow](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1)
- [Qwen model parameters and multi-reference examples](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/README.md)
