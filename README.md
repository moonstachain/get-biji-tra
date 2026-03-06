# Get Biji Transcript

## What it is
Get Biji Transcript is a Codex skill for importing supported links into Get笔记, creating notes, and extracting the original transcript when the target note type exposes it.

## Who it's for
This repo is for operators who want a repeatable way to turn Bilibili, Xiaohongshu, Douyin, or similar supported links into transcripts that can be reused in downstream workflows.

## Quick start
```bash
python3 scripts/get_biji_transcript.py transcribe-link --link 'https://www.bilibili.com/video/BV1DGAYzPELm/' --timeout-seconds 300
```

## Inputs
- A supported public media URL.
- A valid Get笔记 browser session if login is required.
- Optional note id when fetching an existing original transcript directly.

## Outputs
- Transcript text files in `artifacts/transcript-<note-id>-<timestamp>.txt`.
- Structured transcript JSON in `artifacts/transcript-<note-id>-<timestamp>.json`.
- Debugging screenshots, HTML snapshots, and page-state dumps in `artifacts/`.

## Constraints
- The transcript path is link-only in this skill.
- `submit-link` may create only a summary note, not a full transcript.
- `fetch-original` works only for notes that expose `/note/:id/original`.
- If Get笔记 does not expose an original transcript for the imported note, the workflow must stop and report that outcome explicitly.

## Example
Import a Bilibili video link into Get笔记, let the automation create a note, and then export the original transcript into `.txt` and `.json` if the note supports transcript retrieval.

## Project structure
- `scripts/`: automation entrypoints for probing login state, importing links, and fetching transcripts.
- `agents/`: Codex interface metadata.

