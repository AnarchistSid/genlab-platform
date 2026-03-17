# Optimization Rules for Claude Code

## Context Management
- Use `rg` / `fd` to locate files before reading — never scan entire directories
- Read only the sections of files you need (use line offsets for large files)
- Checkpoint intermediate results to `.tmp/runs/<run_id>/` — don't hold state in chat
- When context gets large, summarize and restart from checkpointed artifacts

## Execution Efficiency
- Always check `.tmp/cache/` before making external requests
- Batch related operations — don't make one API call per item if batch is possible
- Prefer Python scripts over manual in-chat processing for anything repeated
- Run independent pipeline steps in parallel when possible

## Cost Control
- Target <$5/day in API costs
- Cache TTL: 6 hours for news fetches
- Never refetch the same URL in the same run unless explicitly forced

## Error Recovery
- On failure: capture error + inputs to `.tmp/runs/<run_id>/errors/`
- Re-run only the smallest failing step, not the entire pipeline
- Max 2 retries with exponential backoff
- After 2 failures: halt, log, and flag for human review
