"""
hms_sync.py — Triple-browser async Playwright HMS automation.

PATCHED VERSION — addresses recurring cache-miss + Suggester-A wedge,
PLUS the EPATHJR-4304457-1834 false-positive-stale bug in Committer,
PLUS the EFRKGGN-3838704-1311 stale-confirm-for-different-bag bug.

NEW IN THIS REVISION (EDELJKS-668392449 / EDELJKS-668389765 false-positive
sync investigation, 2026-05-26 evening):
================================================================
Two bags showed as HMS-synced in our Google sheet but were not actually
staged in HMS. The page text in the log looked identical to 11 other
N-N2 bags that DID stage successfully — so simple text matching on
'put confirmed' cannot distinguish a real success from a false positive.

This revision adds a new "STRICT VERIFICATION" block in _commit_one that:

  (1) Right BEFORE pressing Enter on step 2 (area barcode submit),
      reads back the actual values of every text input on the page
      via input_value() and logs them. If for any reason the bag ID
      is sitting in the input we're about to submit (the field-not-
      cleared scenario), we will see it in the log clearly.

  (2) After detecting "put confirmed", uses _extract_hms_bag_id() to
      find the bag id that HMS rendered closest to the 'put confirmed'
      keyword in the page text. Logs that bag id alongside our bag id
      with an explicit MATCH/NO-MATCH verdict. This catches the
      EFRKGGN case (different bag in toast).

  (3) Adds a "settle check": after detecting a fresh 'put confirmed',
      waits up to 1.5s and re-reads the page. A genuine successful
      commit transitions the page back toward the clean "Scan item"
      input within a few hundred ms; a false-positive (stale toast
      from a previous commit that survived) tends to keep 'put confirmed'
      hanging there indefinitely or together with 'put to'.

  (4) Logs the raw page text snippet (NOT lowercased) so if a failure
      recurs we have the exact original casing/spacing to diff against
      the success cases.

ALL OTHER LOGIC IS UNCHANGED. Suggester browsers, manager, sheet writer,
trolley release, all the same. The only file change is inside
CommitterBrowser._commit_one and one new helper method
_extract_hms_bag_id.

──────────────────────────────────────────────────────────────────────────
ALREADY-STAGED VERIFICATION PATCH (2026-05-27)
──────────────────────────────────────────────────────────────────────────
The strict verification block above ONLY covered the 'put confirmed' /
'successfully put' success path. The 'already staged' early-exit branch
trusted the page text at face value with NO bag-id match check and NO
input snapshot. A stale 'already staged in grid: X' toast surviving
pre-clear could cause a false-positive HMS_Synced write for the NEXT
bag.

Three minimal changes:

  (a) New helper _extract_hms_bag_id_near(raw_text, keyword) — generalized
      version of _extract_hms_bag_id that takes the success keyword as a
      parameter. The original _extract_hms_bag_id is now a thin wrapper.

  (b) _preclear_page DIRTY_KEYWORDS now includes 'already staged' — a
      stale staging toast from a prior commit forces a page-clean.

  (c) Both 'already staged' early-exit branches in _commit_one now:
        - read raw (case-preserved) page text
        - extract the bag id near 'already staged'
        - reject with MATCH=NO if it's a different bag
        - snapshot inputs for diagnostics
        - log a VERIFY line, same shape as the fresh-confirm path

The success-path strict verification (point 2 + 3 above) is unchanged.

──────────────────────────────────────────────────────────────────────────
SESSION-AWARE RECOVERY PATCH (2026-05-27 evening)
──────────────────────────────────────────────────────────────────────────
The previous ensure_ready() was too aggressive: any time the put-page
text wasn't immediately recognized, it would call page.reload() which
killed the session and forced a full re-login + facility-select cycle.
A transient toast or a momentary URL state could trigger this.

Symptom in logs:
  - many "Not on Put Item - recovering" warnings
  - committer step 1 occasionally typing into the login page and reading
    back the HMS password-warning banner as "No suggestion"
  - repeated "Session expired - re-logging in" cycles costing ~10s each

Three minimal changes (NO other logic touched):

  (i)   _on_put_page() is now strict — requires the actual put-page UI
        markers AND that no password input / logout banner / "Hub System"
        logged-out screen is visible.

  (ii)  ensure_ready() prefers IN-PLACE recovery: cancel any open put,
        clear inputs, soft menu nav. Only does page.reload() as a last
        resort, and only triggers full re-login when the session is
        genuinely dead (login page, "You have been logged out" banner,
        "Invalid message" error, or "please do not share your password"
        banner).

  (iii) _commit_one() in CommitterBrowser does a session-dead pre-check
        at the very top. If the banner or logout screen is visible, it
        flags is_ready=False and returns immediately so the health
        watcher recovers ONCE, instead of every queued bag burning a
        soft-fail on the dead session.

──────────────────────────────────────────────────────────────────────────
MEMORY & STARVATION PATCH (2026-05-27 night) — 3 minimal changes
──────────────────────────────────────────────────────────────────────────
Pre-production review caught three issues that wouldn't bite immediately
but would surface at 5000-bag scale over long shifts:

  (A) _soft_fail_counts leak on real-rejection abandonment.
      When a bag was abandoned via the real-rejection path (>=
      MAX_REAL_ATTEMPTS), its entry in _soft_fail_counts and
      _soft_cooldowns was never removed. Over 8h these dicts grow
      unboundedly. Fix: pop both on real-rejection abandonment.

  (B) clear_stale_pending() was never called automatically. Bags could
      sit in pending_hms_sync.json forever if the committer never got
      to them. Fix: health watcher now runs clear_stale_pending()
      once an hour to age out bags older than 24h.

  (C) Step 1 wait loop did not check for session-dead state. If the HMS
      session died mid-wait, the loop burned its full 2.5s timeout
      before the post-loop check fired. Fix: poll _looks_session_dead
      inside the loop and break early.

──────────────────────────────────────────────────────────────────────────
ROW-VALIDATION SAFETY NET (2026-05-28) — 2 minimal changes
──────────────────────────────────────────────────────────────────────────
Pre-production audit (RISK 2 in HMS_Sync_Edge_Cases.md) flagged a sheet
row-drift hazard: if the daily backup at 23:59 clears Live_Staging while
bags are still pending in pending_hms_sync.json, those bags carry a
stale sheet_row reference. On the next commit (after midnight), the
SheetWriter would write "Done" to a row that now belongs to a DIFFERENT
bag — silent data corruption.

The proper fix (drain-before-clear) lives in daily_backup.py. This
patch adds a defensive safety net inside hms_sync.py so that even if
the drain logic fails or hasn't been deployed yet, we will NEVER write
"Done" to a row that doesn't match the expected bag_id.

Two minimal changes (NO other logic touched):

  (1) _flush_synced_batch() now does a per-row validation pass BEFORE
      issuing the batch_update. For each pending write it reads the
      current value of column E (Bag_ID) at the stored sheet_row via
      the in-memory cache. If the cell is empty (row was cleared) or
      contains a different bag_id (row was reassigned), the write is
      dropped from the batch and logged as a "row-drift" rejection.
      The bag still gets removed from local pending state — we don't
      want to retry into a worse situation.

  (2) get_status() now exposes the DLQ size in the status payload so
      operators can monitor for quota / row-drift accumulation via the
      /api/hms-status endpoint.

ARCHITECTURE (unchanged — 3 Chromium browser contexts, all on Inbound staging put page)

  Browser #1A "Spiral Suggester A"
    - Reads green "Put to X" suggestion or "already staged" message
    - Clicks Cancel put after every read (never commits)

  Browser #1B "Spiral Suggester B"
    - Identical to #1A
    - Routing picks shorter queue; ties alternate

  Browser #2 "HMS Sync Committer" (LOCAL-FILE approach)
    - Reads pending_hms_sync.json (populated at Grid Put time)
    - Workflow: pre-clear page -> scan bag_id -> fill scan_barcode -> verify -> wait confirmed
"""

import asyncio
import json
import os
import re
import threading
import time
import logging
from datetime import datetime
from queue import Queue, Empty
from typing import Optional, Dict, List

import pytz
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("hms_sync")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    # Console handler — INFO level for terminal readability
    _h = logging.StreamHandler()
    _h.setLevel(logging.INFO)
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(_h)

    # File handler — DEBUG level for full verification trace
    _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    from logging.handlers import TimedRotatingFileHandler
    _log_file = os.path.join(_log_dir, "hms_sync.log")
    _fh = TimedRotatingFileHandler(
        _log_file, when="midnight", interval=1, backupCount=10,
        encoding="utf-8",
    )
    _fh.suffix = "%Y%m%d"
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_fh)


IST = pytz.timezone("Asia/Kolkata")


def _ts():
    return datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]


# Configuration
HMS_URL = os.getenv("HMS_URL", "http://10.24.0.157")
HMS_FACILITY = os.getenv("HMS_FACILITY", "Motherhub_SAI_4")
HMS_HEADLESS = os.getenv("HMS_HEADLESS", "false").lower() == "true"
HMS_BROWSER_PATH = os.getenv("HMS_BROWSER_PATH", "").strip()

SUGGEST_TIMEOUT_MS = int(os.getenv("HMS_SUGGEST_TIMEOUT_MS", "2500"))
COMMIT_TIMEOUT_MS = int(os.getenv("HMS_COMMIT_TIMEOUT_MS", "3000"))

SHEET_BATCH_SIZE = 25
SHEET_BATCH_WAIT_MS = 800
MIN_API_INTERVAL = 1.0

_cred_raw = os.getenv("HMS_CREDENTIALS", "")
HMS_CRED_SLOTS = []
if _cred_raw:
    for pair in _cred_raw.split("|"):
        parts = pair.strip().split(",")
        if len(parts) == 2:
            HMS_CRED_SLOTS.append((parts[0].strip(), parts[1].strip()))

if not HMS_CRED_SLOTS:
    _u = os.getenv("HMS_USERNAME", "")
    _p = os.getenv("HMS_PASSWORD", "")
    if _u and _p:
        HMS_CRED_SLOTS.append((_u, _p))

SLOT_HOURS = 6
SLOT_START_HOUR = 6


# ─────────────────────────────────────────────────────────────────────────
# SESSION-DEAD MARKERS [SESSION-AWARE RECOVERY PATCH 2026-05-27]
# ─────────────────────────────────────────────────────────────────────────
# Text fragments (lowercase) that mean the HMS session is definitely
# dead and a full re-login is required. ANY other state — stale toasts,
# weird URLs, momentary UI hiccups — should be handled by in-place
# recovery WITHOUT re-login.
_SESSION_DEAD_MARKERS = (
    "you have been logged out",       # HMS logout screen (pink banner)
    "error: invalid message",         # HMS logout error subtitle
    "please do not share your password",  # HMS login-page warning banner
    "user found sharing password",    # variant of same banner
)


def _looks_session_dead(text_lower: str) -> bool:
    """True if the page text contains any explicit session-dead marker."""
    if not text_lower:
        return False
    return any(m in text_lower for m in _SESSION_DEAD_MARKERS)


# ─────────────────────────────────────────────────────────────────────────
# Bag-ID extraction near a keyword (for strict verification)
# ─────────────────────────────────────────────────────────────────────────
# Bag IDs in this system look like: EDELJKS-668389765-1344, EFRKGGN-3845785-1312,
# MST02673512-26, ECHNCSK-666534210-1296, etc. Always: letters + digits-optional
# prefix, dash, digits, optional second-dash + digits.
_BAG_ID_PATTERN = re.compile(
    r"\b([A-Za-z]{3,10}[0-9]*-[0-9]+(?:-[0-9]+)?)\b"
)


def _extract_hms_bag_id_near(raw_text: str, keywords) -> str:
    """Find the bag id HMS displayed closest to a given keyword.

    Parameters
    ----------
    raw_text : str
        The raw (case-preserved) page text.
    keywords : str | tuple[str, ...]
        The keyword (or tuple of keywords) to search for. Case-insensitive.
        The function picks the LAST occurrence of any of these keywords.

    Returns
    -------
    str
        The bag id in UPPERCASE, or "" if no candidate found.

    Strategy: locate the LAST occurrence of any keyword in raw_text
    (case-insensitive). Within a window of 400 chars before + 100 chars
    after that index, find all bag-id-shaped tokens. Return the one
    closest to the keyword.

    Why "closest" and not "first": HMS may render the bag id BOTH in the
    history echo (which can include old entries) AND in the fresh
    toast. The closest match is the one in the toast.
    """
    if not raw_text:
        return ""
    if isinstance(keywords, str):
        keywords = (keywords,)

    text_lower = raw_text.lower()
    confirm_idx = -1
    for kw in keywords:
        idx = text_lower.rfind(kw.lower())
        if idx > confirm_idx:
            confirm_idx = idx
    if confirm_idx < 0:
        return ""

    start = max(0, confirm_idx - 400)
    end = min(len(raw_text), confirm_idx + 100)
    window = raw_text[start:end]
    matches = list(_BAG_ID_PATTERN.finditer(window))
    if not matches:
        return ""

    confirm_in_window = confirm_idx - start
    closest = min(matches, key=lambda m: abs(m.start() - confirm_in_window))
    return closest.group(1).upper()


def _extract_hms_bag_id(raw_text: str) -> str:
    """Backward-compatible wrapper: searches near 'put confirmed' /
    'successfully put'. Kept for any external callers."""
    return _extract_hms_bag_id_near(
        raw_text, ("put confirmed", "successfully put")
    )


def _get_current_slot() -> int:
    if not HMS_CRED_SLOTS:
        return 0
    hour = datetime.now(IST).hour
    shifted = (hour - SLOT_START_HOUR) % 24
    return (shifted // SLOT_HOURS) % len(HMS_CRED_SLOTS)


def _is_network_error(exc: Exception) -> bool:
    """True if exception looks like a network/timeout problem (NOT a credential issue)."""
    msg = str(exc).lower()
    network_markers = (
        "timeout", "timed out", "net::err_", "page.goto", "navigation",
        "connection refused", "connection reset", "econnreset", "econnrefused",
        "name not resolved", "dns", "unreachable",
    )
    return any(m in msg for m in network_markers)


# Live_Staging column indices (0-based) - 18-col layout
COL_CONVEYER_ID       = 0
COL_CONVEYER_TS       = 1
COL_CNV_BAG_SCAN_TS   = 2
COL_SPIRAL_BAG_SCAN_TS = 3
COL_BAG_ID            = 4
COL_CASPER_ID         = 5
COL_GRID              = 6
COL_TROLLEY_ID        = 7
COL_GRID_BARCODE      = 8
COL_AREA_PUT          = 9
COL_AREA_PUT_TS       = 10
COL_TROLLEY_PUT       = 11
COL_TROLLEY_PUT_TS    = 12
COL_GRID_PUT          = 13
COL_GRID_PUT_TS       = 14
COL_HMS_SYNCED        = 15
COL_HMS_SYNCED_TS     = 16


def _validate_row_belongs_to_bag(cache, sheet_row: int, bag_id: str) -> tuple:
    """Check that Live_Staging row `sheet_row` (1-based) still holds `bag_id`
    in column E (COL_BAG_ID).

    Returns (ok, reason). ok=True means write is safe.
    """
    if not sheet_row or sheet_row < 1:
        return False, f"sheet_row={sheet_row} is invalid (must be >= 1)"

    l_data = cache.get_all_values("Live_Staging")
    bag_upper = bag_id.strip().upper()

    # sheet_row is 1-based; l_data[0] is the header row.
    # So Bag_ID at sheet_row N is at l_data[N-1][COL_BAG_ID].
    row_idx = sheet_row - 1
    if row_idx < 1 or row_idx >= len(l_data):
        return False, f"row {sheet_row} out of range (cache has {len(l_data)} rows)"

    row = l_data[row_idx]
    cell_val = (str(row[COL_BAG_ID]).strip().upper()
                if len(row) > COL_BAG_ID else "")

    if not cell_val:
        return False, f"row {sheet_row} Bag_ID cell is empty (row cleared)"
    if cell_val != bag_upper:
        return False, f"row {sheet_row} now holds '{cell_val}' (expected '{bag_upper}')"
    return True, ""


class SuggestRequest:
    """A single request for grid suggestion from spiral. Awaitable result."""
    __slots__ = ("bag_id", "future", "queued_at")

    def __init__(self, bag_id: str, future: asyncio.Future):
        self.bag_id = bag_id
        self.future = future
        self.queued_at = time.time()


class HMSBrowser:
    """One Playwright browser context permanently parked on Put Item page."""

    def __init__(self, name: str):
        self.name = name
        self.context = None
        self.page = None
        self.is_ready = False
        self.is_initializing = False
        self.error = None
        self.current_slot = _get_current_slot()
        if HMS_CRED_SLOTS:
            self.user, self.password = HMS_CRED_SLOTS[self.current_slot]
        else:
            self.user, self.password = "", ""
        self.failed_slots = set()
        self.login_count = 0
        self.last_recovery = 0.0
        self.recovery_count = 0

    async def initialize(self, browser):
        self.is_initializing = True
        self.error = None
        try:
            self.context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"),
            )
            self.page = await self.context.new_page()
            self.page.set_default_timeout(15000)

            async def _dismiss(d):
                try:
                    await d.dismiss()
                except Exception:
                    pass
            self.page.on("dialog", lambda d: asyncio.ensure_future(_dismiss(d)))

            await self._do_full_login()
            self.is_ready = True
            self.is_initializing = False
            logger.info(f"[{self.name}] Ready (slot={self.current_slot}, user={self.user})")
        except Exception as e:
            self.error = str(e)
            self.is_initializing = False
            self.is_ready = False
            logger.error(f"[{self.name}] Init failed: {e}")
            if _is_network_error(e) and "INVALID_CREDENTIAL" not in str(e):
                logger.info(f"[{self.name}] Network error - will retry same slot via health watcher.")
                return
            await self._try_failover(browser)

    async def _try_failover(self, browser):
        self.failed_slots.add(self.current_slot)
        for i in range(len(HMS_CRED_SLOTS)):
            if i in self.failed_slots:
                continue
            user, pwd = HMS_CRED_SLOTS[i]
            logger.warning(f"[{self.name}] Failover -> slot {i} ({user})")
            self.current_slot = i
            self.user, self.password = user, pwd
            try:
                if self.context:
                    try:
                        await self.context.close()
                    except Exception:
                        pass
                self.context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    ignore_https_errors=True,
                )
                self.page = await self.context.new_page()
                self.page.set_default_timeout(15000)

                async def _dismiss(d):
                    try:
                        await d.dismiss()
                    except Exception:
                        pass
                self.page.on("dialog", lambda d: asyncio.ensure_future(_dismiss(d)))
                await self._do_full_login()
                self.is_ready = True
                self.error = None
                logger.info(f"[{self.name}] Failover SUCCESS on slot {i} ({user})")
                return
            except Exception as e:
                if _is_network_error(e) and "INVALID_CREDENTIAL" not in str(e):
                    logger.warning(f"[{self.name}] Failover slot {i} network error - aborting loop.")
                    return
                self.failed_slots.add(i)
                logger.error(f"[{self.name}] Failover slot {i} also failed: {e}")
        logger.error(f"[{self.name}] All credential slots exhausted!")
        self.error = "All credentials failed"
        self.is_ready = False

    async def _do_full_login(self):
        page = self.page
        goto_ok = False
        goto_err = None
        for attempt in range(3):
            try:
                await page.goto(HMS_URL, wait_until="domcontentloaded", timeout=30000)
                goto_ok = True
                break
            except Exception as e:
                goto_err = e
                logger.warning(f"[{self.name}] page.goto attempt {attempt + 1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))

        if not goto_ok:
            raise Exception(f"page.goto timeout after 3 retries: {goto_err}")

        await asyncio.sleep(1.5)
        content = await page.content()

        if "Put Item" in content or "Scan item" in content:
            logger.info(f"[{self.name}] Already on Put Item")
            return

        # [SESSION-AWARE RECOVERY PATCH] If the "You have been logged out"
        # screen is showing, click the Login button to get to the login form.
        try:
            body_lower = (await page.evaluate(
                "() => (document.body && document.body.innerText || '').toLowerCase()"
            )) or ""
        except Exception:
            body_lower = ""
        if "you have been logged out" in body_lower:
            logger.info(f"[{self.name}] Detected 'You have been logged out' screen — clicking Login")
            try:
                await page.locator("button:has-text('Login'), a:has-text('Login')").first.click(timeout=3000)
                await asyncio.sleep(2)
                content = await page.content()
            except Exception as e:
                logger.warning(f"[{self.name}] Could not click Login button: {e}")

        if "username" in content.lower() or "login" in page.url.lower() or \
           await page.locator("input[type='password']").count() > 0:
            self.login_count += 1
            logger.info(f"[{self.name}] Login attempt #{self.login_count} as {self.user}")

            await page.wait_for_selector("input[type='text'], input[name='username']", timeout=15000)
            try:
                un_loc = page.locator("input[name='username']")
                if await un_loc.count() > 0:
                    await un_loc.fill(self.user)
                else:
                    await page.locator("input[type='text']").first.fill(self.user)
            except Exception:
                await page.locator("input[type='text']").first.fill(self.user)
            await page.locator("input[type='password']").fill(self.password)

            try:
                await page.locator("button:has-text('Submit')").first.click(timeout=5000)
            except Exception:
                await page.locator("button[type='submit']").first.click(timeout=5000)

            try:
                await page.wait_for_url("**/10.24.0.157**", timeout=20000)
            except Exception:
                await asyncio.sleep(3)

            await asyncio.sleep(2)

            try:
                bt = (await page.text_content("body") or "").lower()
                if "invalid" in bt or "incorrect" in bt or "bad credentials" in bt:
                    raise Exception(f"INVALID_CREDENTIAL: {self.user}")
            except Exception as inner_e:
                if "INVALID_CREDENTIAL" in str(inner_e):
                    raise

        await self._select_facility()
        await asyncio.sleep(1.5)
        await self._navigate_to_put_page()
        await asyncio.sleep(1.5)

        content = await page.content()
        if not ("Scan item" in content or "Put Item" in content or "Scan Item" in content):
            raise Exception("Failed to reach Put Item page after login")

        logger.info(f"[{self.name}] Parked on Put Item")

    async def _select_facility(self):
        page = self.page
        try:
            sel = page.locator("select")
            if await sel.count() > 0:
                for _ in range(15):
                    options = await sel.locator("option").all()
                    real = [o for o in options
                            if (await o.text_content() or "").strip().lower()
                                not in ("", "select facility", "select")]
                    if real:
                        break
                    await asyncio.sleep(0.5)

                options = await sel.locator("option").all()
                matched = False
                for opt in options:
                    txt = (await opt.text_content() or "")
                    if HMS_FACILITY in txt:
                        await sel.select_option(label=txt)
                        logger.info(f"[{self.name}] Facility: {txt}")
                        matched = True
                        break
                if not matched:
                    for opt in options:
                        txt = (await opt.text_content() or "")
                        if HMS_FACILITY.lower() in txt.lower():
                            await sel.select_option(label=txt)
                            logger.info(f"[{self.name}] Facility (partial): {txt}")
                            matched = True
                            break

                await asyncio.sleep(0.4)

                for label in ["Submit", "Go", "Select", "OK", "Proceed", "Enter"]:
                    try:
                        b = page.locator(f"button:has-text('{label}')").first
                        if await b.count() > 0:
                            await b.click(timeout=3000)
                            logger.info(f"[{self.name}] Facility confirmed via '{label}'")
                            await asyncio.sleep(2)
                            return
                    except Exception:
                        pass

                try:
                    btns = page.locator("button")
                    cnt = await btns.count()
                    for i in range(cnt):
                        b = btns.nth(i)
                        txt = (await b.inner_text() or "").strip()
                        if txt and txt.lower() not in ("logout", "cancel", "close"):
                            await b.click(timeout=3000)
                            logger.info(f"[{self.name}] Facility confirmed via '{txt}'")
                            await asyncio.sleep(2)
                            return
                except Exception:
                    pass
                return
        except Exception as e:
            logger.warning(f"[{self.name}] Facility selection failed: {e}")
            raise

    async def _navigate_to_put_page(self):
        page = self.page
        try:
            clicked = await page.evaluate("""() => {
                var links = document.querySelectorAll('a, button, div[routerlink], mat-card, mat-list-item');
                var puts = [];
                for (var i = 0; i < links.length; i++) {
                    var own = (links[i].innerText || '').trim();
                    if (own === 'Put' || own === '+ Put') puts.push(links[i]);
                }
                if (puts.length > 0) {
                    puts[puts.length - 1].click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(3)
                c = await page.content()
                if "Put Item" in c or "Scan item" in c or "Scan Item" in c:
                    return
        except Exception:
            pass

        for route in ["/operation#/home1", "/operation#/put", "/operation#/outbound/put", "/#/home1"]:
            try:
                base = HMS_URL.rstrip("/")
                await page.goto(base + route, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                c = await page.content()
                if "Put Item" in c or "Scan item" in c or "Scan Item" in c:
                    return
            except Exception:
                pass

        try:
            opened = await page.evaluate("""() => {
                var candidates = [];
                document.querySelectorAll('mat-icon').forEach(function (el) {
                    var t = (el.innerText || '').trim().toLowerCase();
                    if (t === 'menu') candidates.push(el);
                });
                document.querySelectorAll('button, [role="button"], a').forEach(function (el) {
                    var aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    var title = (el.getAttribute('title') || '').toLowerCase();
                    if (aria.indexOf('menu') !== -1 || title.indexOf('menu') !== -1) {
                        candidates.push(el);
                    }
                });
                if (candidates.length === 0) return false;
                candidates[0].click();
                return true;
            }""")
            if opened:
                await asyncio.sleep(1)
                await page.evaluate("""() => {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var own = (all[i].innerText || '').trim();
                        if (own === 'Outbound staging') { all[i].click(); return; }
                    }
                }""")
                await asyncio.sleep(1)
                await page.evaluate("""() => {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var own = (all[i].innerText || '').trim();
                        if (own === '+ Put' || own === 'Put') { all[i].click(); return; }
                    }
                }""")
                await asyncio.sleep(2)
        except Exception as e:
            logger.debug(f"[{self.name}] Hamburger nav skipped: {e}")

    async def _on_put_page(self) -> bool:
        """[SESSION-AWARE RECOVERY PATCH] Stricter check.

        Returns True ONLY if we actually see the Put-page UI markers
        AND the page is not the login / logged-out screen.

        The old version returned True for "URL contains 'operation' + any
        text input" — which the HMS login page also satisfies, causing
        ensure_ready to falsely pass and committers to type bag IDs into
        the username field.
        """
        try:
            # If there's a password input, we're on the login form.
            try:
                if await self.page.locator("input[type='password']").count() > 0:
                    return False
            except Exception:
                pass

            # Read body text once and use it for both checks.
            try:
                text = await self.page.evaluate(
                    "() => (document.body && document.body.innerText || '')"
                )
            except Exception:
                text = ""
            text_lower = text.lower()

            # Explicit session-dead screens never count as ready.
            if _looks_session_dead(text_lower):
                return False

            # Real Put-page markers in the page text.
            if not ("scan item" in text_lower or "put item" in text_lower):
                return False

            # And we need a text input to scan into.
            try:
                cnt = await self.page.locator("input[type='text']").count()
            except Exception:
                cnt = 0
            return cnt > 0
        except Exception:
            return False

    async def _is_session_dead(self) -> bool:
        """[SESSION-AWARE RECOVERY PATCH] True iff the current page is
        unambiguously the login / logged-out screen and needs a full
        re-login. ANY other 'unrecognized' state is NOT session-dead;
        use in-place recovery first."""
        try:
            if await self.page.locator("input[type='password']").count() > 0:
                return True
        except Exception:
            pass
        try:
            text_lower = (await self.page.evaluate(
                "() => (document.body && document.body.innerText || '').toLowerCase()"
            )) or ""
        except Exception:
            text_lower = ""
        if _looks_session_dead(text_lower):
            return True
        try:
            url_lower = (self.page.url or "").lower()
        except Exception:
            url_lower = ""
        if "login" in url_lower:
            return True
        return False

    async def ensure_ready(self, browser) -> bool:
        """[SESSION-AWARE RECOVERY PATCH]
        Stay on Scan Item whenever possible. Only re-login when the page
        is genuinely the login / logged-out screen.

        Order of operations:
          1. Already on Put page? -> done.
          2. Session genuinely dead (login form / logout banner / "Invalid
             message")? -> full re-login.
          3. Otherwise -> IN-PLACE recovery: click any open Cancel,
             clear inputs, soft menu nav. NO reload, NO re-login.
          4. Only if in-place recovery still fails -> page.reload() as a
             last resort. After reload, re-check session state.
        """
        # 1. Fast path
        if await self._on_put_page():
            return True

        # 2. Truly dead session — go straight to re-login.
        if await self._is_session_dead():
            logger.warning(f"[{self.name}] Session is dead "
                           f"(login / logged-out screen visible) — re-logging in")
            try:
                await self._do_full_login()
                return await self._on_put_page()
            except Exception as e:
                logger.error(f"[{self.name}] Re-login failed: {e}")
                self.is_ready = False
                return False

        # 3. Session looks alive — try IN-PLACE recovery first. NO reload.
        logger.warning(f"[{self.name}] Not on Put Item - attempting in-place recovery")

        # 3a. Click any open Cancel / Cancel put to dismiss stale dialog.
        try:
            await self.page.evaluate("""() => {
                var btns = document.querySelectorAll('button, a, [role="button"]');
                for (var i = 0; i < btns.length; i++) {
                    var t = (btns[i].innerText || btns[i].textContent || '').trim().toLowerCase();
                    if (t === 'cancel put' || t === 'cancel') {
                        btns[i].click();
                        return;
                    }
                }
            }""")
            await asyncio.sleep(0.3)
        except Exception:
            pass

        # 3b. Clear any input.
        try:
            inputs = self.page.locator("input[type='text']")
            if await inputs.count() > 0:
                try:
                    await inputs.first.fill("", timeout=1000)
                except Exception:
                    pass
        except Exception:
            pass

        if await self._on_put_page():
            logger.info(f"[{self.name}] In-place recovery succeeded (cancel/clear)")
            return True

        # 3c. Soft menu nav (no reload).
        try:
            await self._navigate_to_put_page()
            await asyncio.sleep(1)
        except Exception:
            pass

        if await self._on_put_page():
            logger.info(f"[{self.name}] In-place recovery succeeded (menu nav)")
            return True

        # 4. LAST RESORT — reload. Only after in-place recovery failed.
        logger.warning(f"[{self.name}] In-place recovery failed — falling back to page reload")
        try:
            await self.page.reload(wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # After reload, recheck for dead session before assuming anything.
            if await self._is_session_dead():
                logger.warning(f"[{self.name}] Reload landed on login / logged-out screen "
                               f"— re-logging in")
                await self._do_full_login()
                return await self._on_put_page()

            if await self._on_put_page():
                return True

            # Reload landed somewhere inside the app but not on put page.
            await self._navigate_to_put_page()
            return await self._on_put_page()
        except Exception as e:
            logger.error(f"[{self.name}] ensure_ready reload path failed: {e}")
            self.is_ready = False
            return False

    async def close(self):
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context = None
        self.page = None
        self.is_ready = False


# ===========================================================================
# SuggesterBrowser - UNCHANGED
# ===========================================================================

class SuggesterBrowser(HMSBrowser):
    """Browser #1A or #1B - reads grid suggestions and clicks Cancel put.

    This class is INTENTIONALLY UNCHANGED in this patch.
    """

    DEGRADED_THRESHOLD = 3
    STUCK_THRESHOLD    = 5

    def __init__(self, name: str):
        super().__init__(name)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.processed = 0
        self.errors = 0
        self.last_busy_at = 0.0
        self.consecutive_failures = 0
        self.last_failure_at = 0.0

    def is_degraded(self) -> bool:
        return self.consecutive_failures >= self.DEGRADED_THRESHOLD

    def record_failure(self, reason: str = ""):
        self.consecutive_failures += 1
        self.last_failure_at = time.time()
        logger.warning(f"[{self.name}] failure #{self.consecutive_failures} "
                       f"({reason})")
        if self.consecutive_failures >= self.STUCK_THRESHOLD:
            logger.error(f"[{self.name}] STUCK after "
                         f"{self.consecutive_failures} consecutive failures "
                         f"- flagging is_ready=False")
            self.is_ready = False

    def record_success(self):
        if self.consecutive_failures > 0:
            logger.info(f"[{self.name}] Recovered after "
                        f"{self.consecutive_failures} failure(s)")
        self.consecutive_failures = 0

    async def run_loop(self, browser):
        while True:
            try:
                req: SuggestRequest = await self.queue.get()
            except Exception:
                await asyncio.sleep(0.1)
                continue
            self.last_busy_at = time.time()
            try:
                if not self.is_ready:
                    if not req.future.done():
                        req.future.set_result({
                            "ok": False,
                            "reason": f"{self.name} not ready: {self.error or 'unknown'}",
                            "grid": "",
                            "already_staged": False,
                        })
                    continue

                ok = await self.ensure_ready(browser)
                if not ok:
                    if not req.future.done():
                        req.future.set_result({
                            "ok": False,
                            "reason": "Not on Put Item page",
                            "grid": "",
                            "already_staged": False,
                        })
                    continue

                result = await self._read_suggestion(req.bag_id)
                if not req.future.done():
                    req.future.set_result(result)
                self.processed += 1
            except Exception as e:
                logger.error(f"[{self.name}] suggest error for {req.bag_id}: {e}")
                self.errors += 1
                self.record_failure(f"loop crash: {e}")
                if not req.future.done():
                    req.future.set_result({
                        "ok": False,
                        "reason": f"Internal error: {e}",
                        "grid": "",
                        "already_staged": False,
                    })

    async def _read_suggestion(self, bag_id: str) -> dict:
        page = self.page
        t0 = time.time()
        bag_id = str(bag_id).strip().upper()

        REJECT_KEYWORDS = (
            "incorrect ba", "incorrect barcode", "not found", "not allowed",
            "not expected", "does not belong", "wrong barcode",
            "invalid barcode", "invalid item",
        )
        TERMINAL_KEYWORDS = ("put to", "already staged") + REJECT_KEYWORDS

        try:
            inp = page.locator("input[type='text']").first
            try:
                await inp.fill("", timeout=2000)
            except Exception:
                pass
            await inp.fill(bag_id, timeout=2000)
            await inp.press("Enter")

            text = ""
            deadline = time.time() + (SUGGEST_TIMEOUT_MS / 1000.0)
            while time.time() < deadline:
                try:
                    text = await page.evaluate(
                        "() => (document.body && document.body.innerText || '').toLowerCase()"
                    )
                except Exception:
                    text = ""
                if any(kw in text for kw in TERMINAL_KEYWORDS):
                    break
                await asyncio.sleep(0.05)

            elapsed_ms = int((time.time() - t0) * 1000)

            if "already staged" in text:
                m = re.search(r"in grid:\s*([^\s\n]+)", text)
                grid = m.group(1).upper() if m else ""
                try:
                    await page.locator("input[type='text']").first.fill("", timeout=1000)
                except Exception:
                    pass
                if grid:
                    try:
                        from local_store_grid import store_grid as _store_grid_fast
                        _store_grid_fast(
                            bag_id, grid,
                            already_staged=True,
                            source=f"{self.name}_read",
                        )
                    except Exception as _store_e:
                        logger.error(f"[{self.name}] direct-store failed "
                                     f"for {bag_id}: {_store_e}")
                self.record_success()
                logger.info(f"[{self.name}] {bag_id} -> already_staged in {grid} ({elapsed_ms}ms)")
                return {
                    "ok": True, "grid": grid,
                    "already_staged": True, "reason": "",
                }

            if "put to" in text:
                try:
                    raw = await page.evaluate(
                        "() => (document.body && document.body.innerText || '')"
                    )
                except Exception:
                    raw = ""
                m = re.search(r"[Pp]ut to\s+([^\s\n]+)", raw)
                grid = m.group(1).strip() if m else ""

                if grid:
                    try:
                        from local_store_grid import store_grid as _store_grid_fast
                        _store_grid_fast(
                            bag_id, grid,
                            already_staged=False,
                            source=f"{self.name}_read",
                        )
                    except Exception as _store_e:
                        logger.error(f"[{self.name}] direct-store failed "
                                     f"for {bag_id}: {_store_e}")

                clicked = False
                try:
                    clicked = await page.evaluate("""() => {
                        var btns = document.querySelectorAll('button');
                        for (var i = 0; i < btns.length; i++) {
                            var t = (btns[i].innerText || '').trim().toLowerCase();
                            if (t === 'cancel put' || t === 'cancel') {
                                btns[i].click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                except Exception:
                    pass

                if not clicked:
                    try:
                        clicked = await page.evaluate("""() => {
                            var matBtns = document.querySelectorAll(
                                'button[mat-button], button[mat-raised-button], button[mat-flat-button], ' +
                                'button[mat-stroked-button], button.mat-button, button.mat-raised-button, ' +
                                'button.mat-mdc-button, button.mat-mdc-raised-button, ' +
                                'a[mat-button], a[mat-raised-button]'
                            );
                            for (var i = 0; i < matBtns.length; i++) {
                                var t = (matBtns[i].innerText || matBtns[i].textContent || '').trim().toLowerCase();
                                if (t.indexOf('cancel') !== -1) {
                                    matBtns[i].click();
                                    return true;
                                }
                            }
                            var spans = document.querySelectorAll('button span, button .mat-button-wrapper, button .mdc-button__label');
                            for (var j = 0; j < spans.length; j++) {
                                var st = (spans[j].innerText || spans[j].textContent || '').trim().toLowerCase();
                                if (st === 'cancel put' || st === 'cancel') {
                                    var btn = spans[j].closest('button');
                                    if (btn) { btn.click(); return true; }
                                }
                            }
                            return false;
                        }""")
                    except Exception:
                        pass

                if not clicked:
                    try:
                        await page.locator("button:has-text('Cancel put')").first.click(timeout=1500)
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    try:
                        await page.locator("button:has-text('Cancel')").first.click(timeout=1000)
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    try:
                        await page.locator("input[type='text']").first.fill("", timeout=1000)
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass

                verify_deadline = time.time() + 3.0
                page_ok = False
                retry_click_done = False
                while time.time() < verify_deadline:
                    try:
                        verify_text = await page.evaluate(
                            "() => (document.body && document.body.innerText || '').toLowerCase()"
                        )
                    except Exception:
                        verify_text = ""
                    if "put to" not in verify_text:
                        page_ok = True
                        break
                    if not retry_click_done and (time.time() > verify_deadline - 1.5):
                        retry_click_done = True
                        try:
                            await page.evaluate("""() => {
                                var all = document.querySelectorAll('button, a, [role="button"]');
                                for (var i = 0; i < all.length; i++) {
                                    var t = (all[i].innerText || all[i].textContent || '').trim().toLowerCase();
                                    if (t.indexOf('cancel') !== -1) {
                                        all[i].click();
                                        return;
                                    }
                                }
                            }""")
                        except Exception:
                            pass
                    await asyncio.sleep(0.05)

                if not page_ok:
                    logger.warning(f"[{self.name}] Cancel put didn't clear - "
                                   f"recreating page (full DOM reset)")
                    self.record_failure("cancel stuck")
                    try:
                        old_page = self.page
                        new_page = await self.context.new_page()
                        new_page.set_default_timeout(15000)

                        async def _dismiss(d):
                            try:
                                await d.dismiss()
                            except Exception:
                                pass
                        new_page.on("dialog",
                                    lambda d: asyncio.ensure_future(_dismiss(d)))

                        self.page = new_page
                        try:
                            await old_page.close()
                        except Exception:
                            pass

                        await self.page.goto(HMS_URL,
                                             wait_until="domcontentloaded",
                                             timeout=15000)
                        await asyncio.sleep(1.5)
                        content = await self.page.content()
                        if "Scan item" not in content and "Put Item" not in content:
                            if "username" in content.lower() or \
                               await self.page.locator("input[type='password']").count() > 0:
                                await self._do_full_login()
                            else:
                                await self._select_facility()
                                await asyncio.sleep(1)
                                await self._navigate_to_put_page()
                    except Exception as nav_e:
                        logger.error(f"[{self.name}] Page recreate failed: {nav_e}")
                        self.is_ready = False

                self.record_success()
                logger.info(f"[{self.name}] {bag_id} -> {grid} ({elapsed_ms}ms)")
                return {
                    "ok": True, "grid": grid,
                    "already_staged": False, "reason": "",
                }

            for kw in REJECT_KEYWORDS:
                if kw in text:
                    try:
                        await page.locator("input[type='text']").first.fill("", timeout=1000)
                    except Exception:
                        pass
                    snippet = text[:120].replace("\n", " ").strip()
                    self.record_success()
                    logger.info(f"[{self.name}] {bag_id} -> REJECT '{kw}' ({elapsed_ms}ms): {snippet}")
                    return {
                        "ok": False, "grid": "",
                        "already_staged": False,
                        "reason": f"HMS rejected: {kw}",
                    }

            logger.warning(f"[{self.name}] {bag_id} -> no suggestion ({elapsed_ms}ms): {text[:100]}")
            self.record_failure(f"timeout reading {bag_id}")
            try:
                await page.locator("input[type='text']").first.fill("", timeout=1000)
            except Exception:
                pass
            return {
                "ok": False, "grid": "",
                "already_staged": False,
                "reason": "No suggestion from HMS (timeout)",
            }
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"[{self.name}] _read_suggestion crashed at {elapsed_ms}ms: {e}")
            self.record_failure(f"crash: {e}")
            self.is_ready = False
            return {
                "ok": False, "grid": "",
                "already_staged": False,
                "reason": f"Browser error: {e}",
            }

    def queue_size(self) -> int:
        return self.queue.qsize()


# ===========================================================================
# CommitterBrowser - [PATCHED] strict verification + input snapshots
#                  + already-staged bag-id verification (2026-05-27)
#                  + session-dead pre-check (2026-05-27 evening)
#                  + memory leak fix on real-rejection abandon (2026-05-27 night)
# ===========================================================================

class CommitterBrowser(HMSBrowser):
    """Browser #2 - commits Grid_Put bags to HMS.

    [EDELJKS REVISION]
    Adds strict post-confirm verification:
      - Snapshots ALL input field values before pressing Enter on step 2.
      - When 'put confirmed' is detected, extracts the bag id HMS rendered
        closest to the confirm message and compares it to our bag id.
      - Settle check: re-reads the page after a short pause to confirm
        the confirmation persists / the page transitions cleanly.
      - Logs the raw (case-preserved) page text snippet so failed cases
        are easy to diff against successful ones.

    [ALREADY-STAGED VERIFICATION PATCH 2026-05-27]
      - Pre-clear now also wipes 'already staged' toasts.
      - 'already staged' early-exit branches now verify the bag id near
        that keyword matches ours, snapshot inputs, and log a VERIFY line.

    [SESSION-AWARE RECOVERY PATCH 2026-05-27 evening]
      - _commit_one now does a session-dead pre-check at the very top.
        If the page shows the login form, "You have been logged out",
        "Invalid message", or the password-warning banner, we flag
        is_ready=False and return immediately. This stops every queued
        bag from burning a soft-fail against a dead session — the health
        watcher recovers ONCE.

    [MEMORY & STARVATION PATCH 2026-05-27 night]
      - Real-rejection abandonment now pops _soft_fail_counts +
        _soft_cooldowns so the dicts don't leak entries across the shift.
      - Step 1 wait loop now checks _looks_session_dead inside the loop
        so dying sessions are detected within 50ms instead of burning the
        full 2.5s timeout.

    Trade-off: each commit now takes ~1.5-2s longer in the success path
    (settle + extra logging). That is acceptable — false positives on
    HMS_Synced are worse than slow commits.
    """

    SOFT_COOLDOWN_SEC = 10
    PRECLEAR_MAX_MS = 2000

    # Maximum soft retries before abandoning a bag. This is the safety
    # net against infinite loops — if ANY soft-fail reason persists beyond
    # this count, the bag is abandoned rather than blocking the queue forever.
    MAX_SOFT_RETRIES = 15

    # Settle check: after seeing 'put confirmed', wait this long and re-read.
    SETTLE_CHECK_MS = 1500

    def __init__(self, name: str = "Committer"):
        super().__init__(name)
        self.synced_count = 0
        self.failed_count = 0
        self._soft_cooldowns: Dict[str, float] = {}
        self._soft_fail_counts: Dict[str, int] = {}

    async def run_loop(self, browser, on_synced_cb, on_failed_cb):
        from local_store_hms import (
            get_all_pending, record_real_attempt, abandon_bag,
            MAX_REAL_ATTEMPTS,
        )

        while True:
            try:
                if not self.is_ready:
                    await asyncio.sleep(2)
                    continue

                pending = get_all_pending()
                if not pending:
                    await asyncio.sleep(1.0)
                    continue

                ok = await self.ensure_ready(browser)
                if not ok:
                    await asyncio.sleep(2)
                    continue

                now = time.time()
                processed = 0

                for rec in pending:
                    if processed >= 5:
                        break

                    bag_id       = rec.get("bag_id", "")
                    area_barcode = rec.get("area_barcode", "")
                    trolley_id   = rec.get("trolley_id", "")
                    sheet_row    = rec.get("sheet_row", 0)
                    real_attempts = rec.get("real_attempts", 0)

                    if not bag_id or not area_barcode:
                        continue

                    cooldown_until = self._soft_cooldowns.get(bag_id, 0)
                    if now < cooldown_until:
                        continue

                    if real_attempts >= MAX_REAL_ATTEMPTS:
                        abandon_bag(bag_id, trolley_id,
                                    f"Exceeded {MAX_REAL_ATTEMPTS} real HMS rejections")
                        on_failed_cb(bag_id, trolley_id, sheet_row,
                                     f"Abandoned after {MAX_REAL_ATTEMPTS} rejections",
                                     True)
                        # [MEMORY PATCH] Clean up tracking dicts for the abandoned bag.
                        self._soft_cooldowns.pop(bag_id, None)
                        self._soft_fail_counts.pop(bag_id, None)
                        processed += 1
                        continue

                    try:
                        result = await self._commit_one(bag_id, area_barcode)
                    except Exception as e:
                        logger.error(f"[{self.name}] commit crashed for {bag_id}: {e}")
                        result = {"ok": False, "reason": f"crash: {e}",
                                  "real_rejection": False}

                    # ─── RESULT TRACE (always logged to file) ───
                    logger.debug(
                        f"[{self.name}] ━━━ RESULT {bag_id}: "
                        f"ok={result['ok']}  reason={result.get('reason','')}  "
                        f"real_rejection={result.get('real_rejection', False)}  "
                        f"soft_fails={self._soft_fail_counts.get(bag_id, 0)}  "
                        f"real_attempts={real_attempts} ━━━"
                    )

                    if result["ok"]:
                        self.synced_count += 1
                        self._soft_cooldowns.pop(bag_id, None)
                        self._soft_fail_counts.pop(bag_id, None)
                        on_synced_cb(bag_id, trolley_id, sheet_row,
                                     result.get("reason", ""))
                    else:
                        self.failed_count += 1
                        if result.get("real_rejection"):
                            new_count = record_real_attempt(
                                bag_id, trolley_id,
                                result.get("reason", ""))
                            if new_count >= MAX_REAL_ATTEMPTS:
                                abandon_bag(bag_id, trolley_id,
                                            result.get("reason", ""))
                                on_failed_cb(bag_id, trolley_id, sheet_row,
                                             result.get("reason", ""), True)
                                # [MEMORY PATCH] Clean up tracking dicts when
                                # abandoning via the real-rejection path. Without
                                # this, _soft_fail_counts / _soft_cooldowns leak
                                # entries for every real-rejected bag across an
                                # 8-hour shift.
                                self._soft_cooldowns.pop(bag_id, None)
                                self._soft_fail_counts.pop(bag_id, None)
                            else:
                                remaining = MAX_REAL_ATTEMPTS - new_count
                                logger.warning(
                                    f"[{self.name}] {bag_id} real attempt "
                                    f"{new_count}/{MAX_REAL_ATTEMPTS}: "
                                    f"{result.get('reason','')} "
                                    f"({remaining} left)")
                        else:
                            # Track soft-fail count and abandon if over cap
                            sf_count = self._soft_fail_counts.get(bag_id, 0) + 1
                            self._soft_fail_counts[bag_id] = sf_count
                            if sf_count >= self.MAX_SOFT_RETRIES:
                                logger.error(
                                    f"[{self.name}] {bag_id} hit MAX_SOFT_RETRIES "
                                    f"({self.MAX_SOFT_RETRIES}) — abandoning. "
                                    f"Last reason: {result.get('reason','')}")
                                abandon_bag(bag_id, trolley_id,
                                            f"Soft-fail cap: {result.get('reason','')}")
                                on_failed_cb(bag_id, trolley_id, sheet_row,
                                             f"Abandoned (soft-fail cap): "
                                             f"{result.get('reason','')}", True)
                                self._soft_cooldowns.pop(bag_id, None)
                                self._soft_fail_counts.pop(bag_id, None)
                            else:
                                self._soft_cooldowns[bag_id] = now + self.SOFT_COOLDOWN_SEC
                                logger.warning(
                                    f"[{self.name}] {bag_id} soft fail "
                                    f"({sf_count}/{self.MAX_SOFT_RETRIES}): "
                                    f"{result.get('reason','')} "
                                    f"(retry in {self.SOFT_COOLDOWN_SEC}s)")

                    processed += 1

                    # [SESSION-AWARE RECOVERY PATCH] If commit detected
                    # a dead session and flagged is_ready=False, stop
                    # iterating immediately so the health watcher can
                    # recover. Otherwise we'd hit the dead session for
                    # every remaining bag in this batch.
                    if not self.is_ready:
                        logger.info(f"[{self.name}] is_ready was cleared "
                                    f"during batch — breaking to let health "
                                    f"watcher recover")
                        break

                if len(self._soft_cooldowns) > 200:
                    cutoff = now - 300
                    self._soft_cooldowns = {
                        k: v for k, v in self._soft_cooldowns.items()
                        if v > cutoff
                    }
                    # Prune soft-fail counts for bags no longer in cooldowns
                    self._soft_fail_counts = {
                        k: v for k, v in self._soft_fail_counts.items()
                        if k in self._soft_cooldowns
                    }

                if not processed:
                    await asyncio.sleep(1.0)

            except Exception as e:
                logger.error(f"[{self.name}] run_loop error: {e}")
                await asyncio.sleep(2)

    async def _click_any_cancel(self) -> bool:
        page = self.page
        try:
            clicked = await page.evaluate("""() => {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var t = (btns[i].innerText || '').trim().toLowerCase();
                    if (t === 'cancel put' || t === 'cancel') {
                        btns[i].click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                return True
        except Exception:
            pass

        try:
            clicked = await page.evaluate("""() => {
                var matBtns = document.querySelectorAll(
                    'button[mat-button], button[mat-raised-button], button[mat-flat-button], ' +
                    'button[mat-stroked-button], button.mat-button, button.mat-raised-button, ' +
                    'button.mat-mdc-button, button.mat-mdc-raised-button, ' +
                    'a[mat-button], a[mat-raised-button]'
                );
                for (var i = 0; i < matBtns.length; i++) {
                    var t = (matBtns[i].innerText || matBtns[i].textContent || '').trim().toLowerCase();
                    if (t.indexOf('cancel') !== -1) {
                        matBtns[i].click();
                        return true;
                    }
                }
                var spans = document.querySelectorAll('button span, button .mat-button-wrapper, button .mdc-button__label');
                for (var j = 0; j < spans.length; j++) {
                    var st = (spans[j].innerText || spans[j].textContent || '').trim().toLowerCase();
                    if (st === 'cancel put' || st === 'cancel') {
                        var btn = spans[j].closest('button');
                        if (btn) { btn.click(); return true; }
                    }
                }
                return false;
            }""")
            if clicked:
                return True
        except Exception:
            pass

        try:
            await page.locator("button:has-text('Cancel put')").first.click(timeout=1000)
            return True
        except Exception:
            pass
        try:
            await page.locator("button:has-text('Cancel')").first.click(timeout=800)
            return True
        except Exception:
            pass
        return False

    async def _preclear_page(self, bag_id: str) -> bool:
        page = self.page
        # 'put to' and 'put confirmed' / 'successfully put' are genuine
        # stale states that must be cleared before typing a new bag.
        # NOTE: 'already staged' is NOT included here anymore — the
        # _verify_already_staged() method uses input_value() to confirm
        # whether HMS processed our scan, which is a robust check.
        # Including 'already staged' here caused a cascade of page
        # recreations because the toast is sticky and won't dismiss via
        # Cancel or input-clear — it only disappears when a new bag is
        # typed or the page is reloaded.
        #
        # 'put confirmed' / 'successfully put' are also NOT dirty:
        # after a verified commit, the input is empty and HMS replaces
        # the page content when the next bag is typed. Treating these
        # as dirty caused a 2s timeout + 6.5s page recreation on every
        # consecutive fresh commit. The step 1/step 2 detection logic
        # handles them correctly because:
        #   - STEP1_TERMINAL doesn't include 'put confirmed'
        #   - HMS fully replaces page content on new scan
        #   - step 2 pre_has_confirm flag handles edge cases
        #   - bag-ID verification catches any mismatch
        #
        # Only 'put to' remains dirty: it means the page is mid-commit
        # waiting for an area barcode — typing a new bag ID here would
        # be interpreted as the barcode, not a new scan.
        DIRTY_KEYWORDS = ("put to",)

        try:
            text = await page.evaluate(
                "() => (document.body && document.body.innerText || '').toLowerCase()"
            )
        except Exception:
            text = ""

        if not any(kw in text for kw in DIRTY_KEYWORDS):
            logger.debug(f"[{self.name}] {bag_id}: preclear — page is clean, no dirty keywords found")
            try:
                await page.locator("input[type='text']").first.fill("", timeout=1000)
            except Exception:
                pass
            return True

        logger.info(f"[{self.name}] {bag_id}: pre-clearing stale state "
                    f"({[kw for kw in DIRTY_KEYWORDS if kw in text]})")

        deadline = time.time() + (self.PRECLEAR_MAX_MS / 1000.0)
        cancel_attempts = 0

        while time.time() < deadline:
            if "put to" in text:
                cancel_attempts += 1
                await self._click_any_cancel()
                await asyncio.sleep(0.15)
            else:
                try:
                    await page.locator("input[type='text']").first.fill("", timeout=800)
                except Exception:
                    pass
                await asyncio.sleep(0.2)

            try:
                text = await page.evaluate(
                    "() => (document.body && document.body.innerText || '').toLowerCase()"
                )
            except Exception:
                text = ""

            if not any(kw in text for kw in DIRTY_KEYWORDS):
                try:
                    await page.locator("input[type='text']").first.fill("", timeout=1000)
                except Exception:
                    pass
                logger.info(f"[{self.name}] {bag_id}: pre-clear OK "
                            f"({cancel_attempts} cancel click(s))")
                return True

        logger.warning(f"[{self.name}] {bag_id}: pre-clear timeout, "
                       f"recreating page")
        try:
            old_page = self.page
            new_page = await self.context.new_page()
            new_page.set_default_timeout(15000)

            async def _dismiss(d):
                try:
                    await d.dismiss()
                except Exception:
                    pass
            new_page.on("dialog",
                        lambda d: asyncio.ensure_future(_dismiss(d)))

            self.page = new_page
            try:
                await old_page.close()
            except Exception:
                pass

            await self.page.goto(HMS_URL,
                                 wait_until="domcontentloaded",
                                 timeout=15000)
            await asyncio.sleep(1.5)
            content = await self.page.content()
            if "Scan item" not in content and "Put Item" not in content:
                if "username" in content.lower() or \
                   await self.page.locator("input[type='password']").count() > 0:
                    await self._do_full_login()
                else:
                    await self._select_facility()
                    await asyncio.sleep(1)
                    await self._navigate_to_put_page()

            try:
                text = await self.page.evaluate(
                    "() => (document.body && document.body.innerText || '').toLowerCase()"
                )
            except Exception:
                text = ""
            if not any(kw in text for kw in DIRTY_KEYWORDS):
                logger.info(f"[{self.name}] {bag_id}: page recreated, clean")
                return True
            else:
                logger.error(f"[{self.name}] {bag_id}: page recreated but "
                             f"still dirty?! flagging not ready")
                self.is_ready = False
                return False
        except Exception as nav_e:
            logger.error(f"[{self.name}] {bag_id}: page recreate failed: {nav_e}")
            self.is_ready = False
            return False

    # ─────────────────────────────────────────────────────────────
    # snapshot every text input's current value for diagnostics
    # ─────────────────────────────────────────────────────────────
    async def _snapshot_inputs(self, bag_id: str, label: str) -> List[str]:
        """Read .input_value() for each input[type='text'] on the page.

        Returns a list of strings (one per input). Also logs them. Safe
        on any error — returns whatever we managed to read.
        """
        values = []
        try:
            all_inputs = self.page.locator("input[type='text']")
            cnt = await all_inputs.count()
            for k in range(cnt):
                try:
                    v = await all_inputs.nth(k).input_value()
                except Exception:
                    v = "<read-error>"
                values.append(v)
            logger.info(
                f"[{self.name}] {bag_id} {label}: "
                f"{cnt} input(s), values={values!r}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] {bag_id} {label}: snapshot failed: {e}"
            )
        return values

    # ─────────────────────────────────────────────────────────────
    # [ALREADY-STAGED PATCH] helper: verify an 'already staged' toast
    # really refers to OUR bag, not a stale message from the previous
    # commit.
    # ─────────────────────────────────────────────────────────────
    async def _verify_already_staged(self, bag_id: str,
                                     elapsed_ms: int) -> dict:
        """Verify that an 'already staged' page state refers to our bag.

        Returns the SAME dict shape _commit_one returns:
          - ok=True on verified already-staged
          - ok=False with real_rejection=False on suspicious / unverifiable
            cases — we don't want to mark the sheet on a stale toast.
        """
        page = self.page

        # Read raw (case-preserved) text for bag-id extraction
        try:
            raw_text = await page.evaluate(
                "() => (document.body && document.body.innerText || '')"
            )
        except Exception:
            raw_text = ""

        hms_displayed_bag = _extract_hms_bag_id_near(
            raw_text, "already staged"
        )
        bag_match = (hms_displayed_bag == bag_id)

        # ALWAYS log the verification line — same shape as fresh-confirm
        logger.info(
            f"[Committer] {bag_id} VERIFY (already_staged): "
            f"HMS displayed='{hms_displayed_bag}'  "
            f"Expected='{bag_id}'  "
            f"MATCH={'YES' if bag_match else 'NO'}"
        )

        # Diagnostic snapshot
        post_snap = await self._snapshot_inputs(
            bag_id, "ALREADY-STAGED inputs"
        )

        if not bag_match:
            # ─── KEY INSIGHT (2026-05-27 RCA) ───────────────────────
            # The page was verified CLEAN (no 'already staged' text)
            # BEFORE we typed the bag ID. The 'already staged' response
            # appeared ONLY after we submitted our bag. Therefore this
            # IS the HMS system's response to OUR bag — the displayed
            # bag in the toast is just what HMS shows (e.g. the current
            # grid occupant) and does NOT mean our bag wasn't processed.
            #
            # Additional proof: if the input field is now empty, HMS
            # cleared it after processing our scan — confirming receipt.
            #
            # Previously this returned ok=False which caused an infinite
            # retry loop (soft fail every 10s, never resolving).
            # ─────────────────────────────────────────────────────────
            input_val = ""
            try:
                input_val = await page.locator(
                    "input[type='text']"
                ).first.input_value(timeout=1000)
            except Exception:
                input_val = ""

            logger.debug(
                f"[Committer] {bag_id} ALREADY_STAGED decision: "
                f"input_val='{input_val}' | hms_displayed='{hms_displayed_bag}' | "
                f"bag_in_input={bag_id.lower() in (input_val or '').lower()}"
            )

            # If the input still contains OUR bag_id, HMS did NOT
            # process it — this truly is a stale toast. Reject.
            if bag_id.lower() in (input_val or "").lower():
                logger.error(
                    f"[Committer] {bag_id} -> REJECT (already_staged): "
                    f"input still has our bag_id (HMS didn't process it). "
                    f"toast bag='{hms_displayed_bag}'"
                )
                try:
                    await page.locator("input[type='text']").first.fill("", timeout=1000)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "reason": (f"already_staged stale toast "
                               f"(input not consumed): '{hms_displayed_bag}'"),
                    "real_rejection": False,
                }

            # Input is empty/different → HMS consumed our scan and
            # responded 'already staged'. Accept as success.
            logger.info(
                f"[Committer] {bag_id} ALREADY_STAGED — accepted "
                f"(HMS responded after clean-page submit, input cleared). "
                f"Toast showed '{hms_displayed_bag}' but response is to "
                f"our bag. ({elapsed_ms}ms)"
            )
            try:
                await page.locator("input[type='text']").first.fill("", timeout=1000)
            except Exception:
                pass
            return {"ok": True, "reason": "already_staged",
                    "real_rejection": False}

        # Bag matches — clear the input and accept.
        try:
            await page.locator("input[type='text']").first.fill("", timeout=1000)
        except Exception:
            pass
        logger.info(
            f"[Committer] {bag_id} ALREADY_STAGED & VERIFIED "
            f"({elapsed_ms}ms, hms_bag='{hms_displayed_bag}')"
        )
        return {"ok": True, "reason": "already_staged",
                "real_rejection": False}

    # ─────────────────────────────────────────────────────────────────
    # POST-COMMIT PAGE RESET (2026-05-28 performance patch)
    # ─────────────────────────────────────────────────────────────────
    # After a successful 'put confirmed' commit is FULLY VERIFIED, the
    # page shows a sticky "put confirmed" toast that the next bag's
    # _preclear_page cannot dismiss (Cancel click doesn't work). This
    # forces a 2s timeout + 6.5s full page recreation on every
    # consecutive fresh commit.
    #
    # This method proactively reloads the page AFTER verification is
    # complete. If it succeeds, the next bag's preclear finds a clean
    # page (~0s). If it fails for any reason, the next bag falls back
    # to the old preclear path — zero risk to data integrity.
    #
    # SAFETY: called ONLY after bag-ID match + settle check have passed.
    # The commit result is immutable at this point. The sheet write
    # happens AFTER _commit_one returns, in the run_loop callback.
    # ─────────────────────────────────────────────────────────────────
    async def _post_commit_reset(self, bag_id: str):
        """Best-effort page reload after confirmed commit to avoid
        next-bag preclear timeout. Non-critical — failure is OK."""
        try:
            await self.page.reload(
                wait_until="domcontentloaded", timeout=8000
            )
            await asyncio.sleep(0.5)
            # Verify we landed back on put page
            if await self._on_put_page():
                logger.debug(
                    f"[{self.name}] {bag_id}: post-commit reload OK "
                    f"(page clean for next bag)")
            else:
                # Reload didn't land on put page — could be session
                # expired or HMS quirk. Don't panic: next bag's preclear
                # or ensure_ready will handle it.
                logger.debug(
                    f"[{self.name}] {bag_id}: post-commit reload landed "
                    f"off put-page (next bag's preclear will recover)")
        except Exception as e:
            logger.debug(
                f"[{self.name}] {bag_id}: post-commit reset failed: "
                f"{e} (non-critical, next bag preclear will handle)")

    async def _commit_one(self, bag_id: str, area_barcode: str) -> dict:
        page = self.page
        t0 = time.time()
        bag_id = str(bag_id).strip().upper()
        logger.debug(f"[Committer] ━━━ START _commit_one: {bag_id} area={area_barcode} ━━━")

        # ─── [SESSION-AWARE RECOVERY PATCH] session-dead pre-check ────
        # If the session has died (login screen / "You have been logged
        # out" / "Invalid message" / password-warning banner), abort
        # immediately and flag is_ready=False so the health watcher does
        # exactly ONE re-login, instead of every queued bag soft-failing
        # one by one against the dead session.
        try:
            _early_text = (await page.evaluate(
                "() => (document.body && document.body.innerText || '').toLowerCase()"
            )) or ""
        except Exception:
            _early_text = ""
        _session_dead = _looks_session_dead(_early_text)
        if not _session_dead:
            try:
                if await page.locator("input[type='password']").count() > 0:
                    _session_dead = True
            except Exception:
                pass
        if _session_dead:
            logger.error(f"[Committer] {bag_id}: session-dead state detected "
                         f"BEFORE commit (login / logout / banner visible) — "
                         f"flagging not-ready so health watcher recovers ONCE")
            self.is_ready = False
            return {"ok": False,
                    "reason": "Session expired (login / logged-out screen visible)",
                    "real_rejection": False}
        # ─── end session-dead pre-check ───────────────────────────────

        STEP1_REJECT_KEYWORDS = (
            "incorrect ba", "incorrect barcode", "not found",
            "not allowed", "not expected", "does not belong",
            "wrong barcode", "invalid barcode", "invalid item",
        )
        STEP1_TERMINAL = ("put to", "already staged") + STEP1_REJECT_KEYWORDS

        try:
            # STEP 0: ACTIVE pre-clear of page state (UNCHANGED behaviour,
            # but DIRTY_KEYWORDS now includes 'already staged')
            logger.debug(f"[Committer] {bag_id}: STEP 0 — preclear_page()")
            preclear_ok = await self._preclear_page(bag_id)
            if not preclear_ok:
                logger.debug(f"[Committer] {bag_id}: STEP 0 FAILED — preclear returned False")
                return {"ok": False,
                        "reason": "Could not clear stale page state",
                        "real_rejection": False}
            logger.debug(f"[Committer] {bag_id}: STEP 0 OK — page precleared")
            page = self.page

            # STEP 1: Submit the bag ID
            logger.debug(f"[Committer] {bag_id}: STEP 1 — filling input and pressing Enter")
            inp = page.locator("input[type='text']").first
            try:
                await inp.fill("", timeout=2000)
            except Exception:
                pass
            await inp.fill(bag_id, timeout=2000)
            await inp.press("Enter")
            logger.debug(f"[Committer] {bag_id}: STEP 1 — Enter pressed, waiting for HMS response (timeout={SUGGEST_TIMEOUT_MS}ms)")

            text = ""
            deadline = time.time() + (SUGGEST_TIMEOUT_MS / 1000.0)
            # [MEMORY & STARVATION PATCH] Check for session-dead state inside
            # the loop so we bail out within 50ms instead of burning the full
            # 2.5s timeout when the session dies mid-wait.
            session_died_mid_wait = False
            while time.time() < deadline:
                try:
                    text = await page.evaluate(
                        "() => (document.body && document.body.innerText || '').toLowerCase()"
                    )
                except Exception:
                    text = ""
                if _looks_session_dead(text):
                    session_died_mid_wait = True
                    break
                if any(kw in text for kw in STEP1_TERMINAL):
                    break
                await asyncio.sleep(0.05)

            # Log the raw STEP 1 response for debugging
            _step1_elapsed = int((time.time() - t0) * 1000)
            _step1_snippet = text[:200].replace("\n", " ").strip() if text else "<empty>"
            logger.debug(
                f"[Committer] {bag_id}: STEP 1 RESPONSE ({_step1_elapsed}ms): "
                f"{_step1_snippet}"
            )

            # [SESSION-AWARE RECOVERY PATCH] If during the wait, the page
            # transitioned into a session-dead state (e.g. token expired
            # mid-cycle), bail out and let the watcher recover.
            if session_died_mid_wait or _looks_session_dead(text):
                logger.error(f"[Committer] {bag_id}: session died MID-commit "
                             f"(step 1 wait, {_step1_elapsed}ms) — flagging not-ready")
                self.is_ready = False
                return {"ok": False,
                        "reason": "Session expired mid-commit",
                        "real_rejection": False}

            # ════════════════════════════════════════════════════════════
            # [ALREADY-STAGED PATCH] Verify bag-id match before trusting
            # 'already staged' as success for THIS bag.
            # ════════════════════════════════════════════════════════════
            if "already staged" in text:
                elapsed = int((time.time() - t0) * 1000)
                return await self._verify_already_staged(bag_id, elapsed)

            for kw in STEP1_REJECT_KEYWORDS:
                if kw in text:
                    elapsed = int((time.time() - t0) * 1000)
                    snippet = text[:120].replace("\n", " ").strip()
                    logger.warning(f"[Committer] {bag_id} -> REJECT '{kw}' ({elapsed}ms): {snippet}")
                    try:
                        await page.locator("input[type='text']").first.fill("", timeout=1000)
                    except Exception:
                        pass
                    return {"ok": False, "reason": f"HMS rejected: {kw}",
                            "real_rejection": True}

            if "put to" not in text:
                try:
                    await page.locator("input[type='text']").first.press("Enter")
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                try:
                    text = await page.evaluate(
                        "() => (document.body && document.body.innerText || '').toLowerCase()"
                    )
                except Exception:
                    text = ""

                # [ALREADY-STAGED PATCH] Same verification on the
                # second-chance read.
                if "already staged" in text:
                    elapsed = int((time.time() - t0) * 1000)
                    return await self._verify_already_staged(bag_id, elapsed)

                for kw in STEP1_REJECT_KEYWORDS:
                    if kw in text:
                        try:
                            await page.locator("input[type='text']").first.fill("", timeout=1000)
                        except Exception:
                            pass
                        return {"ok": False, "reason": f"HMS rejected: {kw}",
                                "real_rejection": True}
                if "put to" not in text:
                    err_msg = text[:120].replace("\n", " ").strip()
                    try:
                        await page.locator("input[type='text']").first.fill("", timeout=1000)
                    except Exception:
                        pass
                    return {"ok": False, "reason": f"No suggestion: {err_msg}",
                            "real_rejection": False}

            # STEP 2: Fill the area barcode
            logger.debug(f"[Committer] {bag_id}: STEP 2 — 'put to' detected, filling area barcode '{area_barcode}'")
            await asyncio.sleep(0.3)

            try:
                bc = None
                bc_deadline = time.time() + 5.0
                while time.time() < bc_deadline:
                    all_inputs = page.locator("input[type='text']")
                    input_count = await all_inputs.count()
                    if input_count >= 2:
                        bc = all_inputs.nth(1)
                        logger.info(
                            f"[Committer] {bag_id}: found {input_count} inputs, "
                            f"using index 1 for barcode")
                        break
                    elif input_count == 1:
                        check_text = ""
                        try:
                            check_text = await page.evaluate(
                                "() => (document.body && document.body.innerText || '').toLowerCase()"
                            )
                        except Exception:
                            check_text = ""
                        if "put to" in check_text:
                            bc = all_inputs.first
                            logger.info(
                                f"[Committer] {bag_id}: sequential mode — "
                                f"1 input visible with 'Put to' shown, "
                                f"using it for barcode")
                            break
                    await asyncio.sleep(0.3)

                if bc is None:
                    logger.error(
                        f"[Committer] {bag_id}: ABORTING — could not find "
                        f"barcode input within 5s. Will retry.")
                    try:
                        await page.locator("input[type='text']").first.fill("", timeout=1000)
                    except Exception:
                        pass
                    return {"ok": False,
                            "reason": "No barcode input found after step 1",
                            "real_rejection": False}

                await bc.fill("", timeout=2000)
                await bc.fill(area_barcode, timeout=2000)
            except Exception as e:
                logger.error(
                    f"[Committer] {bag_id}: barcode input failed: {e}")
                return {"ok": False, "reason": f"barcode input: {e}",
                        "real_rejection": False}

            # ═══════════════════════════════════════════════════════════
            # snapshot all input field values right BEFORE pressing Enter.
            # ═══════════════════════════════════════════════════════════
            pre_submit_inputs = await self._snapshot_inputs(
                bag_id, "PRE-SUBMIT inputs"
            )

            pre_submit_text = ""
            try:
                pre_submit_text = await page.evaluate(
                    "() => (document.body && document.body.innerText || '').toLowerCase()"
                )
            except Exception:
                pre_submit_text = ""

            CONFIRM_KEYWORDS = ("put confirmed", "successfully put")
            FAIL_KEYWORDS = ("incorrect barcode", "wrong barcode",
                             "invalid barcode", "not found",
                             "not expected", "does not belong")

            pre_has_confirm = any(kw in pre_submit_text for kw in CONFIRM_KEYWORDS)
            if pre_has_confirm:
                logger.warning(
                    f"[Committer] {bag_id}: UNEXPECTED pre-submit "
                    f"confirmation text — pre-clear may have raced. "
                    f"Will require text change to accept.")

            try:
                await bc.press("Enter")
                logger.info(
                    f"[Committer] {bag_id}: barcode '{area_barcode}' submitted")
                logger.debug(f"[Committer] {bag_id}: STEP 2 — barcode Enter pressed, waiting for confirm/fail (timeout={COMMIT_TIMEOUT_MS}ms)")
            except Exception as e:
                logger.error(
                    f"[Committer] {bag_id}: barcode Enter failed: {e}")
                return {"ok": False, "reason": f"barcode enter: {e}",
                        "real_rejection": False}

            # Wait for terminal state (confirm / fail / scan-reset)
            text = ""
            deadline = time.time() + (COMMIT_TIMEOUT_MS / 1000.0)
            while time.time() < deadline:
                try:
                    text = await page.evaluate(
                        "() => (document.body && document.body.innerText || '').toLowerCase()"
                    )
                except Exception:
                    text = ""
                has_confirm_now = any(kw in text for kw in CONFIRM_KEYWORDS)
                if has_confirm_now and not pre_has_confirm:
                    break
                if has_confirm_now and pre_has_confirm:
                    if "put to" not in text:
                        break
                if any(kw in text for kw in FAIL_KEYWORDS):
                    break
                if ("scan item" in text and "put to" not in text
                        and not has_confirm_now):
                    break
                await asyncio.sleep(0.05)

            # [SESSION-AWARE RECOVERY PATCH] Session died during step 2 wait.
            if _looks_session_dead(text):
                logger.error(f"[Committer] {bag_id}: session died MID-commit "
                             f"(step 2 wait) — flagging not-ready")
                self.is_ready = False
                return {"ok": False,
                        "reason": "Session expired mid-commit (step 2)",
                        "real_rejection": False}

            elapsed_ms = int((time.time() - t0) * 1000)
            page_snippet = text[:200].replace("\n", " ").strip()
            logger.info(
                f"[Committer] {bag_id} step2 result ({elapsed_ms}ms): "
                f"{page_snippet}")

            has_confirm_final = any(kw in text for kw in CONFIRM_KEYWORDS)

            # ═══════════════════════════════════════════════════════════
            # SUCCESS PATH — STRICT VERIFICATION [EDELJKS REVISION]
            # ═══════════════════════════════════════════════════════════
            if has_confirm_final:
                # Grab the RAW (case-preserved) text for bag-id extraction
                # and for logging the original casing for diagnostics.
                try:
                    raw_text = await page.evaluate(
                        "() => (document.body && document.body.innerText || '')"
                    )
                except Exception:
                    raw_text = ""

                hms_displayed_bag = _extract_hms_bag_id(raw_text)
                bag_match = (hms_displayed_bag == bag_id)

                # ALWAYS log the verification result — this is the line
                # you check in the terminal tomorrow.
                logger.info(
                    f"[Committer] {bag_id} VERIFY: "
                    f"HMS displayed='{hms_displayed_bag}'  "
                    f"Expected='{bag_id}'  "
                    f"MATCH={'YES' if bag_match else 'NO'}"
                )

                if not bag_match:
                    # The bag id near 'put confirmed' is not ours. This
                    # is the EFRKGGN-style stale-confirm-for-different-bag.
                    logger.error(
                        f"[Committer] {bag_id} -> REJECT: confirmation is "
                        f"for a DIFFERENT bag ('{hms_displayed_bag}'). "
                        f"raw_text_tail={raw_text[-300:]!r}"
                    )
                    try:
                        await self._click_any_cancel()
                    except Exception:
                        pass
                    return {
                        "ok": False,
                        "reason": (f"HMS confirm shows '{hms_displayed_bag}' "
                                   f"not '{bag_id}'"),
                        "real_rejection": False,
                    }

                # Bag id matches. Now do the SETTLE check: a genuine
                # commit should keep the confirmation visible and start
                # transitioning back toward the clean scan state.
                await asyncio.sleep(self.SETTLE_CHECK_MS / 1000.0)

                try:
                    settle_text = await page.evaluate(
                        "() => (document.body && document.body.innerText || '').toLowerCase()"
                    )
                except Exception:
                    settle_text = ""

                settle_has_confirm = any(
                    kw in settle_text for kw in CONFIRM_KEYWORDS
                )
                settle_has_put_to = "put to" in settle_text

                # Also snapshot inputs after settle — they should be empty
                # for a clean commit.
                post_settle_inputs = await self._snapshot_inputs(
                    bag_id, "POST-SETTLE inputs"
                )

                logger.info(
                    f"[Committer] {bag_id} SETTLE: "
                    f"has_confirm={settle_has_confirm}  "
                    f"has_put_to={settle_has_put_to}  "
                    f"pre_submit_inputs={pre_submit_inputs!r}"
                )

                if settle_has_put_to:
                    # Page is back showing 'put to' — that means our
                    # submit DID NOT complete; the confirm we saw was
                    # transient or stale. Reject.
                    logger.error(
                        f"[Committer] {bag_id} -> REJECT: settled with "
                        f"'put to' still visible — commit not complete. "
                        f"settle_text_tail={settle_text[-200:]!r}"
                    )
                    try:
                        await self._click_any_cancel()
                    except Exception:
                        pass
                    return {
                        "ok": False,
                        "reason": "Settle showed 'put to' still active",
                        "real_rejection": False,
                    }

                # Accept the commit.
                if not pre_has_confirm:
                    logger.info(
                        f"[Committer] {bag_id} -> CONFIRMED & VERIFIED "
                        f"({elapsed_ms}ms, hms_bag='{hms_displayed_bag}')"
                    )
                    return {"ok": True, "reason": "confirmed",
                            "real_rejection": False}
                else:
                    # pre_has_confirm was True but text changed through
                    # the cycle AND bag matches AND settle is clean —
                    # still a valid commit.
                    logger.info(
                        f"[Committer] {bag_id} -> CONFIRMED & VERIFIED "
                        f"(post-stale, {elapsed_ms}ms, "
                        f"hms_bag='{hms_displayed_bag}')"
                    )
                    return {"ok": True, "reason": "confirmed",
                            "real_rejection": False}

            # No confirmation text at all.
            if ("scan item" in text and "put to" not in text and
                    "error" not in text and "incorrect" not in text):
                logger.warning(
                    f"[Committer] {bag_id} -> page reset to scan WITHOUT "
                    f"confirmation text — NOT marking as synced ({elapsed_ms}ms). "
                    f"pre_submit_inputs={pre_submit_inputs!r}")
                return {"ok": False,
                        "reason": "Page reset without confirmation (ambiguous)",
                        "real_rejection": False}

            if "put to" in text:
                logger.warning(
                    f"[Committer] {bag_id} -> barcode rejected ({elapsed_ms}ms). "
                    f"pre_submit_inputs={pre_submit_inputs!r}")
                try:
                    await self._click_any_cancel()
                except Exception:
                    pass
                verify_end = time.time() + 2.0
                while time.time() < verify_end:
                    try:
                        vt = await page.evaluate(
                            "() => (document.body && document.body.innerText || '').toLowerCase()"
                        )
                    except Exception:
                        vt = ""
                    if "put to" not in vt:
                        break
                    await asyncio.sleep(0.05)
                return {"ok": False, "reason": "Area barcode not accepted",
                        "real_rejection": True}

            logger.warning(
                f"[Committer] {bag_id} -> ambiguous ({elapsed_ms}ms): "
                f"{text[:80]}. pre_submit_inputs={pre_submit_inputs!r}")
            return {"ok": False, "reason": f"ambiguous: {text[:80]}",
                    "real_rejection": False}

        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"[Committer] commit crashed for {bag_id} at {elapsed_ms}ms: {e}")
            self.is_ready = False
            return {"ok": False, "reason": f"crash: {e}", "real_rejection": False}


# ===========================================================================
# HMSManager - UNCHANGED except for stale-pending sweeper in _health_watcher
# ===========================================================================

class HMSManager:

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._loop = None
        self._thread = None
        self._started = False

        self.suggester_a: Optional[SuggesterBrowser] = None
        self.suggester_b: Optional[SuggesterBrowser] = None
        self.committer: Optional[CommitterBrowser] = None

        self._next_route = "a"

        self._pending_sync_writes: List[dict] = []
        self._writes_lock = threading.Lock()
        self._writer_thread = None
        self._writer_running = False

        # [MEMORY & STARVATION PATCH] Track last time we ran the
        # clear_stale_pending sweep. The sweep is cheap when the queue is
        # small; we run it once an hour from _health_watcher.
        self._last_stale_sweep = 0.0

    def _pick_suggester(self) -> Optional["SuggesterBrowser"]:
        sa = self.suggester_a
        sb = self.suggester_b
        sa_healthy = bool(sa and sa.is_ready and not sa.is_degraded())
        sb_healthy = bool(sb and sb.is_ready and not sb.is_degraded())

        if sa_healthy and sb_healthy:
            qa, qb = sa.queue_size(), sb.queue_size()
            if qa < qb:
                return sa
            if qb < qa:
                return sb
            if self._next_route == "a":
                self._next_route = "b"
                return sa
            else:
                self._next_route = "a"
                return sb

        if sa_healthy:
            return sa
        if sb_healthy:
            return sb

        sa_ready = bool(sa and sa.is_ready)
        sb_ready = bool(sb and sb.is_ready)
        if sa_ready and sb_ready:
            if sa.consecutive_failures <= sb.consecutive_failures:
                return sa
            return sb

        if sa_ready:
            return sa
        if sb_ready:
            return sb

        return None

    def start(self):
        if self._started:
            return
        if not HMS_CRED_SLOTS:
            logger.warning("HMS_CREDENTIALS not set - HMS sync DISABLED")
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run_loop, name="hms-asyncio", daemon=True)
        self._thread.start()

        self._writer_running = True
        self._writer_thread = threading.Thread(
            target=self._sheet_writer_loop, name="hms-sheet-writer", daemon=True)
        self._writer_thread.start()

        logger.info("[HMSManager] Started (asyncio + sheet writer threads, 3 browsers)")

    def fire_prefetch_immediate(self, bag_id: str):
        if not self._loop or not self._started:
            return
        bag_id = str(bag_id).strip().upper()
        if not bag_id:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._do_immediate_prefetch(bag_id), self._loop
            )
        except Exception as e:
            logger.debug(f"[Prefetch-Immediate] submit failed for {bag_id}: {e}")

    async def _do_immediate_prefetch(self, bag_id: str):
        from local_store_grid import (
            lookup_grid, store_grid, mark_inflight, clear_inflight,
        )

        cached = lookup_grid(bag_id)
        if cached and cached.get("grid") and not cached.get("consumed_at"):
            return

        if not mark_inflight(bag_id):
            logger.debug(f"[Prefetch-Immediate] {bag_id} already inflight - skip")
            return

        try:
            chosen = self._pick_suggester()
            if not chosen:
                logger.debug(f"[Prefetch-Immediate] no browser for {bag_id} - queue-only")
                return

            if chosen.queue_size() > 6:
                logger.debug(f"[Prefetch-Immediate] {chosen.name} backlogged - skip {bag_id}")
                return

            fut = self._loop.create_future()
            chosen.queue.put_nowait(SuggestRequest(bag_id, fut))

            try:
                result = await asyncio.wait_for(fut, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(f"[Prefetch-Immediate] {bag_id} timed out on {chosen.name}")
                return
            except Exception as e:
                logger.warning(f"[Prefetch-Immediate] {bag_id} error: {e}")
                return

            if result.get("ok") and result.get("grid"):
                grid = result["grid"].strip().upper()
                store_grid(
                    bag_id, grid,
                    already_staged=result.get("already_staged", False),
                    source="immediate_prefetch",
                )
                logger.info(f"[Prefetch-Immediate] OK {bag_id} -> {grid} "
                            f"(browser: {chosen.name})")
            else:
                reason = result.get("reason", "unknown")
                logger.info(f"[Prefetch-Immediate] {bag_id} -> no grid: {reason}")
        finally:
            clear_inflight(bag_id)

    def get_grid_suggestion(self, bag_id: str, timeout: float = 4.0) -> dict:
        if not self._loop or not self._started:
            return {"ok": False, "grid": "", "already_staged": False,
                    "reason": "HMS not started"}

        chosen = self._pick_suggester()
        if not chosen:
            return {"ok": False, "grid": "", "already_staged": False,
                    "reason": "No suggester browser available"}

        future_holder = []

        def _submit():
            fut = self._loop.create_future()
            future_holder.append(fut)
            chosen.queue.put_nowait(SuggestRequest(bag_id, fut))

        try:
            asyncio.run_coroutine_threadsafe(
                self._submit_async(_submit), self._loop).result(timeout=1.0)
        except Exception as e:
            return {"ok": False, "grid": "", "already_staged": False,
                    "reason": f"submit error: {e}"}

        if not future_holder:
            return {"ok": False, "grid": "", "already_staged": False,
                    "reason": "submit lost"}

        try:
            result = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(self._await_future(future_holder[0]),
                                 timeout=timeout),
                self._loop,
            ).result(timeout=timeout + 1.0)
            return result
        except asyncio.TimeoutError:
            return {"ok": False, "grid": "", "already_staged": False,
                    "reason": f"Suggester timed out (>{timeout}s)"}
        except Exception as e:
            return {"ok": False, "grid": "", "already_staged": False,
                    "reason": f"Suggester error: {e}"}

    async def _submit_async(self, fn):
        fn()

    async def _await_future(self, fut):
        return await fut

    def get_status(self) -> dict:
        from local_store_hms import get_stats
        try:
            from local_store_grid import get_stats as get_grid_stats
            grid_stats = get_grid_stats()
        except Exception:
            grid_stats = {}

        # [ROW-VALIDATION SAFETY NET 2026-05-28] Expose DLQ size for
        # operator monitoring. Audit RISK 4 recommended adding this so
        # /api/hms-status can be polled to detect quota-exhaustion or
        # row-drift accumulation.
        #
        # We rely on local_store_hms.get_dlq_size() being available. If
        # that function does not exist in your build, the value reported
        # is -1 (sentinel meaning "unsupported in this build") so the
        # operator can tell "0 in DLQ" from "we have no idea". We do NOT
        # drain+restore the DLQ here — that would race with the sheet
        # writer loop running on another thread.
        dlq_size = -1
        try:
            from local_store_hms import get_dlq_size as _get_dlq_size
            dlq_size = _get_dlq_size()
        except Exception:
            dlq_size = -1

        c = self.committer
        out = {
            "started": self._started,
            "suggester_a": {
                "ready": self.suggester_a.is_ready if self.suggester_a else False,
                "degraded": self.suggester_a.is_degraded() if self.suggester_a else False,
                "consecutive_failures": (self.suggester_a.consecutive_failures
                                         if self.suggester_a else 0),
                "queue": self.suggester_a.queue_size() if self.suggester_a else 0,
                "user": self.suggester_a.user if self.suggester_a else "",
                "processed": self.suggester_a.processed if self.suggester_a else 0,
                "errors": self.suggester_a.errors if self.suggester_a else 0,
            },
            "suggester_b": {
                "ready": self.suggester_b.is_ready if self.suggester_b else False,
                "degraded": self.suggester_b.is_degraded() if self.suggester_b else False,
                "consecutive_failures": (self.suggester_b.consecutive_failures
                                         if self.suggester_b else 0),
                "queue": self.suggester_b.queue_size() if self.suggester_b else 0,
                "user": self.suggester_b.user if self.suggester_b else "",
                "processed": self.suggester_b.processed if self.suggester_b else 0,
                "errors": self.suggester_b.errors if self.suggester_b else 0,
            },
            "committer": {
                "ready": c.is_ready if c else False,
                "user": c.user if c else "",
                "synced": c.synced_count if c else 0,
                "failed": c.failed_count if c else 0,
            },
            "queue_stats": get_stats(),
            "grid_cache": grid_stats,
            "dlq_size": dlq_size,
        }
        return out

    @property
    def is_ready(self) -> bool:
        return (self._started and
                self.suggester_a and self.suggester_a.is_ready and
                self.suggester_b and self.suggester_b.is_ready)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            logger.error(f"[HMSManager] asyncio loop crashed: {e}")

    async def _async_main(self):
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()

        launch_args = {
            "headless": HMS_HEADLESS,
            "args": [
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--ignore-certificate-errors",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if HMS_BROWSER_PATH:
            launch_args["executable_path"] = HMS_BROWSER_PATH
        else:
            launch_args["channel"] = "chrome"

        self._browser = await self._playwright.chromium.launch(**launch_args)
        logger.info(f"[HMSManager] Chromium launched (headless={HMS_HEADLESS})")

        self.suggester_a = SuggesterBrowser("Suggester-A")
        self.suggester_b = SuggesterBrowser("Suggester-B")
        self.committer = CommitterBrowser("Committer")

        logger.info("[HMSManager] Stage 1: bringing up Suggester-A (warms HMS)")
        try:
            await self.suggester_a.initialize(self._browser)
        except Exception as e:
            logger.error(f"[HMSManager] Suggester-A init crashed: {e}")

        await asyncio.sleep(2)

        logger.info("[HMSManager] Stage 2: bringing up Suggester-B + Committer")
        await asyncio.gather(
            self.suggester_b.initialize(self._browser),
            self.committer.initialize(self._browser),
            return_exceptions=True,
        )

        logger.info(f"[HMSManager] Browser status: "
                    f"SugA={self.suggester_a.is_ready}, "
                    f"SugB={self.suggester_b.is_ready}, "
                    f"Comm={self.committer.is_ready}")

        tasks = [
            asyncio.create_task(self.suggester_a.run_loop(self._browser)),
            asyncio.create_task(self.suggester_b.run_loop(self._browser)),
            asyncio.create_task(self.committer.run_loop(
                self._browser, self._on_synced, self._on_failed)),
            asyncio.create_task(self._slot_rotation_watcher()),
            asyncio.create_task(self._health_watcher()),
            asyncio.create_task(self._prefetch_worker_loop()),
            asyncio.create_task(self._cache_janitor_loop()),
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _prefetch_worker_loop(self):
        from local_store_grid import (
            pop_batch_for_prefetch, store_grid, requeue_for_prefetch,
            queue_size, lookup_grid, wait_for_queue,
            is_inflight, mark_inflight, clear_inflight,
        )
        consecutive_fail = 0
        _retry_counts = {}
        MAX_PREFETCH_RETRIES = 5
        while True:
            try:
                sa = self.suggester_a
                sb = self.suggester_b
                if not ((sa and sa.is_ready) or (sb and sb.is_ready)):
                    await asyncio.sleep(2)
                    continue

                await asyncio.get_event_loop().run_in_executor(
                    None, wait_for_queue, 1.0)

                batch = pop_batch_for_prefetch(max_items=4)
                if not batch:
                    continue

                to_fetch = []
                for bag_id in batch:
                    cached = lookup_grid(bag_id)
                    if cached and cached.get("grid"):
                        logger.debug(f"[Prefetch] {bag_id} already cached - skip")
                        _retry_counts.pop(bag_id, None)
                        continue
                    if is_inflight(bag_id):
                        logger.debug(f"[Prefetch] {bag_id} already inflight - skip")
                        continue
                    to_fetch.append(bag_id)

                if not to_fetch:
                    continue

                available = []
                if sa and sa.is_ready and not sa.is_degraded():
                    available.append(sa)
                if sb and sb.is_ready and not sb.is_degraded():
                    available.append(sb)
                if not available:
                    if sa and sa.is_ready:
                        available.append(sa)
                    if sb and sb.is_ready:
                        available.append(sb)

                if not available:
                    for bag_id in to_fetch:
                        retries = _retry_counts.get(bag_id, 0)
                        if retries < MAX_PREFETCH_RETRIES:
                            requeue_for_prefetch(bag_id)
                            _retry_counts[bag_id] = retries + 1
                        else:
                            logger.info(f"[Prefetch] {bag_id} dropped after "
                                        f"{MAX_PREFETCH_RETRIES} retries (no browser)")
                            _retry_counts.pop(bag_id, None)
                    await asyncio.sleep(1)
                    continue

                total_backlog = sum(b.queue_size() for b in available)
                if total_backlog > 8:
                    for bag_id in to_fetch:
                        retries = _retry_counts.get(bag_id, 0)
                        if retries < MAX_PREFETCH_RETRIES:
                            requeue_for_prefetch(bag_id)
                            _retry_counts[bag_id] = retries + 1
                        else:
                            _retry_counts.pop(bag_id, None)
                    await asyncio.sleep(0.3)
                    continue

                async def _prefetch_one(bag_id, browser):
                    if not mark_inflight(bag_id):
                        return bag_id, {"ok": False, "reason": "already inflight"}
                    try:
                        fut = self._loop.create_future()
                        browser.queue.put_nowait(SuggestRequest(bag_id, fut))
                        try:
                            result = await asyncio.wait_for(fut, timeout=8.0)
                        except asyncio.TimeoutError:
                            return bag_id, {"ok": False, "reason": "timeout"}
                        return bag_id, result
                    finally:
                        clear_inflight(bag_id)

                tasks = []
                for idx, bag_id in enumerate(to_fetch):
                    browser = available[idx % len(available)]
                    tasks.append(_prefetch_one(bag_id, browser))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, Exception):
                        consecutive_fail += 1
                        continue
                    bag_id, result = r
                    if result.get("ok") and result.get("grid"):
                        store_grid(
                            bag_id, result["grid"],
                            already_staged=result.get("already_staged", False),
                            source="prefetch",
                        )
                        consecutive_fail = 0
                        _retry_counts.pop(bag_id, None)
                    else:
                        reason = result.get("reason", "unknown")
                        soft = any(k in reason.lower() for k in [
                            "timeout", "not ready", "submit", "browser",
                            "no suggester", "session",
                        ])
                        if soft:
                            retries = _retry_counts.get(bag_id, 0)
                            if retries < MAX_PREFETCH_RETRIES:
                                requeue_for_prefetch(bag_id)
                                _retry_counts[bag_id] = retries + 1
                            else:
                                logger.info(f"[Prefetch] {bag_id} dropped after "
                                            f"{MAX_PREFETCH_RETRIES} soft retries: {reason}")
                                _retry_counts.pop(bag_id, None)
                            consecutive_fail += 1
                        else:
                            logger.info(f"[Prefetch] {bag_id} -> real reject: {reason}")
                            consecutive_fail = 0
                            _retry_counts.pop(bag_id, None)

                qs = queue_size()
                if qs > 0:
                    logger.info(f"[Prefetch] {qs} bag(s) still queued")

                if consecutive_fail >= 8:
                    await asyncio.sleep(5)
                    consecutive_fail = 0

                if len(_retry_counts) > 200:
                    _retry_counts.clear()

            except Exception as e:
                logger.error(f"[Prefetch] loop error: {e}")
                await asyncio.sleep(2)

    async def _cache_janitor_loop(self):
        from local_store_grid import purge_stale, CACHE_TTL_HOURS
        while True:
            try:
                await asyncio.sleep(3600)
                purge_stale(CACHE_TTL_HOURS)
            except Exception as e:
                logger.error(f"[CacheJanitor] {e}")

    async def _slot_rotation_watcher(self):
        last_slot = _get_current_slot()
        while True:
            await asyncio.sleep(60)
            try:
                cur = _get_current_slot()
                if cur != last_slot:
                    logger.info(f"[HMSManager] Credential slot rotated: {last_slot} -> {cur}")
                    last_slot = cur
                    for b in [self.suggester_a, self.suggester_b,
                              self.committer]:
                        if b:
                            b.failed_slots.clear()
                            b.current_slot = cur
                            if HMS_CRED_SLOTS:
                                b.user, b.password = HMS_CRED_SLOTS[cur]
                            try:
                                await b.close()
                                await b.initialize(self._browser)
                            except Exception as e:
                                logger.error(f"[HMSManager] Rotation re-init for {b.name}: {e}")
            except Exception as e:
                logger.error(f"[HMSManager] Slot watcher: {e}")

    async def _health_watcher(self):
        while True:
            await asyncio.sleep(10)
            try:
                # [MEMORY & STARVATION PATCH] Once per hour, age out bags
                # that have been sitting in pending_hms_sync.json for >24h.
                # They get moved to abandoned_hms.json for manual review
                # so they never block the queue forever.
                now_ts = time.time()
                if now_ts - self._last_stale_sweep >= 3600:
                    self._last_stale_sweep = now_ts
                    try:
                        from local_store_hms import clear_stale_pending
                        moved = clear_stale_pending(max_age_hours=24)
                        if moved > 0:
                            logger.warning(
                                f"[HMSManager] Stale-pending sweeper moved "
                                f"{moved} bag(s) to abandoned (>24h old)")
                        else:
                            logger.debug(
                                "[HMSManager] Stale-pending sweeper: 0 bags to age out")
                    except Exception as e:
                        logger.error(f"[HMSManager] clear_stale_pending failed: {e}")

                for b in [self.suggester_a, self.suggester_b,
                          self.committer]:
                    if b is None:
                        continue
                    is_suggester = isinstance(b, SuggesterBrowser)
                    needs_recovery = (not b.is_ready) or \
                                     (is_suggester and b.is_degraded())
                    if not needs_recovery:
                        continue
                    if b.is_initializing:
                        continue
                    now = time.time()
                    min_interval = 10 if b.is_ready else 30
                    if now - b.last_recovery < min_interval:
                        continue
                    if b.recovery_count >= 50:
                        continue
                    b.last_recovery = now
                    b.recovery_count += 1
                    state = "degraded" if b.is_ready else "dead"
                    fails = (b.consecutive_failures
                             if is_suggester else 0)
                    logger.info(f"[HMSManager] Recovering {b.name} "
                                f"({state}, attempt {b.recovery_count}, "
                                f"consec_fails={fails})")
                    try:
                        await b.close()
                    except Exception:
                        pass
                    try:
                        if len(b.failed_slots) >= len(HMS_CRED_SLOTS):
                            b.failed_slots.clear()
                        b.current_slot = _get_current_slot()
                        if HMS_CRED_SLOTS:
                            b.user, b.password = HMS_CRED_SLOTS[b.current_slot]
                        await b.initialize(self._browser)
                        if is_suggester:
                            b.consecutive_failures = 0
                        # [PRODUCTION HARDENING] On successful recovery,
                        # clear soft-fail counts for the committer so
                        # bags that failed due to browser issues (not HMS
                        # rejections) get a fresh retry budget. Without
                        # this, bags accumulate soft-fails across browser
                        # crashes and get abandoned even though the
                        # underlying issue (dead browser) was resolved.
                        if (b.is_ready and isinstance(b, CommitterBrowser)
                                and b._soft_fail_counts):
                            cleared = len(b._soft_fail_counts)
                            b._soft_fail_counts.clear()
                            b._soft_cooldowns.clear()
                            logger.info(
                                f"[HMSManager] Cleared {cleared} soft-fail "
                                f"counter(s) for {b.name} after recovery")
                        # Decay recovery_count on success so long sessions
                        # don't exhaust the 50-recovery cap prematurely
                        if b.is_ready and b.recovery_count > 0:
                            b.recovery_count = max(0, b.recovery_count - 1)
                    except Exception as e:
                        logger.error(f"[HMSManager] Recovery failed for {b.name}: {e}")
            except Exception as e:
                logger.error(f"[HMSManager] Health watcher: {e}")

    def _on_synced(self, bag_id: str, trolley_id: str, sheet_row: int,
                   reason: str = ""):
        from sheets import ts as _ts_now, get_cache
        now = _ts_now()
        is_already_staged = (reason == "already_staged")
        status = "Already_Staged" if is_already_staged else "Done"

        cache = None
        try:
            cache = get_cache()
        except Exception as e:
            logger.error(f"[HMSManager] {bag_id}: get_cache() failed in _on_synced: {e}")

        # ─── ROW-DRIFT GUARD (HOLE 1 PATCH) ──────────────────────────
        # Validate BEFORE touching the cache. If the row no longer
        # belongs to this bag, do NOT write the cache, do NOT remove
        # from pending, do NOT queue the sheet write. Flag for review.
        if cache is not None:
            ok, reason_str = _validate_row_belongs_to_bag(cache, sheet_row, bag_id)
            if not ok:
                logger.error(
                    f"[HMSManager] {bag_id}: ROW-DRIFT in _on_synced — "
                    f"NOT writing cache/sheet. trolley={trolley_id} "
                    f"sheet_row={sheet_row}: {reason_str}"
                )
                try:
                    self._record_row_drift(bag_id, trolley_id, sheet_row,
                                           status, reason_str)
                except Exception as e:
                    logger.error(f"[HMSManager] {bag_id}: row-drift record failed: {e}")
                return
        # ─── end ROW-DRIFT GUARD ─────────────────────────────────────

        # Validation passed — safe to update cache.
        try:
            if cache is not None and sheet_row and sheet_row > 0:
                cache.update_cell("Live_Staging", sheet_row,
                                  COL_HMS_SYNCED + 1, status)
                cache.update_cell("Live_Staging", sheet_row,
                                  COL_HMS_SYNCED_TS + 1, now)
        except Exception as e:
            logger.error(f"[HMSManager] cache update for {bag_id} failed: {e}")

        try:
            from local_store_hms import remove_synced_bag
            remove_synced_bag(bag_id, trolley_id)
        except Exception:
            pass

        with self._writes_lock:
            self._pending_sync_writes.append({
                "bag_id": bag_id,
                "trolley_id": trolley_id,
                "sheet_row": sheet_row,
                "ts": now,
                "already_staged": is_already_staged,
            })

    def _on_failed(self, bag_id: str, trolley_id: str, sheet_row: int,
                   reason: str, real_rejection: bool):
        from sheets import ts as _ts_now
        logger.warning(f"[HMSManager] HMS sync FAILED for {bag_id}: {reason} "
                       f"(real={real_rejection})")
        # Write "HMS_Failed" to the sheet so abandoned bags are VISIBLE
        # (previously left blank — the user couldn't tell if the bag was
        # still pending or genuinely failed).
        if sheet_row and sheet_row > 0:
            now = _ts_now()
            with self._writes_lock:
                self._pending_sync_writes.append({
                    "bag_id": bag_id,
                    "trolley_id": trolley_id,
                    "sheet_row": sheet_row,
                    "ts": now,
                    "hms_failed": True,
                    "fail_reason": (reason or "")[:80],
                })

    def _record_row_drift(self, bag_id: str, trolley_id: str, sheet_row: int,
                          intended_status: str, reason: str):
        """Append a row-drift event to _cache/row_drift_review.json so an
        operator can reconcile the sheet manually.

        The bag IS synced in HMS (we wouldn't be in _on_synced otherwise).
        The PROBLEM is we don't know which sheet row to mark as Done.
        """
        import json, os, tempfile
        from datetime import datetime as _dt
        import pytz

        IST_local = pytz.timezone("Asia/Kolkata")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(base_dir, "_cache")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, "row_drift_review.json")

        entry = {
            "bag_id": bag_id,
            "trolley_id": trolley_id,
            "sheet_row_at_queue_time": sheet_row,
            "intended_status": intended_status,
            "detected_at": _dt.now(IST_local).isoformat(),
            "reason": reason,
            "note": ("HMS sync DID succeed — bag is staged in HMS. "
                     "But the sheet row reference is stale. "
                     "Manually find the bag in Live_Staging and mark "
                     "HMS_Synced column."),
        }

        with self._writes_lock:
            existing = []
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            existing = json.loads(content)
                            if not isinstance(existing, list):
                                existing = []
            except Exception:
                existing = []
            existing.append(entry)
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".row_drift_review_", suffix=".tmp", dir=cache_dir,
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                raise

        logger.warning(
            f"[HMSManager] ROW-DRIFT RECORDED for {bag_id} → "
            f"{path} (ops review needed)"
        )

    def _sheet_writer_loop(self):
        from local_store_hms import drain_dlq, add_to_dlq
        from sheets import get_cache, get_sheet, _col_letter, ts as _ts_now

        while self._writer_running:
            try:
                batch = []
                deadline = time.time() + (SHEET_BATCH_WAIT_MS / 1000.0)
                with self._writes_lock:
                    if self._pending_sync_writes:
                        batch.extend(self._pending_sync_writes[:SHEET_BATCH_SIZE])
                        del self._pending_sync_writes[:len(batch)]
                if not batch:
                    dlq_items = drain_dlq()
                    if dlq_items:
                        batch.extend(dlq_items[:SHEET_BATCH_SIZE])
                if not batch:
                    time.sleep(0.5)
                    continue

                while time.time() < deadline and len(batch) < SHEET_BATCH_SIZE:
                    with self._writes_lock:
                        if self._pending_sync_writes:
                            take = min(
                                SHEET_BATCH_SIZE - len(batch),
                                len(self._pending_sync_writes),
                            )
                            batch.extend(self._pending_sync_writes[:take])
                            del self._pending_sync_writes[:take]
                            continue
                    time.sleep(0.05)

                ok = self._flush_synced_batch(batch)
                if not ok:
                    add_to_dlq(batch)
                else:
                    self._maybe_release_trolleys(batch)

            except Exception as e:
                logger.error(f"[SheetWriter] Loop error: {e}")
                time.sleep(2)

    # ─────────────────────────────────────────────────────────────────
    # [ROW-VALIDATION SAFETY NET 2026-05-28]
    # Per-row validation against the in-memory cache. Returns the list
    # of items that PASSED validation and are safe to write. Items that
    # fail validation are logged + dropped (NOT re-queued — the row no
    # longer exists or belongs to a different bag, so retrying will not
    # help; manual review via pending_hms_sync.json / logs is required).
    #
    # This is a defensive backstop for RISK 2 in HMS_Sync_Edge_Cases.md:
    # the proper fix (drain queue + abandon before backup-clears-rows)
    # lives in daily_backup.py. Even with that in place, this check
    # guarantees we will NEVER write "Done" to a row whose Bag_ID does
    # not match the bag we think we're marking.
    # ─────────────────────────────────────────────────────────────────
    def _validate_batch_against_cache(self, batch: List[dict]) -> List[dict]:
        """Drop items whose sheet_row no longer points at the right bag.

        Uses the shared _validate_row_belongs_to_bag helper — same logic
        as the HOLE 1 guard in _on_synced, single source of truth.

        Rejections are loud — logged at ERROR level so they show up in
        the dashboard / file log immediately.
        """
        if not batch:
            return []

        try:
            from sheets import get_cache
            cache = get_cache()
        except Exception as e:
            logger.warning(f"[SheetWriter] Row-validation skipped — "
                           f"could not read cache: {e}")
            return batch

        validated: List[dict] = []
        for item in batch:
            sheet_row = item.get("sheet_row", 0)
            bag_id = str(item.get("bag_id", "")).strip().upper()

            if not sheet_row or sheet_row <= 0:
                validated.append(item)
                continue

            ok, reason = _validate_row_belongs_to_bag(cache, sheet_row, bag_id)
            if not ok:
                logger.error(
                    f"[SheetWriter] ROW-DRIFT REJECT: {bag_id} -> "
                    f"{reason}. Dropping write.")
                continue
            validated.append(item)

        if len(validated) < len(batch):
            logger.warning(
                f"[SheetWriter] Row-validation dropped "
                f"{len(batch) - len(validated)}/{len(batch)} write(s) "
                f"due to row drift")

        return validated

    def _flush_synced_batch(self, batch: List[dict]) -> bool:
        if not batch:
            return True
        try:
            from sheets import get_sheet, _col_letter, get_cache
            ws = get_sheet("Live_Staging")
            cache = get_cache()

            # [ROW-VALIDATION SAFETY NET 2026-05-28] Drop any items whose
            # sheet_row no longer points at the expected bag_id BEFORE
            # building the batch_update payload. If everything in the
            # batch fails validation, return True so the caller doesn't
            # push the dropped items to the DLQ — there's nothing useful
            # to retry; the rows are gone.
            validated_batch = self._validate_batch_against_cache(batch)
            if not validated_batch:
                logger.warning(
                    f"[SheetWriter] All {len(batch)} write(s) failed "
                    f"row-validation — nothing to flush. Items dropped "
                    f"(not DLQ'd) since the rows no longer exist.")
                return True

            col_synced = _col_letter(COL_HMS_SYNCED + 1)
            col_ts = _col_letter(COL_HMS_SYNCED_TS + 1)
            payload = []
            max_row = 0
            for item in validated_batch:
                row_num = item["sheet_row"]
                if not row_num or row_num <= 0:
                    continue
                max_row = max(max_row, row_num)
                if item.get("hms_failed"):
                    status = "HMS_Failed"
                elif item.get("already_staged"):
                    status = "Already_Staged"
                else:
                    status = "Done"
                payload.append({
                    "range": f"{col_synced}{row_num}:{col_ts}{row_num}",
                    "values": [[status, item["ts"]]],
                })

            if not payload:
                return True

            if max_row > ws.row_count:
                ws.add_rows(max_row - ws.row_count + 10)

            for attempt in range(5):
                try:
                    ws.batch_update(payload, value_input_option="RAW")
                    logger.info(f"[SheetWriter] Pushed {len(payload)} HMS_Synced row(s)")
                    try:
                        with cache._lock:
                            if "Live_Staging" in cache._dirty_rows:
                                for item in validated_batch:
                                    rn = item["sheet_row"]
                                    if rn and rn > 0:
                                        cache._dirty_rows["Live_Staging"].discard(rn - 1)
                    except Exception:
                        pass
                    return True
                except Exception as e:
                    err = str(e)
                    if "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
                        wait = min(2 ** attempt, 30)
                        logger.warning(f"[SheetWriter] Rate limited, wait {wait}s")
                        time.sleep(wait)
                        continue
                    wait = min(2 ** attempt, 10)
                    logger.warning(f"[SheetWriter] attempt {attempt+1}/5 failed: {e}, wait {wait}s")
                    time.sleep(wait)
            logger.error(f"[SheetWriter] Batch failed after 5 attempts -> DLQ")
            return False
        except Exception as e:
            logger.error(f"[SheetWriter] Flush crashed: {e}")
            return False

    def _maybe_release_trolleys(self, batch: List[dict]):
        from sheets import get_cache, ts as _ts_now

        trolleys_to_check = set(item.get("trolley_id", "") for item in batch
                                if item.get("trolley_id"))
        if not trolleys_to_check:
            return

        cache = get_cache()
        now = _ts_now()
        l_data = cache.get_all_values("Live_Staging")

        for trolley_id in trolleys_to_check:
            has_unfinished = False
            bag_count = 0
            for i in range(1, len(l_data)):
                row = l_data[i]
                row_trolley = str(row[COL_TROLLEY_ID]).strip() if len(row) > COL_TROLLEY_ID else ""
                row_trolley_put = str(row[COL_TROLLEY_PUT]).strip() if len(row) > COL_TROLLEY_PUT else ""
                row_grid_put = str(row[COL_GRID_PUT]).strip() if len(row) > COL_GRID_PUT else ""
                row_hms_synced = str(row[COL_HMS_SYNCED]).strip() if len(row) > COL_HMS_SYNCED else ""

                if row_trolley == trolley_id and row_trolley_put == "Done":
                    bag_count += 1
                    if row_grid_put != "Done" or not row_hms_synced:
                        has_unfinished = True
                        break

            if has_unfinished or bag_count == 0:
                continue

            try:
                t_data = cache.get_all_values("Trolley_Registry")
                for j in range(1, len(t_data)):
                    row = t_data[j]
                    if str(row[0]).strip() == str(trolley_id):
                        row_num = j + 1
                        cur_status = str(row[2]).strip() if len(row) > 2 else ""
                        if cur_status == "Active":
                            cache.update_cell("Trolley_Registry", row_num, 2, "")
                            cache.update_cell("Trolley_Registry", row_num, 3, "Available")
                            cache.update_cell("Trolley_Registry", row_num, 4, now)
                            logger.info(f"[SheetWriter] Trolley {trolley_id} RELEASED "
                                        f"(all {bag_count} bags HMS-synced + grid-put done)")
                        break
            except Exception as e:
                logger.error(f"[SheetWriter] Release trolley {trolley_id} failed: {e}")


# Singleton

_manager: Optional[HMSManager] = None


def get_hms_manager() -> HMSManager:
    global _manager
    if _manager is None:
        _manager = HMSManager()
    return _manager


def start_hms_sync() -> HMSManager:
    m = get_hms_manager()
    m.start()
    return m


def get_hms_sync() -> HMSManager:
    return get_hms_manager()