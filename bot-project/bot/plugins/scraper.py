"""Sequential Website Scraper & File-Store plugin.

Workflow:
  1. Admin sends /scrap <url> or /scrapall <url> → bot crawls for video links → saves to queue.
  2. Background worker picks up FIFO: download (yt-dlp 720p) → thumbnail (FFmpeg)
     → compress if >100 MB → upload to BIN_CHANNEL → store unique_code → post to
     MAIN_CHANNEL with Watch-Video deep-link → cleanup.
  3. /start video_<unique_code> → forward from BIN_CHANNEL to user.

Commands:
  /scrap <url>               — crawl ONE page, queue all video links found
  /scrapall <url> [maxpages] — auto-paginate up to maxpages (default 50)
  /scrap_stop                — pause the worker between jobs
  /scrap_resume              — resume the worker
  /scrap_clear               — delete all pending/failed queue items
  /scraper_status            — show queue counts
  /scraper_test <url>        — queue a single URL for direct download
"""

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import (
    parse_qs, urlencode, urljoin, urlparse, urlunparse
)

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import ADMIN_IDS, BIN_CHANNEL_ID, MAIN_CHANNEL_ID, BOT_USERNAME
from bot.database import scraper_db

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_DOWNLOAD_DIR = Path("downloads")
_DOWNLOAD_DIR.mkdir(exist_ok=True)

_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv"}

# Path segments that are never video pages
_NAV_SEGMENTS = {
    "login", "logout", "register", "signup", "sign-up", "sign-in",
    "search", "categories", "category", "tags", "tag", "browse",
    "explore", "trending", "popular", "new", "top", "recent", "latest",
    "members", "member", "users", "user", "profile", "settings",
    "account", "about", "contact", "help", "faq", "terms", "privacy",
    "dmca", "advertise", "sitemap", "random", "images", "image",
    "photos", "photo", "galleries", "gallery", "groups", "group",
    "boards", "board", "classifieds", "shouts", "chat", "store",
    "videos", "girls", "guys", "straight", "gay", "lesbian", "trans",
    "home", "index", "upload", "download", "report", "share", "embed",
    "comments", "comment", "forum", "forums", "news", "blog", "feed",
    "rss", "api", "static", "assets", "css", "js", "img",
    # redirect / affiliate paths that wrap real video links
    "goto", "redirect", "out", "exit", "click", "track", "ref",
    # pagination segments
    "page", "pages",
}

# Worker state
_worker_started = False
_worker_paused = False
_worker_client: Client | None = None


# ── helpers ────────────────────────────────────────────────────────────────────

async def _notify_admin(client: Client, chat_id: int, text: str) -> None:
    try:
        await client.send_message(chat_id, text)
    except Exception:
        pass


async def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "Process timed out"
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _is_video_link(path: str, base_path: str) -> bool:
    """Return True if the URL path looks like an individual video page."""
    path = path.rstrip("/")
    if not path or path == base_path.rstrip("/"):
        return False

    last_seg = path.split("/")[-1].lower()
    if not last_seg or len(last_seg) < 4:
        return False
    if last_seg in _NAV_SEGMENTS:
        return False

    # Skip file extensions that are definitely not video pages
    if any(last_seg.endswith(ext) for ext in (
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".css", ".js", ".xml", ".json", ".txt", ".pdf",
    )):
        return False

    # Must look like a video slug (hyphen-separated words) or hash-style ID
    is_slug = "-" in last_seg and len(last_seg) >= 6
    is_hash_id = last_seg.isalnum() and len(last_seg) >= 5
    return is_slug or is_hash_id


async def _scrape_page_links(url: str) -> list[dict]:
    """Fetch the HTML of url and extract video-page links on the same domain."""
    import aiohttp
    from html.parser import HTMLParser

    base_parsed = urlparse(url)
    base_domain = base_parsed.netloc
    base_path = base_parsed.path

    class _LP(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = dict(attrs).get("href", "")
                if href:
                    self.links.append(href)

    try:
        async with aiohttp.ClientSession(headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                html = await resp.text(errors="replace")
    except Exception as e:
        logger.warning("[Scraper] HTTP fetch failed for %s: %s", url, e)
        return []

    parser = _LP()
    parser.feed(html)

    seen: set[str] = set()
    items: list[dict] = []

    for href in parser.links:
        full = urljoin(url, href)
        parsed = urlparse(full)
        if parsed.netloc != base_domain:
            continue
        if not _is_video_link(parsed.path, base_path):
            continue
        clean = parsed._replace(query="", fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)
        title = parsed.path.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()
        items.append({"url": clean, "title": title})
        if len(items) >= 60:
            break

    return items


async def _crawl_videos(url: str) -> list[dict]:
    """
    Try yt-dlp --flat-playlist first.
    If it returns nothing, fall back to HTML link scraping.
    """
    rc, out, _ = await _run([
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(webpage_url)s\t%(title)s",
        "--no-warnings",
        "--quiet",
        url,
    ], timeout=120)

    items: list[dict] = []
    for line in out.strip().splitlines():
        parts = line.split("\t", 1)
        if parts and parts[0].startswith("http"):
            items.append({
                "url": parts[0].strip(),
                "title": parts[1].strip() if len(parts) > 1 else parts[0].strip(),
            })

    if items:
        return items

    return await _scrape_page_links(url)


def _build_page_url(base_url: str, page_num: int) -> str | None:
    """
    Try to build the URL for page_num of a listing.
    Supports: ?page=N, ?p=N, /page/N/, and appending ?page=N.
    Returns None for page 1 (that's the original URL).
    """
    if page_num <= 1:
        return None

    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)

    if "page" in qs:
        qs["page"] = [str(page_num)]
        return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

    if "p" in qs:
        qs["p"] = [str(page_num)]
        return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

    # /page/N/ in path
    path = parsed.path
    m = re.match(r"^(.*?/page/)(\d+)(/?.*)$", path)
    if m:
        return urlunparse(parsed._replace(path=f"{m.group(1)}{page_num}{m.group(3)}"))

    # Append ?page=N
    sep = "&" if parsed.query else ""
    new_query = f"{parsed.query}{sep}page={page_num}" if parsed.query else f"page={page_num}"
    return urlunparse(parsed._replace(query=new_query))


async def _crawl_all_pages(
    url: str,
    max_pages: int = 50,
) -> tuple[list[dict], int]:
    """
    Crawl url across multiple pages. Returns (all_items, pages_crawled).
    Stops when a page yields no new items.
    """
    all_items: list[dict] = []
    seen_urls: set[str] = set()
    pages_crawled = 0

    for page_num in range(1, max_pages + 1):
        if page_num == 1:
            page_url = url
        else:
            page_url = _build_page_url(url, page_num)
            if not page_url:
                break

        logger.info("[Scraper] Crawling page %d: %s", page_num, page_url)
        page_items = await _crawl_videos(page_url)
        pages_crawled += 1

        new_items = [i for i in page_items if i["url"] not in seen_urls]
        if not new_items:
            logger.info("[Scraper] No new items on page %d, stopping pagination.", page_num)
            break

        for i in new_items:
            seen_urls.add(i["url"])
        all_items.extend(new_items)

        if page_num < max_pages:
            await asyncio.sleep(1.5)  # polite crawl delay

    return all_items, pages_crawled


# ── download / upload helpers ──────────────────────────────────────────────────

async def _download_video(video_url: str, out_dir: Path) -> Path | None:
    template = str(out_dir / "%(id)s.%(ext)s")
    rc, _, err = await _run([
        "yt-dlp",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "-o", template,
        "--no-playlist",
        "--no-warnings",
        "--retries", "3",
        video_url,
    ], timeout=600)

    if rc != 0:
        logger.warning("yt-dlp failed for %s: %s", video_url, err[:500])
        return None

    if out_dir.exists():
        for f in out_dir.iterdir():
            if f.suffix.lower() in _VIDEO_EXTS and f.stat().st_size > 0:
                return f
    return None


async def _extract_thumbnail(video_path: Path, out_path: Path) -> bool:
    rc, _, _ = await _run([
        "ffmpeg", "-y", "-ss", "5",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=320:-1",
        str(out_path),
    ], timeout=60)
    return rc == 0 and out_path.exists() and out_path.stat().st_size > 0


async def _compress_video(video_path: Path, out_path: Path) -> bool:
    rc, _, _ = await _run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ], timeout=600)
    return rc == 0 and out_path.exists() and out_path.stat().st_size > 0


def _cleanup_dir(tmp_dir: Path) -> None:
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


# ── worker ─────────────────────────────────────────────────────────────────────

async def _process_one(client: Client, job: dict) -> None:
    job_id = str(job["_id"])
    video_url = job["url"]
    title = job.get("title", "Video")
    admin_chat_id = job.get("admin_chat_id")

    logger.info("[Scraper] Processing: %s", video_url)
    await scraper_db.mark_processing(job_id)

    tmp_dir = _DOWNLOAD_DIR / job_id
    tmp_dir.mkdir(parents=True, exist_ok=True)

    async def _fail(reason: str):
        await scraper_db.mark_failed(job_id, reason)
        logger.warning("[Scraper] Failed %s: %s", video_url, reason)
        if admin_chat_id:
            await _notify_admin(client, admin_chat_id,
                f"❌ **Failed**\n`{video_url}`\n{reason[:200]}")
        _cleanup_dir(tmp_dir)

    try:
        video_path = await _download_video(video_url, tmp_dir)
        if not video_path:
            await _fail("yt-dlp could not download (private, age-restricted, or unsupported)")
            return

        upload_path = video_path
        thumb_path = tmp_dir / "thumb.jpg"
        thumb_ok = await _extract_thumbnail(video_path, thumb_path)
        if not thumb_ok:
            thumb_path = None

        size_mb = video_path.stat().st_size / (1024 * 1024)
        if size_mb > 100:
            compressed = tmp_dir / "compressed.mp4"
            if await _compress_video(video_path, compressed):
                upload_path = compressed

        if not BIN_CHANNEL_ID:
            await _fail("BIN_CHANNEL_ID not configured")
            return

        send_kwargs = dict(chat_id=BIN_CHANNEL_ID, video=str(upload_path), caption=title)
        if thumb_path:
            send_kwargs["thumb"] = str(thumb_path)

        bin_msg = await client.send_video(**send_kwargs)
        unique_code = uuid.uuid4().hex[:12]

        await scraper_db.save_video(
            unique_code=unique_code,
            bin_message_id=bin_msg.id,
            caption=title,
            source_url=video_url,
        )

        if MAIN_CHANNEL_ID:
            bot_username = BOT_USERNAME
            if not bot_username:
                me = await client.get_me()
                bot_username = me.username

            watch_link = f"https://t.me/{bot_username}?start=video_{unique_code}"
            post_text = (
                f"🎬 **{title}**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Tap below to watch this video."
            )
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Watch Video", url=watch_link)]])

            if thumb_path:
                await client.send_photo(
                    chat_id=MAIN_CHANNEL_ID, photo=str(thumb_path),
                    caption=post_text, reply_markup=btn,
                )
            else:
                await client.send_message(
                    chat_id=MAIN_CHANNEL_ID, text=post_text, reply_markup=btn,
                )

        await scraper_db.mark_done(job_id)
        logger.info("[Scraper] Done: %s → code=%s", video_url, unique_code)

        if admin_chat_id:
            await _notify_admin(client, admin_chat_id,
                f"✅ **Posted!**\n🎬 {title}\n🔑 `{unique_code}`")

    except Exception as exc:
        logger.exception("[Scraper] Error processing %s: %s", video_url, exc)
        await _fail(str(exc))
    finally:
        _cleanup_dir(tmp_dir)


async def scraper_worker(client: Client) -> None:
    global _worker_paused
    logger.info("[Scraper] Worker started.")
    while True:
        try:
            if _worker_paused:
                await asyncio.sleep(5)
                continue

            job = await scraper_db.get_next_pending()
            if job:
                await _process_one(client, job)
                await asyncio.sleep(2)
            else:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("[Scraper] Worker unexpected error: %s", exc)
            await asyncio.sleep(15)


def start_scraper_worker(client: Client) -> None:
    global _worker_started, _worker_client
    if not _worker_started:
        _worker_client = client
        asyncio.create_task(scraper_worker(client))
        _worker_started = True
        logger.info("[Scraper] Background worker scheduled.")


# ── shared queue logic ─────────────────────────────────────────────────────────

async def _enqueue_items(
    client: Client,
    message: Message,
    items: list[dict],
    source_url: str,
) -> tuple[int, int]:
    """Add items to queue, skipping duplicates. Returns (added, skipped)."""
    added = skipped = 0
    for v in items:
        result = await scraper_db.add_to_queue(
            url=v["url"],
            source_url=source_url,
            title=v["title"],
            admin_chat_id=message.chat.id,
            skip_duplicates=True,
        )
        if result is None:
            skipped += 1
        else:
            added += 1
    return added, skipped


# ── Pyrogram handlers ─────────────────────────────────────────────────────────

def _is_admin(_, __, message: Message) -> bool:
    return message.from_user and message.from_user.id in ADMIN_IDS


_admin_filter = filters.create(_is_admin)


@Client.on_message(filters.private & _admin_filter & filters.command("scrap"))
async def scrap_command(client: Client, message: Message):
    """/scrap <url> — crawl ONE page and queue all videos found."""
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply_text(
            "**Usage:**\n"
            "`/scrap <url>`  — crawl one page, queue all videos\n"
            "`/scrapall <url> [maxpages]`  — auto-crawl ALL pages (e.g. 50)\n\n"
            "Videos are processed one by one: download → BIN → post to channel."
        )
        return

    url = args[1].strip()
    if not url.startswith("http"):
        await message.reply_text("❌ Please provide a valid URL starting with `http`.")
        return

    status_msg = await message.reply_text(f"🔍 **Crawling…**\n\n`{url}`")

    items = await _crawl_videos(url)
    if not items:
        await status_msg.edit_text(
            "⚠️ **No videos found on that page.**\n\n"
            "Try `/scrapall <url>` to crawl multiple pages, or "
            "`/scraper_test <url>` for a single direct video URL."
        )
        return

    added, skipped = await _enqueue_items(client, message, items, url)
    pending = await scraper_db.count_pending()
    await status_msg.edit_text(
        f"✅ **Queued from 1 page**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 `{url}`\n"
        f"📋 Found: **{len(items)}** · Added: **{added}** · Skipped (dup): **{skipped}**\n"
        f"⏳ Total pending: **{pending}**\n\n"
        f"Processing one by one ✅  Use `/scrap_stop` to pause."
    )


@Client.on_message(filters.private & _admin_filter & filters.command("scrapall"))
async def scrapall_command(client: Client, message: Message):
    """/scrapall <url> [maxpages] — auto-paginate and queue ALL pages."""
    args = message.text.split(None, 2)
    if len(args) < 2:
        await message.reply_text(
            "**Usage:** `/scrapall <url> [maxpages]`\n\n"
            "Example: `/scrapall https://motherless.com/term/bbc 100`\n"
            "Default max pages: 50"
        )
        return

    url = args[1].strip()
    if not url.startswith("http"):
        await message.reply_text("❌ Please provide a valid URL starting with `http`.")
        return

    max_pages = 50
    if len(args) == 3:
        try:
            max_pages = max(1, min(int(args[2].strip()), 200))
        except ValueError:
            pass

    status_msg = await message.reply_text(
        f"🔍 **Crawling all pages…**\n\n"
        f"`{url}`\n"
        f"Max pages: **{max_pages}** — this may take a while."
    )

    items, pages_done = await _crawl_all_pages(url, max_pages=max_pages)

    if not items:
        await status_msg.edit_text(
            f"⚠️ **No videos found** after crawling {pages_done} page(s).\n\n"
            f"Make sure the URL is a listing/category page."
        )
        return

    added, skipped = await _enqueue_items(client, message, items, url)
    pending = await scraper_db.count_pending()
    await status_msg.edit_text(
        f"✅ **Multi-page crawl done!**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 `{url}`\n"
        f"📄 Pages crawled: **{pages_done}**\n"
        f"📋 Found: **{len(items)}** · Added: **{added}** · Skipped (dup): **{skipped}**\n"
        f"⏳ Total pending: **{pending}**\n\n"
        f"Processing one by one ✅  Use `/scrap_stop` to pause."
    )


@Client.on_message(filters.private & _admin_filter & filters.command("scrap_stop"))
async def scrap_stop(client: Client, message: Message):
    """/scrap_stop — pause the worker after the current video finishes."""
    global _worker_paused
    _worker_paused = True
    pending = await scraper_db.count_pending()
    await message.reply_text(
        f"⏸ **Scraper paused.**\n\n"
        f"The current download will finish, then the worker stops.\n"
        f"⏳ {pending} job(s) still pending.\n\n"
        f"Use `/scrap_resume` to continue."
    )


@Client.on_message(filters.private & _admin_filter & filters.command("scrap_resume"))
async def scrap_resume(client: Client, message: Message):
    """/scrap_resume — resume the paused worker."""
    global _worker_paused
    _worker_paused = False
    pending = await scraper_db.count_pending()
    await message.reply_text(
        f"▶️ **Scraper resumed!**\n\n"
        f"⏳ {pending} job(s) in queue."
    )


@Client.on_message(filters.private & _admin_filter & filters.regex(_URL_RE))
async def url_received(client: Client, message: Message):
    """Admin sends a bare URL (no command) → crawl one page and queue."""
    raw_text = message.text or message.caption or ""
    if raw_text.strip().startswith("/"):
        return
    match = _URL_RE.search(raw_text)
    if not match:
        return

    source_url = match.group(0)
    status_msg = await message.reply_text(f"🔍 **Crawling…**\n\n`{source_url}`")

    items = await _crawl_videos(source_url)
    if not items:
        await status_msg.edit_text(
            "⚠️ **No videos found.**\n\n"
            "Try `/scrapall <url>` for multi-page crawling or "
            "`/scraper_test <url>` for a single video URL."
        )
        return

    added, skipped = await _enqueue_items(client, message, items, source_url)
    pending = await scraper_db.count_pending()
    await status_msg.edit_text(
        f"✅ **{added} video(s) queued** ({skipped} skipped as duplicates)\n\n"
        f"⏳ Total pending: **{pending}**"
    )


@Client.on_message(filters.private & _admin_filter & filters.command("scraper_test"))
async def scraper_test(client: Client, message: Message):
    """/scraper_test <url> — queue a single URL for direct download (no crawling)."""
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply_text("Usage: `/scraper_test https://example.com/video/slug/`")
        return

    url = args[1].strip()
    title = url.rstrip("/").split("/")[-1].replace("-", " ").title() or "Video"
    result = await scraper_db.add_to_queue(
        url=url, source_url=url, title=title,
        admin_chat_id=message.chat.id, skip_duplicates=True,
    )
    if result is None:
        await message.reply_text(f"⚠️ Already in queue or downloaded:\n`{url}`")
    else:
        pending = await scraper_db.count_pending()
        await message.reply_text(
            f"✅ **Queued for direct download**\n\n`{url}`\n⏳ Pending: **{pending}**"
        )


@Client.on_message(filters.private & _admin_filter & filters.command("scraper_status"))
async def scraper_status(client: Client, message: Message):
    """/scraper_status — show queue counts."""
    global _worker_paused
    counts = await scraper_db.count_by_status()
    total_videos = await scraper_db.total_videos()
    state = "⏸ PAUSED" if _worker_paused else "▶️ RUNNING"

    lines = [
        f"📊 **Scraper Status** — {state}\n",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"⏳ Pending:    **{counts.get('pending', 0)}**",
        f"⚙️ Processing: **{counts.get('processing', 0)}**",
        f"✅ Done:       **{counts.get('done', 0)}**",
        f"❌ Failed:     **{counts.get('failed', 0)}**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎬 Videos stored: **{total_videos}**",
    ]
    await message.reply_text("\n".join(lines))


@Client.on_message(filters.private & _admin_filter & filters.command("scrap_clear"))
async def scrap_clear(client: Client, message: Message):
    """/scrap_clear — delete all pending and failed jobs."""
    deleted = await scraper_db.clear_pending_failed()
    await message.reply_text(
        f"🗑 **Queue cleared!**\n\nRemoved **{deleted}** pending/failed job(s)."
    )
