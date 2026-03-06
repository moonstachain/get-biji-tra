---
name: get-biji-transcript
description: Use when Codex needs to operate Get笔记 to import supported links or local audio/video, create AI notes, and extract full original transcripts. Trigger this skill for requests about B站、抖音、小红书、公众号或其他网页链接的 Get笔记 导入，尤其是当目标是完整逐字稿、原文提取、录音转写、或复用已登录的 Get笔记 Web 自动化流程时。
---

# Get笔记逐字稿

Use the bundled script to drive Get笔记 Web with the persistent Chrome profile stored in this skill. The user may need to complete login or other authorization in the opened browser window.

This skill now treats `视频链接 -> Get笔记 添加链接 -> original transcript` as the only end-to-end transcript path.

## Workflow Decision

Choose the path that matches the user goal:

- If the user needs `Get笔记` login or the session may have expired, run `probe` first and let the user complete login.
- If the user wants a `链接总结` from a supported URL, run `submit-link`.
- If the user already has a remote `视频链接` and wants an end-to-end transcript in one step, run `transcribe-link`.
- If the user wants a `完整逐字稿`, only use the link-based `transcribe-link` path for this skill.
- If the user already has a Get笔记 `note id` and you need the original transcript, run `fetch-original`.

## Supported Reality

Treat these rules as hard constraints:

- `submit-link` usually creates a structured summary note, not a guaranteed full transcript.
- `fetch-original` only works for note types that expose `/note/:id/original`.
- For video platforms such as `B站` or `小红书视频`, `transcribe-link` follows one path only:
  1. submit the original link into `Get笔记`
  2. try to fetch an `original transcript` from that link-created note
- If the link-created note has no `original transcript`, stop and report that Get笔记 did not expose a transcript for that link.
- For articles or image-text links such as `公众号` or `小红书图文`, Get笔记 can summarize imported content, but there is no audio transcript unless the source itself is media.

## Commands

Run commands from the current workspace so artifacts land in `./artifacts` by default.

```bash
python3 scripts/get_biji_transcript.py probe --timeout-seconds 1800
```

```bash
python3 scripts/get_biji_transcript.py submit-link --link 'https://www.bilibili.com/video/BV1DGAYzPELm/' --timeout-seconds 180
```

```bash
python3 scripts/get_biji_transcript.py transcribe-link --link 'https://www.bilibili.com/video/BV1DGAYzPELm/' --timeout-seconds 300
```

```bash
python3 scripts/get_biji_transcript.py fetch-original --note-id 1903496783305829808
```

## Expected Outputs

Read the script output and return the important paths to the user:

- transcript text: `artifacts/transcript-<note-id>-<timestamp>.txt`
- structured transcript JSON: `artifacts/transcript-<note-id>-<timestamp>.json`
- page-state snapshots for debugging: screenshots, HTML, and element dumps in `artifacts/`

When transcript extraction succeeds, report:

- note title
- note id
- transcript file paths
- whether the output is full transcript or only summary

## Operating Notes

- Use `headless=True` flows unless login is required.
- If the script reports `Get笔记 is not logged in`, switch to `probe` and let the user authorize.
- If a link import succeeds but no original transcript route is available, say clearly that Get笔记 produced a summary note rather than a transcript.
- Do not auto-fallback to local download or media upload in this skill. The transcript path is link-only.
