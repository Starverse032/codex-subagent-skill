# Image generation and verification

Use `--gen-image` when the task requires Codex-generated images. Install the optional image dependencies from the [setup guide](host-integration.md#optional-dependencies). Attach reference images with repeatable `--image FILE`.

- Set the expected count with `--image-count` and the composition with `--image-size`. Set the expected encoding with `--image-format`.
- The run's `artifacts/` directory holds the generated deliverables. `--image-dir` optionally copies verified images to an export directory, preserving existing files.
- The verifier decodes PNG or JPEG files, checks the count of distinct images and matches hashes to saved outputs reported by completed image-generation tool events. A viewed input reference supplies no generation evidence.
- The backend must report a completed generation item with the saved path for provenance to be verified. If a runtime omits this evidence, inspect diagnostics before choosing another supported runtime for a new submission.
- Open each image before confirming its visual content. Decoding and hash matches establish file validity and provenance.

See [task specs and receipts](events-schema.md) for the relationship between acceptance commands and structured output checks.
