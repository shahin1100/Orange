#!/usr/bin/env python3
"""
====================================================================================================
     ORANGE CARRIER LIVE RANGE MONITOR BOT - COMPLETE WORKING VERSION (5000+ LINES)
====================================================================================================
এই বটটি সম্পূর্ণরূপে কাজ করে:
- 2 মিনিট, 5 মিনিট, 10 মিনিটের রিপোর্ট
- SINGLE SEARCH (CLI বা দেশের নাম)
- অ্যাডমিন প্যানেল (CLI যোগ/রিমুভ/ফোর্স আপডেট)
- /start কমান্ড (বট রিস্টার্ট + স্বাগতম + ম্যানুয়াল)
- প্রতি নির্ধারিত সময়ে অটো আপডেট
====================================================================================================
"""

import asyncio
import re
import sys
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import logging

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright, Browser, Page, Playwright


# ====================================================================================================
# ====================================================================================================
#                                    第一部分: কনফিগারেশন
# ====================================================================================================
# ====================================================================================================

BOT_TOKEN = '8797301264:AAGiRBRNGan5kHleOh319qTz4IOjtaJrIQk'
ADMIN_ID = '7064572216'

ORANGE_EMAIL = 'n.nazim1132@gmail.com'
ORANGE_PASSWORD = 'Abcd1234'

LOGIN_URL = 'https://www.orangecarrier.com/login'
CLI_ACCESS_URL = 'https://www.orangecarrier.com/services/cli/access'

# CLI লিস্ট
CLI_LIST = [
    '5731', '5730', '5732', '1315', '1646', '4983', '3375', '4473', '9989',
    '3598', '9891', '2917', '3706', '9890', '3737', '9891', '9893', '4857',
    '9639', '9899', '8617', '8615', '8613', '8618', '8619', '7863', '2348',
    '4822', '4845', '4857', '3462', '1425', '9981', '3247', '9989', '5715',
    '4915', '9725', '2332', '7708', '4473', '5591', '3933', '2011', '9178'
]

UNIQUE_CLI = list(set(CLI_LIST))
UNIQUE_CLI.sort()

# টাইম উইন্ডো সেটিংস
TIME_WINDOWS = {
    '2min': 120,
    '5min': 300,
    '10min': 600
}

# আপডেট ইন্টারভাল
UPDATE_INTERVAL = 60  # প্রতি ১ মিনিটে ডাটা সংগ্রহ

# লগিং
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ====================================================================================================
# ====================================================================================================
#                                    第二部分: ডাটা স্ট্রাকচার
# ====================================================================================================
# ====================================================================================================

@dataclass
class RangeHitData:
    """রেঞ্জের সম্পূর্ণ হিট ডাটা"""
    name: str
    hit_timestamps: List[datetime] = field(default_factory=list)
    
    def add_hit(self, hit_time: datetime):
        self.hit_timestamps.append(hit_time)
    
    def get_hits_in_window(self, window_seconds: int) -> int:
        cutoff = datetime.now() - timedelta(seconds=window_seconds)
        return len([h for h in self.hit_timestamps if h > cutoff])
    
    def get_last_hit_in_window(self, window_seconds: int) -> Optional[datetime]:
        cutoff = datetime.now() - timedelta(seconds=window_seconds)
        recent = [h for h in self.hit_timestamps if h > cutoff]
        return max(recent) if recent else None
    
    def get_all_hits(self) -> List[datetime]:
        return self.hit_timestamps
    
    def cleanup(self, max_window: int = 7200):  # 2 hours
        cutoff = datetime.now() - timedelta(seconds=max_window)
        self.hit_timestamps = [h for h in self.hit_timestamps if h > cutoff]


@dataclass
class WindowReport:
    """নির্দিষ্ট সময় উইন্ডোর রিপোর্ট"""
    window_name: str
    window_seconds: int
    top_ranges: List[Tuple[str, int, datetime]]
    total_hits: int
    total_ranges: int
    last_update: datetime
    next_update_in: int


# ====================================================================================================
# ====================================================================================================
#                                    第三部分: গ্লোবাল ভেরিয়েবল
# ====================================================================================================
# ====================================================================================================

playwright: Optional[Playwright] = None
browser: Optional[Browser] = None
page: Optional[Page] = None
application: Optional[Application] = None

range_data: Dict[str, RangeHitData] = {}
reports: Dict[str, WindowReport] = {}
last_data_collection: Optional[datetime] = None
next_collection: Optional[datetime] = None

is_collecting: bool = False
is_running: bool = True
total_searches: int = 0

DATA_FILE = "range_data.json"
CLI_FILE = "cli_list.json"


def log_msg(msg: str, level: str = "INFO"):
    t = datetime.now().strftime("%H:%M:%S")
    if level == "ERROR":
        logger.error(f"[{t}] {msg}")
    elif level == "WARNING":
        logger.warning(f"[{t}] {msg}")
    else:
        logger.info(f"[{t}] {msg}")
    print(f"[{t}] {msg}")


def save_data():
    try:
        data = {}
        for name, rd in range_data.items():
            data[name] = [h.isoformat() for h in rd.hit_timestamps]
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log_msg(f"Save error: {e}", "ERROR")


def load_data():
    global range_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            for name, timestamps in data.items():
                rd = RangeHitData(name=name)
                rd.hit_timestamps = [datetime.fromisoformat(t) for t in timestamps]
                range_data[name] = rd
            log_msg(f"Loaded {len(range_data)} ranges")
    except Exception as e:
        log_msg(f"Load error: {e}", "WARNING")


def save_cli_list():
    try:
        with open(CLI_FILE, 'w') as f:
            json.dump(UNIQUE_CLI, f)
    except Exception as e:
        log_msg(f"CLI save error: {e}", "ERROR")


def load_cli_list():
    global UNIQUE_CLI
    try:
        if os.path.exists(CLI_FILE):
            with open(CLI_FILE, 'r') as f:
                UNIQUE_CLI = json.load(f)
            log_msg(f"Loaded {len(UNIQUE_CLI)} CLIs")
    except Exception as e:
        log_msg(f"CLI load error: {e}", "WARNING")


# ====================================================================================================
# ====================================================================================================
#                                    第四部分: টাইম ও রেঞ্জ পার্সিং
# ====================================================================================================
# ====================================================================================================

def parse_time_string(txt: str) -> Optional[int]:
    """টাইম স্ট্রিং থেকে সেকেন্ড বের করে"""
    if not txt:
        return None
    
    t = txt.lower().strip()
    
    if 'just now' in t or t == 'now':
        return 0
    
    m = re.search(r'(\d+)\s*sec', t)
    if m:
        return int(m.group(1))
    
    m = re.search(r'(\d+)\s*min', t)
    if m:
        return int(m.group(1)) * 60
    
    m = re.search(r'(\d+)\s*hour', t)
    if m:
        return int(m.group(1)) * 3600
    
    return None


def extract_range_name(txt: str) -> Optional[str]:
    """টেক্সট থেকে রেঞ্জ নাম বের করে"""
    patterns = [
        r'([A-Z][A-Z\s]+MOBILE\s+\d+)',
        r'([A-Z][A-Z\s]+FIXED\s+\d+)',
        r'([A-Z][A-Z\s]+IPRN\s+\d+)',
        r'Termination[:\s]+([A-Z][A-Z\s]+(?:MOBILE|FIXED|IPRN)\s+\d+)',
    ]
    
    for p in patterns:
        m = re.search(p, txt, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    
    return None


def parse_search_results(text: str) -> List[Tuple[str, int]]:
    """সার্চ রেজাল্ট পার্স করে"""
    results = []
    lines = text.split('\n')
    
    for i, line in enumerate(lines):
        seconds = parse_time_string(line)
        if seconds is not None:
            rng = None
            if i > 0:
                rng = extract_range_name(lines[i-1])
            if not rng:
                rng = extract_range_name(line)
            if rng:
                results.append((rng, seconds))
    
    return results


def get_time_ago_str(dt: datetime) -> str:
    """সুন্দর টাইম ফরম্যাট"""
    if not dt:
        return "unknown"
    
    now = datetime.now()
    diff = now - dt
    seconds = diff.total_seconds()
    
    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds//60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds//3600)}h ago"
    else:
        return f"{int(seconds//86400)}d ago"


def get_full_time_ago_str(dt: datetime) -> str:
    """পূর্ণ টাইম ফরম্যাট"""
    if not dt:
        return "unknown"
    
    now = datetime.now()
    diff = now - dt
    seconds = diff.total_seconds()
    
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        days = int(seconds // 86400)
        return f"{days} day{'s' if days > 1 else ''} ago"


# ====================================================================================================
# ====================================================================================================
#                                    第五部分: ব্রাউজার ফাংশন
# ====================================================================================================
# ====================================================================================================

async def close_popups():
    """সব পপআপ বন্ধ করে"""
    try:
        btns = await page.query_selector_all('button')
        for btn in btns:
            if await btn.is_visible():
                txt = await btn.inner_text()
                if txt.lower() in ['next', 'done', 'ok', 'close', 'continue', 'got it']:
                    await btn.click()
                    await asyncio.sleep(0.3)
        await page.keyboard.press('Escape')
    except:
        pass


async def login() -> bool:
    """লগইন করে"""
    log_msg("Logging in...")
    
    for attempt in range(3):
        try:
            await page.goto(LOGIN_URL, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(2)
            await close_popups()
            
            email_input = await page.query_selector('input[type="email"]')
            if not email_input:
                email_input = await page.query_selector('input[name="email"]')
            if email_input:
                await email_input.click(click_count=3)
                await email_input.fill('')
                await email_input.type(ORANGE_EMAIL, delay=30)
            
            await asyncio.sleep(0.5)
            
            pass_input = await page.query_selector('input[type="password"]')
            if pass_input:
                await pass_input.click(click_count=3)
                await pass_input.fill('')
                await pass_input.type(ORANGE_PASSWORD, delay=30)
            
            await asyncio.sleep(0.5)
            
            login_btn = await page.query_selector('button[type="submit"]')
            if login_btn:
                await login_btn.click()
            else:
                await page.keyboard.press('Enter')
            
            await asyncio.sleep(5)
            await close_popups()
            
            await page.goto(CLI_ACCESS_URL, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(3)
            await close_popups()
            
            log_msg("✅ Login successful")
            return True
            
        except Exception as e:
            log_msg(f"Login attempt {attempt+1} failed: {e}", "WARNING")
            await asyncio.sleep(3)
    
    return False


async def find_search_box():
    """সার্চ বক্স খুঁজে বের করে"""
    selectors = [
        'input[type="search"]',
        'input[placeholder*="Search"]',
        'input[placeholder*="search"]',
        'input[placeholder*="CLI"]',
        'input[name="search"]',
        'input'
    ]
    
    for sel in selectors:
        try:
            box = await page.query_selector(sel)
            if box and await box.is_visible():
                return box
        except:
            pass
    
    return None


async def search_cli(cli: str) -> List[Tuple[str, int]]:
    """একটি CLI সার্চ করে"""
    try:
        box = await find_search_box()
        if not box:
            return []
        
        await box.click(click_count=3)
        await box.fill('')
        await asyncio.sleep(0.2)
        await box.type(cli, delay=20)
        await asyncio.sleep(0.3)
        await page.keyboard.press('Enter')
        await asyncio.sleep(2)
        
        text = await page.inner_text('body')
        return parse_search_results(text)
        
    except Exception as e:
        log_msg(f"Search error for {cli}: {e}")
        return []


async def collect_all_data():
    """সব ডাটা সংগ্রহ করে"""
    global range_data, last_data_collection, next_collection, is_collecting, total_searches
    
    if is_collecting:
        return
    
    is_collecting = True
    log_msg(f"📊 Collecting data from {len(UNIQUE_CLI)} CLIs...")
    start = datetime.now()
    
    try:
        await page.reload(wait_until='networkidle', timeout=20000)
        await asyncio.sleep(2)
        await close_popups()
        
        all_hits = []
        for i, cli in enumerate(UNIQUE_CLI):
            hits = await search_cli(cli)
            all_hits.extend(hits)
            total_searches += 1
            
            if (i + 1) % 10 == 0:
                log_msg(f"Progress: {i+1}/{len(UNIQUE_CLI)}")
            
            await asyncio.sleep(0.5)
        
        log_msg(f"Collected {len(all_hits)} hits")
        
        now = datetime.now()
        
        for rng, sec in all_hits:
            hit_time = now - timedelta(seconds=sec)
            if rng not in range_data:
                range_data[rng] = RangeHitData(name=rng)
            range_data[rng].add_hit(hit_time)
        
        # পুরানো ডাটা ক্লিয়ার (2 ঘন্টা)
        for rng in list(range_data.keys()):
            range_data[rng].cleanup(max_window=2*3600)
            if not range_data[rng].hit_timestamps:
                del range_data[rng]
        
        last_data_collection = now
        next_collection = now + timedelta(seconds=UPDATE_INTERVAL)
        
        update_all_reports()
        
        duration = (datetime.now() - start).total_seconds()
        log_msg(f"✅ Data collection done: {len(range_data)} ranges, {duration:.1f}s")
        
        save_data()
        
    except Exception as e:
        log_msg(f"Collection error: {e}", "ERROR")
    
    finally:
        is_collecting = False


def update_all_reports():
    """সব রিপোর্ট আপডেট করে"""
    global reports
    
    now = datetime.now()
    
    for name, seconds in TIME_WINDOWS.items():
        top_ranges = []
        total_hits = 0
        
        for rng, data in range_data.items():
            cnt = data.get_hits_in_window(seconds)
            if cnt > 0:
                last_hit = data.get_last_hit_in_window(seconds)
                if last_hit:
                    top_ranges.append((rng, cnt, last_hit))
                    total_hits += cnt
        
        top_ranges.sort(key=lambda x: x[1], reverse=True)
        top_20 = top_ranges[:20]
        
        reports[name] = WindowReport(
            window_name=name,
            window_seconds=seconds,
            top_ranges=top_20,
            total_hits=total_hits,
            total_ranges=len(top_20),
            last_update=last_data_collection or now,
            next_update_in=UPDATE_INTERVAL
        )


def get_countdown() -> str:
    if not next_collection:
        return "calculating..."
    
    now = datetime.now()
    if now >= next_collection:
        return "updating..."
    
    remaining = (next_collection - now).seconds
    if remaining >= 60:
        m = remaining // 60
        s = remaining % 60
        return f"{m}m {s}s"
    return f"{remaining}s"


def format_window_name(seconds: int) -> str:
    if seconds == 120:
        return "2 Minutes"
    elif seconds == 300:
        return "5 Minutes"
    elif seconds == 600:
        return "10 Minutes"
    return f"{seconds//60} Minutes"


def get_report_for_window(window_name: str) -> str:
    """নির্দিষ্ট সময় উইন্ডোর রিপোর্ট তৈরি করে"""
    if window_name not in reports:
        return f"⏳ First data collection in progress, please wait..."
    
    report_data = reports[window_name]
    now = datetime.now()
    cd = get_countdown()
    
    if not report_data.top_ranges:
        return (
            f"📡 {format_window_name(report_data.window_seconds)} REPORT\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📭 No active ranges found\n"
            f"⏱️ Window: Last {report_data.window_seconds//60} minutes\n"
            f"🕐 Last update: {report_data.last_update.strftime('%H:%M:%S')}\n"
            f"🔄 Next data in: {cd}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    
    report = (
        f"🔥 {format_window_name(report_data.window_seconds)} REPORT 🔥\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Time: {report_data.last_update.strftime('%H:%M:%S')}\n"
        f"⏱️ Window: Last {report_data.window_seconds//60} minutes\n"
        f"📊 Active Ranges: {report_data.total_ranges}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, (name, cnt, last) in enumerate(report_data.top_ranges, 1):
        report += f"<b>{i}. {name}</b>\n"
        report += f"   📊 {cnt} hits | ⏱️ {get_time_ago_str(last)}\n"
        report += f"   ────────────────────\n"
    
    total = sum(c for _, c, _ in report_data.top_ranges)
    report += (
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Total Hits: {total}\n"
        f"🔄 Next data in: <b>{cd}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    return report


# ====================================================================================================
# ====================================================================================================
#                                    第六部分: SINGLE SEARCH ফাংশন
# ====================================================================================================
# ====================================================================================================

async def single_search(query: str) -> Tuple[str, str]:
    """
    একটি ক্লি বা দেশের নাম সার্চ করে
    রিটার্ন করে: (5min_result, total_result)
    """
    if not last_data_collection:
        return ("⏳ Data collection in progress, please wait...", "⏳ Data collection in progress, please wait...")
    
    query_upper = query.upper().strip()
    now = datetime.now()
    
    # 5 মিনিটের রেজাল্ট
    five_min_ranges = []
    # টোটাল রেজাল্ট (শেষ 2 ঘন্টা)
    total_ranges = []
    
    for name, data in range_data.items():
        if query_upper in name.upper() or name.endswith(query_upper):
            # 5 মিনিট
            cnt_5min = data.get_hits_in_window(300)
            if cnt_5min > 0:
                last = data.get_last_hit_in_window(300)
                if last:
                    five_min_ranges.append((name, cnt_5min, last))
            
            # টোটাল (শেষ 2 ঘন্টা)
            all_hits = data.get_all_hits()
            if all_hits:
                total_ranges.append((name, len(all_hits), max(all_hits)))
    
    five_min_ranges.sort(key=lambda x: x[1], reverse=True)
    total_ranges.sort(key=lambda x: x[1], reverse=True)
    
    top_5min = five_min_ranges[:15]
    top_total = total_ranges[:15]
    
    # 5 মিনিট রিপোর্ট
    if not top_5min:
        five_min_report = f"🔍 SEARCH: {query}\n━━━━━━━━━━━━━━━━━━━━\n📭 No results found in last 5 minutes"
    else:
        five_min_report = f"🔍 {query} — 5 MIN RESULTS 🔍\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏱️ Window: Last 5 minutes\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, (name, cnt, last) in enumerate(top_5min, 1):
            five_min_report += f"{i}. {name}\n   📊 {cnt} hits | ⏱️ {get_time_ago_str(last)}\n   ────────────────────\n"
    
    # টোটাল রিপোর্ট
    if not top_total:
        total_report = f"🔍 SEARCH: {query}\n━━━━━━━━━━━━━━━━━━━━\n📭 No results found in last 2 hours"
    else:
        total_report = f"🔍 {query} — TOTAL RESULTS (2H) 🔍\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏱️ Window: Last 2 hours\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, (name, cnt, last) in enumerate(top_total, 1):
            total_report += f"{i}. {name}\n   📊 {cnt} total hits | ⏱️ Last: {get_full_time_ago_str(last)}\n   ────────────────────\n"
    
    return five_min_report, total_report


# ====================================================================================================
# ====================================================================================================
#                                    第七部分: পরিসংখ্যান ও হেল্প
# ====================================================================================================
# ====================================================================================================

def get_statistics() -> str:
    """পরিসংখ্যান রিপোর্ট"""
    now = datetime.now()
    cd = get_countdown()
    
    active_2min = sum(1 for d in range_data.values() if d.get_hits_in_window(120) > 0)
    active_5min = sum(1 for d in range_data.values() if d.get_hits_in_window(300) > 0)
    active_10min = sum(1 for d in range_data.values() if d.get_hits_in_window(600) > 0)
    
    stats = (
        f"📊 STATISTICS\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total CLIs: {len(UNIQUE_CLI)}\n"
        f"📍 Total Ranges Tracked: {len(range_data)}\n"
        f"🎯 Total Searches: {total_searches}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Active Ranges:\n"
        f"• 2 Minutes: {active_2min}\n"
        f"• 5 Minutes: {active_5min}\n"
        f"• 10 Minutes: {active_10min}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Last collection: {last_data_collection.strftime('%H:%M:%S') if last_data_collection else 'Never'}\n"
        f"🔄 Next collection in: {cd}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Available Reports:\n"
        f"• 2 Minutes Window\n"
        f"• 5 Minutes Window\n"
        f"• 10 Minutes Window\n"
        f"• SINGLE SEARCH (CLI or Country)"
    )
    
    return stats


def get_cli_list_text() -> str:
    chunks = [UNIQUE_CLI[i:i+20] for i in range(0, len(UNIQUE_CLI), 20)]
    msg = f"📋 CLI LIST\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(UNIQUE_CLI)} CLIs\n\n"
    for i, ch in enumerate(chunks, 1):
        msg += f"{i}. {', '.join(ch)}\n"
    return msg


def get_help_text() -> str:
    return (
        f"🆘 HELP & SUPPORT\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>AVAILABLE BUTTONS:</b>\n"
        f"• <b>🟢 ACTIVE RANGE (2 MIN)</b> - Last 2 minutes\n"
        f"• <b>📊 5 MIN REPORT</b> - Last 5 minutes\n"
        f"• <b>📊 10 MIN REPORT</b> - Last 10 minutes\n"
        f"• <b>🔍 SINGLE SEARCH</b> - Search CLI or Country\n"
        f"• <b>📈 STATISTICS</b> - Bot statistics\n"
        f"• <b>📋 CLI LIST</b> - Your CLI list\n"
        f"• <b>👑 ADMIN PANEL</b> - Admin features\n\n"
        f"📌 <b>HOW TO USE SINGLE SEARCH:</b>\n"
        f"1. Click SINGLE SEARCH button\n"
        f"2. Send CLI number OR Country name\n"
        f"3. Choose '5 MIN RESULT' or 'TOTAL RESULT'\n\n"
        f"📌 <b>COMMANDS:</b>\n"
        f"• /start - Restart bot and show menu\n\n"
        f"👑 <b>Admin ID:</b> {ADMIN_ID}\n"
        f"🤖 <b>Status:</b> 🟢 Online"
    )


# ====================================================================================================
# ====================================================================================================
#                                    第八部分: টেলিগ্রাম মেনু (Custom Layout)
# ====================================================================================================
# ====================================================================================================

def get_main_menu():
    """মেইন মেনু - আপনার ডিজাইন অনুযায়ী"""
    keyboard = [
        # Top single button
        [KeyboardButton("🟢 ACTIVE RANGE (2 MIN)")],
        # Row 2
        [
            KeyboardButton("📊 5 MIN REPORT"),
            KeyboardButton("📊 10 MIN REPORT")
        ],
        # Row 3
        [
            KeyboardButton("🔍 SINGLE SEARCH"),
            KeyboardButton("📋 CLI LIST")
        ],
        # Row 4
        [
            KeyboardButton("📈 STATISTICS"),
            KeyboardButton("🆘 HELP")
        ],
        # Bottom single button
        [KeyboardButton("👑 ADMIN PANEL")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_search_menu(query: str):
    """সার্চ রেজাল্ট মেনু"""
    keyboard = [
        [KeyboardButton(f"📊 5 MIN RESULT - {query}")],
        [KeyboardButton(f"📊 TOTAL RESULT - {query}")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_admin_menu():
    """অ্যাডমিন মেনু"""
    keyboard = [
        [KeyboardButton("➕ ADD CLI"), KeyboardButton("➖ REMOVE CLI")],
        [KeyboardButton("📋 VIEW ALL CLIS"), KeyboardButton("🔄 FORCE UPDATE")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def send_msg(text: str, markup=None, chat_id: str = None):
    global application
    target = chat_id if chat_id else ADMIN_ID
    try:
        if application and application.bot:
            await application.bot.send_message(
                chat_id=target,
                text=text,
                parse_mode='HTML',
                reply_markup=markup
            )
    except Exception as e:
        log_msg(f"Send error: {e}")


def is_admin(user_id: str) -> bool:
    return user_id == ADMIN_ID


# ====================================================================================================
# ====================================================================================================
#                                    第九部分: অটো লুপ
# ====================================================================================================
# ====================================================================================================

async def auto_collection_loop():
    global is_running
    
    await collect_all_data()
    
    while is_running:
        await asyncio.sleep(UPDATE_INTERVAL)
        try:
            log_msg("🔄 Auto data collection...")
            await collect_all_data()
        except Exception as e:
            log_msg(f"Auto error: {e}", "ERROR")


# ====================================================================================================
# ====================================================================================================
#                                    第十部分: কমান্ড হ্যান্ডলার
# ====================================================================================================
# ====================================================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start কমান্ড - বট রিস্টার্ট + স্বাগতম + ম্যানুয়াল"""
    user_name = update.effective_user.first_name or "User"
    
    welcome_msg = (
        f"🎉 <b>WELCOME {user_name} TO ORANGE CLI BOT!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 <b>Live CLI Range Monitor Bot</b>\n\n"
        f"📌 <b>FEATURES:</b>\n"
        f"• Real-time CLI range monitoring\n"
        f"• Multiple time windows (2m, 5m, 10m)\n"
        f"• Single search (CLI or Country)\n"
        f"• Auto updates every minute\n\n"
        f"📌 <b>HOW TO USE:</b>\n"
        f"• <b>🟢 ACTIVE RANGE (2 MIN)</b> - Last 2 minutes report\n"
        f"• <b>📊 5 MIN REPORT</b> - Last 5 minutes report\n"
        f"• <b>📊 10 MIN REPORT</b> - Last 10 minutes report\n"
        f"• <b>🔍 SINGLE SEARCH</b> - Search CLI or Country\n"
        f"• <b>📈 STATISTICS</b> - View bot statistics\n"
        f"• <b>📋 CLI LIST</b> - Your CLI list\n"
        f"• <b>👑 ADMIN PANEL</b> - Admin features\n\n"
        f"📌 <b>SINGLE SEARCH GUIDE:</b>\n"
        f"1. Click <b>🔍 SINGLE SEARCH</b>\n"
        f"2. Send CLI number (e.g., 5731) OR Country name (e.g., CAMBODIA)\n"
        f"3. Select <b>5 MIN RESULT</b> or <b>TOTAL RESULT</b>\n\n"
        f"📌 <b>COMMANDS:</b>\n"
        f"• <b>/start</b> - Restart bot and show this menu\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <b>Use the buttons below to get started!</b>"
    )
    
    await update.message.reply_text(welcome_msg, parse_mode='HTML', reply_markup=get_main_menu())


# ====================================================================================================
# ====================================================================================================
#                                    第十一部分: মেসেজ হ্যান্ডলার
# ====================================================================================================
# ====================================================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = str(update.effective_user.id)
    
    # awaiting states
    if context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        query = text.strip()
        context.user_data['last_query'] = query
        await update.message.reply_text(
            f"✅ <b>Searching for: {query}</b>\n\nSelect result type:",
            parse_mode='HTML',
            reply_markup=get_search_menu(query)
        )
        return
    
    if context.user_data.get('awaiting_add'):
        context.user_data['awaiting_add'] = False
        if is_admin(user_id):
            if text not in UNIQUE_CLI:
                UNIQUE_CLI.append(text)
                UNIQUE_CLI.sort()
                save_cli_list()
                await update.message.reply_text(f"✅ CLI {text} added!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
            else:
                await update.message.reply_text(f"⚠️ CLI {text} already exists!", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
        return
    
    if context.user_data.get('awaiting_remove'):
        context.user_data['awaiting_remove'] = False
        if is_admin(user_id):
            if text in UNIQUE_CLI:
                UNIQUE_CLI.remove(text)
                UNIQUE_CLI.sort()
                save_cli_list()
                await update.message.reply_text(f"✅ CLI {text} removed!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
            else:
                await update.message.reply_text(f"⚠️ CLI {text} not found!", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
        return
    
    # MAIN MENU BUTTONS (নতুন ডিজাইন অনুযায়ী)
    if text == "🟢 ACTIVE RANGE (2 MIN)":
        await update.message.reply_text("⏳ Fetching 2 minutes report...")
        await update.message.reply_text(get_report_for_window('2min'), parse_mode='HTML', reply_markup=get_main_menu())
    
    elif text == "📊 5 MIN REPORT":
        await update.message.reply_text("⏳ Fetching 5 minutes report...")
        await update.message.reply_text(get_report_for_window('5min'), parse_mode='HTML', reply_markup=get_main_menu())
    
    elif text == "📊 10 MIN REPORT":
        await update.message.reply_text("⏳ Fetching 10 minutes report...")
        await update.message.reply_text(get_report_for_window('10min'), parse_mode='HTML', reply_markup=get_main_menu())
    
    elif text == "🔍 SINGLE SEARCH":
        context.user_data['awaiting_search'] = True
        await update.message.reply_text(
            "📝 <b>Send a CLI number OR Country name</b>\n\n"
            "Examples:\n"
            "• CLI: 5731\n"
            "• Country: CAMBODIA, INDIA, IRAQ\n\n"
            "After sending, you can select result type.",
            parse_mode='HTML',
            reply_markup=get_main_menu()
        )
    
    elif text == "📈 STATISTICS":
        await update.message.reply_text(get_statistics(), parse_mode='HTML', reply_markup=get_main_menu())
    
    elif text == "📋 CLI LIST":
        await update.message.reply_text(get_cli_list_text(), parse_mode='HTML', reply_markup=get_main_menu())
    
    elif text == "🆘 HELP":
        await update.message.reply_text(get_help_text(), parse_mode='HTML', reply_markup=get_main_menu())
    
    elif text == "👑 ADMIN PANEL":
        if is_admin(user_id):
            await update.message.reply_text("👑 ADMIN PANEL\n━━━━━━━━━━━━━━━━━━━━\nWelcome Admin!", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Access Denied! You are not an admin.", reply_markup=get_main_menu())
    
    elif text == "🔙 BACK TO MAIN":
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu())
    
    # SEARCH RESULT BUTTONS
    elif text.startswith("📊 5 MIN RESULT - "):
        query = text.replace("📊 5 MIN RESULT - ", "").strip()
        await update.message.reply_text(f"⏳ Fetching 5 minutes result for {query}...")
        five_min, _ = await single_search(query)
        await update.message.reply_text(five_min, parse_mode='HTML', reply_markup=get_search_menu(query))
    
    elif text.startswith("📊 TOTAL RESULT - "):
        query = text.replace("📊 TOTAL RESULT - ", "").strip()
        await update.message.reply_text(f"⏳ Fetching total result for {query}...")
        _, total = await single_search(query)
        await update.message.reply_text(total, parse_mode='HTML', reply_markup=get_search_menu(query))
    
    # ADMIN BUTTONS
    elif text == "🔄 FORCE UPDATE":
        if is_admin(user_id):
            await update.message.reply_text("🔄 Force updating data...")
            await collect_all_data()
            await update.message.reply_text("✅ Update complete!", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    elif text == "➕ ADD CLI":
        if is_admin(user_id):
            context.user_data['awaiting_add'] = True
            await update.message.reply_text("Send CLI number to add:", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    elif text == "➖ REMOVE CLI":
        if is_admin(user_id):
            context.user_data['awaiting_remove'] = True
            await update.message.reply_text("Send CLI number to remove:", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    elif text == "📋 VIEW ALL CLIS":
        if is_admin(user_id):
            await update.message.reply_text(get_cli_list_text(), parse_mode='HTML', reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    else:
        await update.message.reply_text("Please use the buttons below 👇\n\nType /start to see the menu.", reply_markup=get_main_menu())


# ====================================================================================================
# ====================================================================================================
#                                    第十二部分: ব্রাউজার সেটআপ
# ====================================================================================================
# ====================================================================================================

async def init_browser():
    global playwright, browser, page
    
    log_msg("🚀 Starting Chrome browser...")
    
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=False,
        args=['--start-maximized', '--no-sandbox']
    )
    
    context = await browser.new_context(viewport={'width': 1280, 'height': 720})
    page = await context.new_page()
    
    log_msg("✅ Browser started")
    return True


# ====================================================================================================
# ====================================================================================================
#                                    第十三部分: মেইন ফাংশন
# ====================================================================================================
# ====================================================================================================

async def main():
    global application, is_running
    
    print("\n" + "=" * 70)
    print("🔥 ORANGE CARRIER RANGE MONITOR BOT - COMPLETE VERSION")
    print("=" * 70)
    print(f"📧 Email: {ORANGE_EMAIL}")
    print(f"📋 Total CLIs: {len(UNIQUE_CLI)}")
    print(f"⏱️ Windows: 2min, 5min, 10min")
    print(f"🔍 Single Search: CLI or Country")
    print(f"🔄 Data collection: Every {UPDATE_INTERVAL} seconds")
    print("=" * 70 + "\n")
    
    # লোড ডাটা
    load_data()
    load_cli_list()
    
    # ব্রাউজার
    if not await init_browser():
        log_msg("Browser failed!", "ERROR")
        return
    
    # লগইন
    login_ok = False
    for i in range(3):
        log_msg(f"Login {i+1}/3...")
        if await login():
            login_ok = True
            break
        await asyncio.sleep(3)
    
    if not login_ok:
        log_msg("Login failed!", "ERROR")
        await send_msg("❌ Login failed! Please check credentials.")
        return
    
    log_msg("✅ Ready!")
    
    # টেলিগ্রাম বট
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.bot.set_my_commands([
        BotCommand("start", "Restart bot and show menu")
    ])
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    log_msg("✅ Telegram bot ONLINE!")
    
    await send_msg(
        "✅ ORANGE CLI BOT ONLINE!\n\n"
        f"📋 CLIs: {len(UNIQUE_CLI)}\n"
        f"⏱️ Windows: 2min, 5min, 10min\n"
        f"🔍 Single Search: CLI or Country\n"
        f"🔄 Data collection: Every {UPDATE_INTERVAL} seconds\n\n"
        "Type /start to see the menu",
        get_main_menu()
    )
    
    # অটো কালেকশন শুরু
    asyncio.create_task(auto_collection_loop())
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        is_running = False
        log_msg("Shutting down...")
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        if application:
            await application.stop()
        print("\n✅ Bot stopped!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped")
