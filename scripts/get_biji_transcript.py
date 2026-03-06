#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SKILL_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = SKILL_ROOT / "state"
PROFILE_DIR = STATE_DIR / "browser-profile" / "get-biji"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def ensure_dirs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def default_download_dir(output_dir: Path) -> Path:
    return output_dir / "downloads"


def dump_page_state(page, output_dir: Path, prefix: str) -> None:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    screenshot_path = output_dir / f"{prefix}-{timestamp}.png"
    html_path = output_dir / f"{prefix}-{timestamp}.html"
    json_path = output_dir / f"{prefix}-{timestamp}.json"

    page.screenshot(path=str(screenshot_path), full_page=True)
    html_path.write_text(page.content(), encoding="utf-8")

    element_data = page.evaluate(
        """
        () => {
          const items = Array.from(document.querySelectorAll('button, [role="button"], a, input, textarea'))
            .map((el) => ({
              tag: el.tagName,
              text: (el.innerText || el.textContent || '').trim(),
              placeholder: el.getAttribute('placeholder'),
              href: el.href || null,
              className: el.className || '',
              ariaLabel: el.getAttribute('aria-label'),
            }))
            .filter((item) => item.text || item.placeholder || item.href || item.ariaLabel);
          return items.slice(0, 300);
        }
        """
    )
    json_path.write_text(
        json.dumps(
            {
                "url": page.url,
                "title": page.title(),
                "elements": element_data,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Saved screenshot: {screenshot_path}")
    print(f"Saved html: {html_path}")
    print(f"Saved element dump: {json_path}")


def wait_for_login(page, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        page.goto("https://www.biji.com/note", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        body_text = page.locator("body").inner_text(timeout=10000)
        if "注册/登录" not in body_text:
            return
        try:
            page.get_by_text("注册/登录").click(timeout=3000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(5000)
    raise TimeoutError("Timed out waiting for Get笔记 login.")


def launch_context(playwright, *, headless: bool):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        executable_path=CHROME_PATH,
        headless=headless,
        viewport={"width": 1440, "height": 960},
    )


def first_note_title(page) -> str:
    locator = page.locator(".note-card .header-title").first
    locator.wait_for(timeout=10000)
    return locator.inner_text().strip()


def wait_for_new_note(page, previous_title: str, timeout_seconds: int) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        page.wait_for_timeout(3000)
        title = first_note_title(page)
        if title and title != previous_title:
            return title
    raise TimeoutError("Timed out waiting for the new Get笔记 note to appear in the list.")


def write_json_file(output_dir: Path, data: dict, prefix: str) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"{prefix}-{timestamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def downloader_cmd() -> list[str]:
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def download_media_from_link(output_dir: Path, link: str, download_dir: Optional[Path] = None) -> Tuple[Path, dict]:
    ensure_dirs(output_dir)
    download_dir = (download_dir or default_download_dir(output_dir)).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    metadata_path = output_dir / f"download-link-result-{timestamp}.json"
    outtmpl = str(download_dir / "%(title).180B [%(id)s].%(ext)s")
    cmd = [
        *downloader_cmd(),
        "--no-simulate",
        "--no-playlist",
        "--print",
        "after_move:filepath",
        "--dump-single-json",
        "-f",
        "bestaudio[ext=m4a]/bestaudio/best",
        "-o",
        outtmpl,
        link,
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "yt-dlp download failed"
        raise RuntimeError(message)

    stdout_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not stdout_lines:
        raise RuntimeError(f"Unexpected yt-dlp output: {proc.stdout}")

    metadata = json.loads(stdout_lines[-1])
    requested_downloads = metadata.get("requested_downloads") or []
    file_value = None
    if requested_downloads:
        first = requested_downloads[0]
        file_value = first.get("filepath") or first.get("filename") or first.get("_filename")
    file_value = file_value or metadata.get("_filename")
    if not file_value:
        raise RuntimeError(f"Could not determine downloaded file path from yt-dlp output: {proc.stdout}")

    file_path = Path(file_value).expanduser().resolve()
    if not file_path.exists():
        raise RuntimeError(f"Downloaded media file not found: {file_path}")

    metadata_path.write_text(
        json.dumps(
            {
                "input_link": link,
                "downloaded_file": str(file_path),
                "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "metadata": metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return file_path, {
        "metadata_path": str(metadata_path),
        "metadata": metadata,
    }


def extract_note_id(url: str) -> str:
    match = re.search(r"/note/(\d+)", url)
    if not match:
        raise ValueError(f"Could not extract note id from URL: {url}")
    return match.group(1)


def format_timestamp(ms: int) -> str:
    total_seconds = max(0, ms // 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_transcript_text(title: str, sentences: list[dict]) -> str:
    lines = [title, ""]
    previous_speaker = None
    for item in sentences:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        speaker_id = item.get("speaker_id", 0)
        speaker = item.get("speaker_name") or f"说话人{speaker_id + 1}"
        timestamp = format_timestamp(int(item.get("start_time", 0)))
        if speaker != previous_speaker:
            if previous_speaker is not None:
                lines.append("")
            lines.append(f"[{timestamp}] {speaker}")
            previous_speaker = speaker
        else:
            lines.append(f"[{timestamp}]")
        lines.append(text)
    return "\n".join(lines).strip() + "\n"


def require_logged_in(page) -> None:
    page.goto("https://www.biji.com/note", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    body_text = page.locator("body").inner_text(timeout=10000)
    if "注册/登录" in body_text:
        raise RuntimeError("Get笔记 is not logged in. Run `probe` first and complete login.")


def fetch_original_note_data(page, note_id: str) -> dict:
    api_url = f"https://get-notes.luojilab.com/voicenotes/web/notes/{note_id}/original"
    note_url = f"https://www.biji.com/note/{note_id}/original"
    with page.expect_response(
        lambda response: response.url == api_url and response.status == 200,
        timeout=60000,
    ) as response_info:
        page.goto(note_url, wait_until="domcontentloaded", timeout=60000)
    response = response_info.value
    page.wait_for_load_state("networkidle", timeout=60000)
    return response.json()


def try_fetch_original_note_data(context, note_id: str) -> Optional[dict]:
    api_url = f"https://get-notes.luojilab.com/voicenotes/web/notes/{note_id}/original"
    response = context.request.get(api_url, timeout=60000)
    if not response.ok:
        return None
    data = response.json()
    content_text = data.get("c", {}).get("content")
    if not content_text:
        return None
    try:
        content_data = json.loads(content_text)
    except json.JSONDecodeError:
        return None
    if not content_data.get("sentence_list"):
        return None
    return data


def submit_link_note(page, link: str, prompt: str, timeout_seconds: int) -> dict:
    previous_title = first_note_title(page)
    page.get_by_text("添加链接").click(timeout=10000)
    page.wait_for_timeout(1000)
    page.get_by_placeholder("粘贴或者输入链接").fill(link)
    if prompt:
        page.get_by_placeholder("整理这条链接的核心内容").fill(prompt)
    page.get_by_role("button", name="生成笔记").click(timeout=10000)
    new_title = wait_for_new_note(page, previous_title=previous_title, timeout_seconds=timeout_seconds)
    page.get_by_text(new_title).first.click(timeout=10000)
    page.wait_for_timeout(3000)
    note_url = page.url
    return {
        "input_link": link,
        "prompt": prompt,
        "note_title": new_title,
        "note_id": extract_note_id(note_url),
        "note_url": note_url,
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "body_text": page.locator("body").inner_text(timeout=10000),
    }


def run_probe(output_dir: Path, timeout_seconds: int) -> int:
    ensure_dirs(output_dir)
    with sync_playwright() as playwright:
        context = launch_context(playwright, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.biji.com", wait_until="domcontentloaded", timeout=60000)
        try:
            wait_for_login(page, timeout_seconds=timeout_seconds)
        except Exception as exc:
            dump_page_state(page, output_dir, "login-timeout")
            print(str(exc), file=sys.stderr)
            context.close()
            return 1
        page.goto("https://www.biji.com/note", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        dump_page_state(page, output_dir, "after-login-note")
        print("Probe complete. Browser will remain open for 30 seconds.")
        page.wait_for_timeout(30000)
        context.close()
        return 0


def run_submit_link(output_dir: Path, link: str, prompt: str, timeout_seconds: int) -> int:
    ensure_dirs(output_dir)
    with sync_playwright() as playwright:
        context = launch_context(playwright, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            require_logged_in(page)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            context.close()
            return 1

        try:
            result = submit_link_note(page, link=link, prompt=prompt, timeout_seconds=timeout_seconds)
        except Exception as exc:
            dump_page_state(page, output_dir, "submit-link-timeout")
            print(str(exc), file=sys.stderr)
            context.close()
            return 1

        result_path = write_json_file(output_dir, result, "submit-link-result")
        dump_page_state(page, output_dir, "submit-link-detail")
        print(f"Saved result: {result_path}")
        context.close()
        return 0


def run_import_audio(output_dir: Path, file_path: str, timeout_seconds: int) -> int:
    ensure_dirs(output_dir)
    media_path = Path(file_path).expanduser().resolve()
    if not media_path.exists():
        print(f"Media file does not exist: {media_path}", file=sys.stderr)
        return 1

    with sync_playwright() as playwright:
        context = launch_context(playwright, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            require_logged_in(page)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            context.close()
            return 1

        previous_title = first_note_title(page)
        page.get_by_text("导入音视频").click(timeout=10000)
        page.wait_for_timeout(1000)
        with page.expect_file_chooser(timeout=10000) as chooser_info:
            page.get_by_text("点此导入").click(timeout=10000)
        chooser_info.value.set_files(str(media_path))
        page.get_by_text("文件上传完成").wait_for(timeout=10 * 60 * 1000)
        page.get_by_role("button", name="生成笔记").click(timeout=10000)

        try:
            new_title = wait_for_new_note(page, previous_title=previous_title, timeout_seconds=timeout_seconds)
        except Exception as exc:
            dump_page_state(page, output_dir, "import-audio-timeout")
            print(str(exc), file=sys.stderr)
            context.close()
            return 1

        page.get_by_text(new_title).first.click(timeout=10000)
        page.wait_for_timeout(3000)
        note_url = page.url
        result = {
            "input_file": str(media_path),
            "note_title": new_title,
            "note_id": extract_note_id(note_url),
            "note_url": note_url,
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        result_path = write_json_file(output_dir, result, "import-audio-result")
        dump_page_state(page, output_dir, "import-audio-detail")
        print(f"Saved result: {result_path}")
        context.close()
        return 0


def transcribe_media_file(output_dir: Path, media_path: Path, timeout_seconds: int) -> dict:
    ensure_dirs(output_dir)
    if not media_path.exists():
        raise FileNotFoundError(f"Media file does not exist: {media_path}")

    with sync_playwright() as playwright:
        context = launch_context(playwright, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        require_logged_in(page)

        previous_title = first_note_title(page)
        page.get_by_text("导入音视频").click(timeout=10000)
        page.wait_for_timeout(1000)
        with page.expect_file_chooser(timeout=10000) as chooser_info:
            page.get_by_text("点此导入").click(timeout=10000)
        chooser_info.value.set_files(str(media_path))
        page.get_by_text("文件上传完成").wait_for(timeout=10 * 60 * 1000)
        page.get_by_role("button", name="生成笔记").click(timeout=10000)

        try:
            new_title = wait_for_new_note(page, previous_title=previous_title, timeout_seconds=timeout_seconds)
        except Exception:
            dump_page_state(page, output_dir, "transcribe-file-timeout")
            raise

        page.get_by_text(new_title).first.click(timeout=10000)
        page.wait_for_timeout(3000)
        note_url = page.url
        note_id = extract_note_id(note_url)
        api_data = fetch_original_note_data(page, note_id=note_id)

        content_text = api_data["c"].get("content") or "{}"
        content_data = json.loads(content_text)
        sentences = content_data.get("sentence_list") or []
        title = api_data["c"].get("title") or new_title
        transcript_text = build_transcript_text(title=title, sentences=sentences)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        result_path = output_dir / f"transcribe-file-result-{timestamp}.json"
        json_path = output_dir / f"transcript-{note_id}-{timestamp}.json"
        txt_path = output_dir / f"transcript-{note_id}-{timestamp}.txt"
        result = {
            "input_file": str(media_path),
            "note_title": title,
            "note_id": note_id,
            "note_url": note_url,
            "sentence_count": len(sentences),
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "note_id": note_id,
                    "title": title,
                    "sentence_count": len(sentences),
                    "sentences": sentences,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        txt_path.write_text(transcript_text, encoding="utf-8")
        dump_page_state(page, output_dir, f"transcribe-file-{note_id}")
        context.close()
        return {
            **result,
            "result_json": str(result_path),
            "transcript_json": str(json_path),
            "transcript_txt": str(txt_path),
        }


def run_fetch_original(output_dir: Path, note_id: str) -> int:
    ensure_dirs(output_dir)
    with sync_playwright() as playwright:
        context = launch_context(playwright, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            require_logged_in(page)
            api_data = fetch_original_note_data(page, note_id=note_id)
        except Exception as exc:
            dump_page_state(page, output_dir, "fetch-original-timeout")
            print(str(exc), file=sys.stderr)
            context.close()
            return 1

        content_text = api_data["c"].get("content") or "{}"
        content_data = json.loads(content_text)
        sentences = content_data.get("sentence_list") or []
        title = api_data["c"].get("title") or note_id
        transcript_text = build_transcript_text(title=title, sentences=sentences)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        json_path = output_dir / f"transcript-{note_id}-{timestamp}.json"
        txt_path = output_dir / f"transcript-{note_id}-{timestamp}.txt"
        json_path.write_text(
            json.dumps(
                {
                    "note_id": note_id,
                    "title": title,
                    "sentence_count": len(sentences),
                    "sentences": sentences,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        txt_path.write_text(transcript_text, encoding="utf-8")
        dump_page_state(page, output_dir, f"original-{note_id}")
        print(f"Title: {title}")
        print(f"Sentence count: {len(sentences)}")
        print(f"Saved transcript JSON: {json_path}")
        print(f"Saved transcript TXT: {txt_path}")
        context.close()
        return 0


def run_transcribe_file(output_dir: Path, file_path: str, timeout_seconds: int) -> int:
    media_path = Path(file_path).expanduser().resolve()
    try:
        result = transcribe_media_file(output_dir=output_dir, media_path=media_path, timeout_seconds=timeout_seconds)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Title: {result['note_title']}")
    print(f"Note ID: {result['note_id']}")
    print(f"Sentence count: {result['sentence_count']}")
    print(f"Saved result JSON: {result['result_json']}")
    print(f"Saved transcript JSON: {result['transcript_json']}")
    print(f"Saved transcript TXT: {result['transcript_txt']}")
    return 0


def run_download_link(output_dir: Path, link: str, download_dir: Optional[Path] = None) -> int:
    try:
        media_path, download_info = download_media_from_link(
            output_dir=output_dir,
            link=link,
            download_dir=download_dir,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    metadata = download_info["metadata"]
    print(f"Downloaded file: {media_path}")
    print(f"Metadata JSON: {download_info['metadata_path']}")
    print(f"Title: {metadata.get('title', '')}")
    print(f"Extractor: {metadata.get('extractor_key', metadata.get('extractor', ''))}")
    return 0


def run_transcribe_link(
    output_dir: Path,
    link: str,
    timeout_seconds: int,
    download_dir: Optional[Path] = None,
) -> int:
    link_result = None
    try:
        with sync_playwright() as playwright:
            context = launch_context(playwright, headless=True)
            page = context.pages[0] if context.pages else context.new_page()
            require_logged_in(page)
            link_result = submit_link_note(page, link=link, prompt="", timeout_seconds=timeout_seconds)
            original_data = try_fetch_original_note_data(context, note_id=link_result["note_id"])
            if original_data:
                content_data = json.loads(original_data["c"]["content"])
                sentences = content_data.get("sentence_list") or []
                title = original_data["c"].get("title") or link_result["note_title"]
                transcript_text = build_transcript_text(title=title, sentences=sentences)
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                result_json = output_dir / f"transcribe-link-result-{timestamp}.json"
                transcript_json = output_dir / f"transcript-{link_result['note_id']}-{timestamp}.json"
                transcript_txt = output_dir / f"transcript-{link_result['note_id']}-{timestamp}.txt"
                result_json.write_text(
                    json.dumps(
                        {
                            "path_used": "get-link-original",
                            "input_link": link,
                            "note_title": title,
                            "note_id": link_result["note_id"],
                            "note_url": link_result["note_url"],
                            "sentence_count": len(sentences),
                            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                transcript_json.write_text(
                    json.dumps(
                        {
                            "note_id": link_result["note_id"],
                            "title": title,
                            "sentence_count": len(sentences),
                            "sentences": sentences,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                transcript_txt.write_text(transcript_text, encoding="utf-8")
                dump_page_state(page, output_dir, f"transcribe-link-{link_result['note_id']}")
                context.close()
                print(f"Path used: get-link-original")
                print(f"Title: {title}")
                print(f"Note ID: {link_result['note_id']}")
                print(f"Sentence count: {len(sentences)}")
                print(f"Saved workflow JSON: {result_json}")
                print(f"Saved transcript JSON: {transcript_json}")
                print(f"Saved transcript TXT: {transcript_txt}")
                return 0
            context.close()

        media_path, download_info = download_media_from_link(output_dir=output_dir, link=link, download_dir=download_dir)
        result = transcribe_media_file(output_dir=output_dir, media_path=media_path, timeout_seconds=timeout_seconds)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    summary_path = write_json_file(
        output_dir,
        {
            "path_used": "download-import-original",
            "input_link": link,
            "link_result": link_result,
            "downloaded_file": str(media_path),
            "download_metadata_path": download_info["metadata_path"],
            "note_title": result["note_title"],
            "note_id": result["note_id"],
            "note_url": result["note_url"],
            "sentence_count": result["sentence_count"],
            "result_json": result["result_json"],
            "transcript_json": result["transcript_json"],
            "transcript_txt": result["transcript_txt"],
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "transcribe-link-result",
    )
    print("Path used: download-import-original")
    print(f"Get note from link: {link_result['note_id'] if link_result else ''}")
    print(f"Downloaded file: {media_path}")
    print(f"Title: {result['note_title']}")
    print(f"Note ID: {result['note_id']}")
    print(f"Sentence count: {result['sentence_count']}")
    print(f"Saved workflow JSON: {summary_path}")
    print(f"Saved transcript JSON: {result['transcript_json']}")
    print(f"Saved transcript TXT: {result['transcript_txt']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Automate Get笔记 web flows for transcript extraction.")
    parser.add_argument(
        "command",
        choices=[
            "probe",
            "submit-link",
            "import-audio",
            "fetch-original",
            "download-link",
            "transcribe-file",
            "transcribe-link",
        ],
        help="Action to perform.",
    )
    parser.add_argument("--link", help="The URL to submit to Get笔记.")
    parser.add_argument("--prompt", default="", help="Optional prompt for Get笔记 link import.")
    parser.add_argument("--file", help="The local audio/video file to upload into Get笔记.")
    parser.add_argument("--note-id", help="The Get笔记 note id used to fetch the original transcript.")
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd() / "artifacts"),
        help="Directory for screenshots, JSON, and transcript artifacts. Defaults to ./artifacts.",
    )
    parser.add_argument(
        "--download-dir",
        help="Directory for downloaded media files. Defaults to <output-dir>/downloads.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Seconds to wait for login or note generation.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    download_dir = Path(args.download_dir).expanduser().resolve() if args.download_dir else None

    if args.command == "probe":
        return run_probe(output_dir=output_dir, timeout_seconds=args.timeout_seconds)
    if args.command == "submit-link":
        if not args.link:
            print("--link is required for submit-link", file=sys.stderr)
            return 1
        return run_submit_link(
            output_dir=output_dir,
            link=args.link,
            prompt=args.prompt,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "import-audio":
        if not args.file:
            print("--file is required for import-audio", file=sys.stderr)
            return 1
        return run_import_audio(
            output_dir=output_dir,
            file_path=args.file,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "fetch-original":
        if not args.note_id:
            print("--note-id is required for fetch-original", file=sys.stderr)
            return 1
        return run_fetch_original(output_dir=output_dir, note_id=args.note_id)
    if args.command == "download-link":
        if not args.link:
            print("--link is required for download-link", file=sys.stderr)
            return 1
        return run_download_link(output_dir=output_dir, link=args.link, download_dir=download_dir)
    if args.command == "transcribe-file":
        if not args.file:
            print("--file is required for transcribe-file", file=sys.stderr)
            return 1
        return run_transcribe_file(
            output_dir=output_dir,
            file_path=args.file,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "transcribe-link":
        if not args.link:
            print("--link is required for transcribe-link", file=sys.stderr)
            return 1
        return run_transcribe_link(
            output_dir=output_dir,
            link=args.link,
            timeout_seconds=args.timeout_seconds,
            download_dir=download_dir,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
