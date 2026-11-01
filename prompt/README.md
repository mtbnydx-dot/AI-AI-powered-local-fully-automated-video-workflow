# Prompt sources

All application-owned model instructions live in the UTF-8 `.txt` files here:

- `review_system.txt`: visual rubric, evidence limits and the existing review JSON contract.
- `review_user.txt`, `review_reference.txt`, `review_frame.txt`: request layout and image labels. Python fills the named `{placeholders}` once.
- `transcript_compare_system.txt`: advisory comparison of script dialogue with ASR segments. The user message contains the actual script and segment data as JSON.
- `product/*.txt`: existing product-example generation prompts, read by `scripts/create_example.py`.
- `h3_legacy_negative.txt`: the existing legacy input conversion, not a native H3 negative-conditioning channel. New H3 packages should omit `negative_prompt`.
- `deploy_b200_agent.txt`: deployment handoff instructions for the Tide bundle; not sent to production models.

Shot-specific prompts, dialogue and acceptance criteria supplied in a production package remain input data in `package.json`; the loader currently requires inline `shots[].prompt`. Moving Tide's creative content or adding prompt-file fields is a separate package-format change. Generated request records retain the fully assembled prompts.

The visual rubric follows the clear criteria and evidence-based grading guidance in [Anthropic's evaluation guide](https://www.anthropic.com/research/demystifying-evals-for-ai-agents), adapted to this application's sampled-frame input and permissive retry policy. H3 authoring conventions come from [MiniMax's official base prompt guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md) and [ComfyUI's H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3). Prompt quality has not been evaluated in this change.
