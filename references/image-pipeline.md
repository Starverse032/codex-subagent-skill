# Image generation and verification

Use `--gen-image` or `--preset image` for a task that needs Codex-generated images alongside other work. A standalone image request should use a direct image tool.

Input attachments use `--image`. Generated deliverables use this run's `artifacts/` directory. Optional `--image-dir` exports verified images additively; it never cleans old files or replaces a collision.

The runner accepts paths from declared artifacts inside the owned directory. It also copies paths explicitly emitted by completed image-generation tool items. It does not regex-scan conversation text, pick the newest global image, or treat a viewed reference image as generation evidence.

Verification requires:

1. PNG/JPEG decoding with Pillow, including loading pixel data.
2. An exact count of distinct image hashes matching the requested count.
3. A hash match against a completed generation tool output.

The output hash is captured on the completed tool event and checked after caller acceptance commands. Replacing that file later does not count as generation evidence for the replacement. Collection uses exclusive creation and a hash suffix when a filename is occupied by different bytes; existing files are retained.

Different composition dimensions produce a warning. A missing generation event means the provenance cannot be verified; the runner fails verification instead of claiming success. CLI builds that do not expose generation tool outputs may therefore require the SDK backend. This does not justify switching generators automatically.

`images[].valid` means the file decoded. `confidence=high` means generation provenance matched. `visual_review=required` means the host must still open the image to assess its content. A successful automated check never proves the visual request was satisfied.

This release's image validation is covered by local fixture tests. A real generation call is only claimed when a release report explicitly records it.
