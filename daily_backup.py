"""
daily_backup.py — Daily backup & cleanup for Staging Hub

Operational day: 07:00 IST → 07:00 IST next day.

Timing of the day-end ritual:
  • 07:05 IST → poc_report's dedicated job fills the FINAL hour ("6-7")
                (handled inside poc_report.py, not here)
  • 07:10 IST → THIS module runs and:
                  1. Creates a date-named subfolder (YYYY-MM-DD) inside the Drive
                     parent folder
                  2. Exports Live_Staging → CSV → uploads to Drive subfolder
                  3. Clears Live_Staging data (keep header row)
                  4. Clears Trolley_Registry data (keep header row, no backup)
                  5. Safety-checks the final hour ('6-7') was captured at 07:05
                  6. Appends a fresh empty block to POC_report for the new day
                     (the sheet is append-only — old days stay; NOT archived
                     to Drive since the sheet itself is the permanent record)

Why 07:10 (not 07:00)? The final hour ("6-7") is captured at 07:05 to give
MH Reports time to populate it. The backup waits until 07:10 to ensure that
capture has completed, and to give a 5-minute safety buffer.

Usage:
  Manual run:       python daily_backup.py
  Dry run:          python daily_backup.py --dry-run
  From app.py:      from daily_backup import check_and_run_backup
"""

import csv
import io
import os
import logging
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv
from googleapiclient.http import MediaInMemoryUpload

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("daily_backup")

IST = pytz.timezone("Asia/Kolkata")

# ── Config ────────────────────────────────────────────────────
DRIVE_PARENT_FOLDER_ID = os.getenv("DRIVE_PARENT_FOLDER_ID", "")
# BACKUP_HOUR/BACKUP_MINUTE = 23:59 IST (24-hour format, IST timezone).
# The operational "day" is a simple calendar day (00:00 → 23:59).
# Backup fires at 23:59 and archives TODAY's data.
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "23"))
BACKUP_MINUTE = int(os.getenv("BACKUP_MINUTE", "59"))

# Marker file to track when the last backup ran (avoids double-runs)
LAST_BACKUP_MARKER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".last_backup_date"
)


# ── Drive helpers ─────────────────────────────────────────────

def _find_or_create_subfolder(drive_service, parent_id, folder_name):
    """Find an existing subfolder by name, or create it."""
    query = (
        f"'{parent_id}' in parents "
        f"and name = '{folder_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = drive_service.files().list(
        q=query, spaces="drive", fields="files(id, name)", pageSize=1
    ).execute()
    files = results.get("files", [])

    if files:
        logger.info(f"  Subfolder '{folder_name}' already exists: {files[0]['id']}")
        return files[0]["id"]

    meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = drive_service.files().create(body=meta, fields="id").execute()
    logger.info(f"  Created subfolder '{folder_name}': {folder['id']}")
    return folder["id"]


def _upload_csv(drive_service, folder_id, filename, csv_content):
    """Upload a CSV string as a file to Google Drive (update if exists)."""
    media = MediaInMemoryUpload(
        csv_content.encode("utf-8"), mimetype="text/csv", resumable=False
    )

    query = (
        f"'{folder_id}' in parents "
        f"and name = '{filename}' "
        f"and trashed = false"
    )
    existing = drive_service.files().list(
        q=query, spaces="drive", fields="files(id, name)", pageSize=1
    ).execute().get("files", [])

    if existing:
        file_id = existing[0]["id"]
        uploaded = drive_service.files().update(
            fileId=file_id, media_body=media, fields="id, name"
        ).execute()
        logger.info(f"  Updated existing '{uploaded['name']}' in Drive ({uploaded['id']})")
        return uploaded["id"]

    meta = {"name": filename, "parents": [folder_id]}
    uploaded = drive_service.files().create(
        body=meta, media_body=media, fields="id, name"
    ).execute()
    logger.info(f"  Uploaded '{uploaded['name']}' → Drive ({uploaded['id']})")
    return uploaded["id"]


# ── Sheet helpers ─────────────────────────────────────────────

def _sheet_to_csv(data):
    """Convert a list-of-lists (from gspread) to a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(data)
    return buf.getvalue()


def _clear_sheet_data(sheet):
    """Delete all data rows except the header (row 1), reset formatting to white."""
    row_count = sheet.row_count
    if row_count <= 1:
        logger.info(f"  {sheet.title}: nothing to clear (only header)")
        return 0

    sheet.delete_rows(2, row_count)
    cleared = row_count - 1
    logger.info(f"  {sheet.title}: cleared {cleared} data row(s)")

    try:
        format_to = max(cleared + 1, 500)
        if sheet.row_count < format_to:
            sheet.resize(rows=format_to)
        sheet.format(f"A2:R{format_to}", {
            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
            "textFormat": {"bold": False}
        })
        logger.info(f"  {sheet.title}: reset rows 2-{format_to} to white background")
    except Exception as e:
        logger.warning(f"  {sheet.title}: formatting reset failed (non-critical): {e}")

    return cleared


# ── Marker helpers ────────────────────────────────────────────

def _read_last_backup_date():
    if not os.path.exists(LAST_BACKUP_MARKER):
        return None
    try:
        with open(LAST_BACKUP_MARKER, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _write_last_backup_date(date_str):
    with open(LAST_BACKUP_MARKER, "w") as f:
        f.write(date_str)


# ── One-time: remove Last_Updated from Grid_Registry ──────────

def remove_grid_last_updated():
    """One-time: delete the Last_Updated column from Grid_Registry."""
    from sheets import get_sheet
    try:
        grid_sheet = get_sheet("Grid_Registry")
        headers = grid_sheet.row_values(1)
        col_index = None
        for idx, h in enumerate(headers):
            if h.strip().lower() == "last_updated":
                col_index = idx
                break
        if col_index is None:
            logger.info("Grid_Registry: Last_Updated column not found (already removed?)")
            return False
        grid_sheet.delete_columns(col_index + 1)
        logger.info(f"Grid_Registry: Removed 'Last_Updated' column (was col {col_index + 1})")
        return True
    except Exception as e:
        logger.error(f"Grid_Registry column removal failed: {e}")
        return False


# ── Main backup routine ───────────────────────────────────────

def run_backup(dry_run=False, backup_date=None, force=False):
    """
    Full backup cycle:
      1. Read Live_Staging data
      2. Upload CSV to Drive (date subfolder)
      3. Clear Live_Staging (headers only)
      4. Clear Trolley_Registry (headers only, no backup)

    backup_date: Override the folder name (e.g. '2026-04-29' for a missed
                 backup). Defaults to today's date (calendar day model).
    force:       Skip the same-day guard (only for manual trigger).
    """
    from sheets import get_sheet, get_drive_service, get_cache

    today = datetime.now(IST).strftime("%Y-%m-%d")

    # Calendar day model: data accumulated today (00:00 → 23:59) is archived
    # under today's date.
    if backup_date is None:
        folder_date = today
    else:
        folder_date = backup_date

    if not dry_run and not force:
        last = _read_last_backup_date()
        if last == today:
            logger.warning(f"⚠️ Backup already ran today ({today}) — SKIPPING to protect live data")
            return False
    logger.info(f"═══ Daily Backup — {folder_date} {'(DRY RUN)' if dry_run else ''} ═══")

    if not DRIVE_PARENT_FOLDER_ID:
        logger.error("DRIVE_PARENT_FOLDER_ID not set in .env — aborting")
        return False

    try:
        cache = get_cache()
        if cache._initialized:
            cache.force_sync_now()
            logger.info("  ⚡ Cache writes flushed to Google Sheets")
    except Exception as e:
        logger.warning(f"  Cache flush warning: {e}")

    # ── Step 1: Read Live_Staging ──
    logger.info("Step 1: Reading Live_Staging …")
    live_sheet = get_sheet("Live_Staging")
    live_data = live_sheet.get_all_values()
    data_rows = len(live_data) - 1
    logger.info(f"  Found {data_rows} data row(s)")

    if data_rows <= 0:
        logger.info("  No data to back up — skipping Drive upload")
    else:
        # ── Step 2: Upload to Drive ──
        logger.info("Step 2: Uploading to Google Drive …")
        csv_content = _sheet_to_csv(live_data)

        if not dry_run:
            drive = get_drive_service()
            subfolder_id = _find_or_create_subfolder(drive, DRIVE_PARENT_FOLDER_ID, folder_date)
            filename = f"Live_Staging_{folder_date}.csv"
            _upload_csv(drive, subfolder_id, filename, csv_content)
        else:
            logger.info(f"  [DRY RUN] Would upload {len(csv_content)} bytes to Drive/{folder_date}/")

    # ── Step 3: Clear Live_Staging ──
    logger.info("Step 3: Clearing Live_Staging …")
    if not dry_run:
        _clear_sheet_data(live_sheet)
    else:
        logger.info(f"  [DRY RUN] Would clear {data_rows} row(s)")

    # ── Step 4: Clear Trolley_Registry ──
    logger.info("Step 4: Clearing Trolley_Registry (no backup) …")
    if not dry_run:
        trolley_sheet = get_sheet("Trolley_Registry")
        _clear_sheet_data(trolley_sheet)
    else:
        logger.info("  [DRY RUN] Would clear Trolley_Registry")

    # ── Reload cache after clearing ──
    if not dry_run:
        try:
            cache = get_cache()
            if cache._initialized:
                with cache._lock:
                    cache._dirty_rows = {}
                    cache._append_queue = {}
                cache.clear_data_rows("Live_Staging")
                cache.clear_data_rows("Trolley_Registry")
                cache.full_reload()
                logger.info("  🔄 Cache cleared and reloaded after backup")
        except Exception as e:
            logger.warning(f"  Cache reload warning: {e}")

    if not dry_run:
        _write_last_backup_date(today)
    logger.info(f"═══ Backup complete — {folder_date} ═══\n")
    return True


# ── Startup check (called from app.py) ────────────────────────

def check_and_run_backup():
    """
    Check if a backup was missed (server was off at 23:59).
    ONLY runs if the last backup is from a PREVIOUS day — never on same-day restarts.
    Since backup fires at 23:59, a missed backup means yesterday's data wasn't archived.
    """
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    last = _read_last_backup_date()

    # Guard 1: Already backed up today (or yesterday's data was saved) — skip
    if last == today or last == yesterday:
        # If last == yesterday AND it's already past midnight, that's fine —
        # yesterday's 23:59 backup ran successfully. Today's will run at 23:59.
        logger.info(f"Startup check: backup up to date (last={last}) — skipping")
        return False

    # Guard 2: First-ever start: don't blind-backup
    if not last:
        logger.info(f"Startup check: no backup marker found — initializing to today ({today})")
        logger.info(f"  First backup will run at {BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d} today.")
        _write_last_backup_date(today)
        return False

    # We have a stale marker (older than yesterday) → missed backup.
    # Archive yesterday's data (the most recent complete day).
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
    except ValueError:
        logger.warning(f"Startup check: invalid marker date '{last}' — resetting to today")
        _write_last_backup_date(today)
        return False

    logger.info(f"Missed backup detected! Last: {last}, Today: {today}, "
                f"Archiving yesterday's data: {yesterday}")
    return run_backup(backup_date=yesterday)


# ── Scheduled backup (APScheduler) ────────────────────────────

_scheduler = None

def start_backup_scheduler():
    """
    Background scheduler — runs backup at exactly BACKUP_HOUR:BACKUP_MINUTE
    IST every day. Default: 23:59 IST.
    """
    global _scheduler
    if _scheduler is not None:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            _scheduled_backup_job,
            CronTrigger(hour=BACKUP_HOUR, minute=BACKUP_MINUTE, timezone=IST),
            id="daily_backup_morning",
            name=f"Daily {BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d} IST Backup",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        _scheduler.start()
        logger.info(f"✅ Backup scheduler started — daily at {BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d} IST")
    except ImportError:
        logger.warning("APScheduler not installed — using simple threading fallback")
        _start_simple_scheduler()


def _scheduled_backup_job():
    """Job called by APScheduler at BACKUP_HOUR:BACKUP_MINUTE (23:59 IST).
    Archives today's data (calendar day model: 00:00 → 23:59)."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    last = _read_last_backup_date()
    if last == today:
        logger.info(f"⏰ Scheduled backup skipped — already ran today ({today})")
        return

    logger.info(f"⏰ Scheduled backup triggered ({BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d} IST) — data date: {today}")
    try:
        run_backup(backup_date=today)
    except Exception as e:
        logger.error(f"Scheduled backup failed: {e}")


def _start_simple_scheduler():
    """Threading fallback if APScheduler isn't available."""
    import threading
    import time

    def _loop():
        while True:
            now = datetime.now(IST)
            now_time = (now.hour, now.minute)
            target_time = (BACKUP_HOUR, BACKUP_MINUTE)
            if now_time >= target_time:
                target = now.replace(hour=BACKUP_HOUR, minute=BACKUP_MINUTE, second=0, microsecond=0)
                target += timedelta(days=1)
            else:
                target = now.replace(hour=BACKUP_HOUR, minute=BACKUP_MINUTE, second=0, microsecond=0)

            sleep_secs = (target - now).total_seconds()
            logger.info(f"Simple scheduler: sleeping {sleep_secs:.0f}s until {target}")
            time.sleep(sleep_secs)

            try:
                today = datetime.now(IST).strftime("%Y-%m-%d")
                run_backup(backup_date=today)
            except Exception as e:
                logger.error(f"Scheduled backup failed: {e}")

    t = threading.Thread(target=_loop, name="backup-scheduler", daemon=True)
    t.start()
    logger.info(f"✅ Simple backup scheduler started — daily at {BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d} IST")


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if "--remove-grid-col" in sys.argv:
        remove_grid_last_updated()
        sys.exit(0)

    run_backup(dry_run=dry, force=force)
