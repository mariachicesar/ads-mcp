"""
Video ingestion script — download, transcribe, and summarize Google Ads learning videos.

Usage:
    python scripts/ingest_video.py <url> [--model base] [--title "Optional Title"]

Examples:
    python scripts/ingest_video.py https://fb.watch/GURC2DV8LX/
    python scripts/ingest_video.py https://www.youtube.com/watch?v=dQw4w9WgXcQ --model small

Requirements:
    pip install -r scripts/requirements-ingest.txt
    ffmpeg must be installed on your system (winget install ffmpeg on Windows)

Environment:
    ANTHROPIC_API_KEY  — required for summarization step
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "servers" / "google-ads" / "knowledge"

SUMMARIZE_SYSTEM = """\
You are a Google Ads expert extracting actionable insights from video content.
Given a raw transcript of a Google Ads educational video, produce a structured markdown summary.

Output ONLY the following sections — no preamble, no commentary:

## Title
A concise descriptive title (not a YouTube/Facebook title — your own summary title).

## Topics
A comma-separated list of relevant topic tags from this set:
bidding, smart-bidding, manual-cpc, target-cpa, target-roas, maximize-clicks,
quality-score, ad-relevance, landing-page, keywords, match-types, negative-keywords,
search-terms, impression-share, ad-copy, rsa, extensions, audience, remarketing,
budget, campaign-structure, conversion-tracking, performance-max, shopping,
account-setup, reporting, local-campaigns, call-ads

## Key Tactics
A bulleted list of the most actionable, specific tactics discussed in the video.
Each bullet should be a complete, self-contained insight (2-3 sentences max).
Focus on advice that can be directly applied to improve a Google Ads account.
Aim for 5-15 bullets.

## Summary
2-3 sentences summarizing the overall lesson of the video.
"""


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60].strip("-")


def _find_ffmpeg() -> str | None:
    """Return the ffmpeg binary path, checking PATH and common winget install location."""
    # First try PATH (works after shell restart)
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    # Winget install location (before shell restart)
    winget_path = (
        Path.home()
        / "AppData/Local/Microsoft/WinGet/Packages"
    )
    if winget_path.exists():
        for ffmpeg_bin in winget_path.glob("Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe"):
            return str(ffmpeg_bin)
    return None


def _download_audio(url: str, output_dir: Path) -> Path:
    try:
        import yt_dlp
    except ImportError:
        sys.exit("yt-dlp not installed. Run: pip install -r scripts/requirements-ingest.txt")

    audio_path = output_dir / "audio"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(audio_path),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    ffmpeg_loc = _find_ffmpeg()
    if ffmpeg_loc:
        ydl_opts["ffmpeg_location"] = str(Path(ffmpeg_loc).parent)
    else:
        sys.exit(
            "ERROR: ffmpeg not found.\n"
            "Install it with: winget install ffmpeg\n"
            "Then open a new terminal and try again."
        )

    print(f"  Downloading audio from: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # yt-dlp appends .mp3
    mp3_path = output_dir / "audio.mp3"
    if not mp3_path.exists():
        # Fallback: find any audio file
        candidates = list(output_dir.glob("audio.*"))
        if not candidates:
            sys.exit("ERROR: yt-dlp did not produce an audio file. Is ffmpeg installed?")
        mp3_path = candidates[0]

    print(f"  Audio saved: {mp3_path.name} ({mp3_path.stat().st_size // 1024} KB)")
    return mp3_path


def _transcribe(audio_path: Path, model_name: str) -> str:
    try:
        import whisper
    except ImportError:
        sys.exit("openai-whisper not installed. Run: pip install -r scripts/requirements-ingest.txt")

    # Ensure ffmpeg is on PATH for Whisper's audio loading
    ffmpeg_loc = _find_ffmpeg()
    if ffmpeg_loc:
        ffmpeg_dir = str(Path(ffmpeg_loc).parent)
        if ffmpeg_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    print(f"  Loading Whisper '{model_name}' model (first run downloads it)...")
    model = whisper.load_model(model_name)
    print("  Transcribing... (this may take a few minutes)")
    result = model.transcribe(str(audio_path), fp16=False)
    text = result["text"].strip()
    print(f"  Transcript: {len(text):,} characters")
    return text


def _summarize(transcript: str, url: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        sys.exit(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Set it before running: set ANTHROPIC_API_KEY=sk-ant-..."
        )

    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic not installed. Run: pip install -r scripts/requirements-ingest.txt")

    client = anthropic.Anthropic(api_key=api_key)

    # Truncate transcript to ~6000 words to stay within context limits
    words = transcript.split()
    if len(words) > 6000:
        transcript = " ".join(words[:6000]) + "\n\n[transcript truncated]"

    print("  Summarizing with Claude Haiku...")
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=SUMMARIZE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Source URL: {url}\n\nTranscript:\n{transcript}",
            }
        ],
    )

    summary_md = message.content[0].text.strip()
    tokens_in = message.usage.input_tokens
    tokens_out = message.usage.output_tokens
    cost_est = (tokens_in / 1_000_000 * 0.25) + (tokens_out / 1_000_000 * 1.25)

    print(f"  Summarization done ({tokens_in} in / {tokens_out} out / ~${cost_est:.4f})")
    return {"markdown": summary_md, "tokens_in": tokens_in, "tokens_out": tokens_out}


def _extract_title_from_summary(summary_md: str) -> str:
    for line in summary_md.splitlines():
        if line.startswith("## Title"):
            continue
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return "untitled"


def _save_knowledge_file(
    url: str,
    summary_md: str,
    transcript: str,
    title_override: str | None,
) -> Path:
    title = title_override or _extract_title_from_summary(summary_md)
    slug = _slugify(title)
    today = date.today().isoformat()

    # Extract topics line from summary for frontmatter
    topics_line = ""
    lines = summary_md.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Topics":
            if i + 1 < len(lines):
                topics_line = lines[i + 1].strip()
            break

    # Truncate full transcript stored in file to ~3000 words
    transcript_words = transcript.split()
    stored_transcript = transcript
    truncated = False
    if len(transcript_words) > 3000:
        stored_transcript = " ".join(transcript_words[:3000])
        truncated = True

    frontmatter = f"---\nsource: {url}\ndate: {today}\ntopics: [{topics_line}]\ntitle: {title}\n---\n\n"

    transcript_section = (
        "\n\n---\n\n## Full Transcript\n\n"
        + stored_transcript
        + ("\n\n[transcript truncated at 3000 words]" if truncated else "")
    )

    # Remove "## Title" and title text from body since it's in frontmatter
    cleaned_summary = []
    skip_next = False
    for line in lines:
        if line.strip() == "## Title":
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        cleaned_summary.append(line)
    summary_body = "\n".join(cleaned_summary).strip()

    content = frontmatter + summary_body + transcript_section

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = KNOWLEDGE_DIR / f"{slug}.md"

    # Avoid overwriting — append a counter suffix if file exists
    if output_path.exists():
        counter = 2
        while output_path.exists():
            output_path = KNOWLEDGE_DIR / f"{slug}-{counter}.md"
            counter += 1

    output_path.write_text(content, encoding="utf-8")
    return output_path


def _ingest_one(url: str, model: str, title: str | None, index: int, total: int) -> bool:
    """Ingest a single URL. Returns True on success, False on failure."""
    prefix = f"[{index}/{total}]" if total > 1 else ""
    print(f"\n{prefix} Ingesting:\n  {url}\n")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            print(f"{prefix}[1/3] Downloading audio...")
            audio_path = _download_audio(url, tmp_path)

            print(f"\n{prefix}[2/3] Transcribing audio...")
            transcript = _transcribe(audio_path, model)

            print(f"\n{prefix}[3/3] Summarizing transcript...")
            result = _summarize(transcript, url)

        output_path = _save_knowledge_file(
            url=url,
            summary_md=result["markdown"],
            transcript=transcript,
            title_override=title,
        )
        print(f"\n  Saved: {output_path.relative_to(ROOT)}")
        return True

    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  ERROR processing {url}: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Google Ads educational videos into the knowledge base.",
        epilog=(
            "Examples:\n"
            "  Single URL:  python scripts/ingest_video.py https://fb.watch/ABC\n"
            "  Multi URL:   python scripts/ingest_video.py https://fb.watch/ABC https://youtu.be/XYZ\n"
            "  From file:   python scripts/ingest_video.py --file urls.txt\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="One or more video URLs (Facebook, YouTube, etc.)",
    )
    parser.add_argument(
        "--file",
        default=None,
        metavar="FILE",
        help="Path to a text file with one URL per line (lines starting with # are ignored).",
    )
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium"],
        help="Whisper model size (default: base). 'small' is more accurate but slower.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Override title for the output filename. Only applies when ingesting a single URL.",
    )
    args = parser.parse_args()

    # Collect URLs from positional args and/or --file
    urls: list[str] = list(args.urls)
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            sys.exit(f"ERROR: File not found: {args.file}")
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)

    if not urls:
        parser.print_help()
        sys.exit(1)

    if args.title and len(urls) > 1:
        print("WARNING: --title is ignored when processing multiple URLs.\n")
        args.title = None

    total = len(urls)
    print(f"\n[ingest_video] {total} video(s) to process\n" + "=" * 50)

    succeeded = 0
    failed: list[str] = []

    for i, url in enumerate(urls, 1):
        ok = _ingest_one(url, args.model, args.title, i, total)
        if ok:
            succeeded += 1
        else:
            failed.append(url)

    # Final summary
    print("\n" + "=" * 50)
    print(f"Done: {succeeded}/{total} succeeded.")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for u in failed:
            print(f"  {u}")
    else:
        print("\nYou can now ask Claude: 'What have I learned about <topic>?'")


if __name__ == "__main__":
    main()
