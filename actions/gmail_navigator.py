"""
gmail_navigator.py — Autonomous Gmail Navigator for SIVA
"""

import asyncio
import os
import sys
from pathlib import Path

from config import get_base_dir as _get_base_dir
_base_dir = _get_base_dir

sys.path.append(str(_base_dir()))
try:
    from actions.browser_control import _real_profile_dir, _find_exe_windows
except ImportError:
    pass

async def _navigate_and_read(search_query: str) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return "Failed: Playwright is not installed. Run 'pip install playwright' and 'playwright install chromium'."

    async with async_playwright() as p:
        exe_path = _find_exe_windows("chrome")
        # Use a dedicated SIVA profile so it never conflicts with the user's active Chrome
        profile_dir = str(Path.home() / ".siva_profiles" / "chrome")
        os.makedirs(profile_dir, exist_ok=True)
        
        if not exe_path:
            return "Failed: Chrome executable not found."
            
        print("[Gmail] 🚀 Launching SIVA's dedicated Chrome profile...")
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                executable_path=exe_path,
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"]
            )
        except Exception as e:
            return f"Failed to launch Chrome: {e}."

        page = await context.new_page()
        
        print("[Gmail] 🌐 Navigating to Gmail...")
        await page.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="networkidle")
        
        print(f"[Gmail] 🔍 Searching for: {search_query}")
        try:
            search_input = page.locator('input[aria-label="Search mail"]')
            await search_input.wait_for(state="visible", timeout=15000)
            await search_input.fill(search_query)
            await search_input.press("Enter")
        except Exception:
            # We intentionally leave the context open so the user can log in
            return "Failed: I could not find the Gmail search bar. You likely need to log into Gmail once for SIVA's dedicated profile. I have left the browser window open for you to log in. Please log in and try again!"
        
        print("[Gmail] ⏳ Waiting for search results...")
        await page.wait_for_timeout(3000)
        
        try:
            email_row = page.locator('tr[role="row"]').nth(1)
            await email_row.wait_for(state="visible", timeout=5000)
            await email_row.click()
        except Exception:
            await context.close()
            return "Failed: Could not find any emails matching that search."
        
        print("[Gmail] 📖 Extracting email text...")
        await page.wait_for_timeout(2000)
        
        try:
            body = page.locator('.a3s.aiL')
            if await body.count() > 0:
                email_text = await body.first.inner_text()
            else:
                email_text = await page.evaluate('document.body.innerText')
        except Exception:
            email_text = await page.evaluate('document.body.innerText')
            
        await context.close()
        return email_text

def read_gmail_action(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """
    Action hook for autonomously navigating Gmail and reading an email out loud.
    """
    search_query = parameters.get("search_query", "").strip()
    if not search_query:
        return "I need a search query to find the email, like 'from: John' or 'subject: project'."
        
    try:
        email_text = asyncio.run(_navigate_and_read(search_query))
        if email_text.startswith("Failed"):
            return email_text
            
        print("[Gmail] ✅ Email extracted successfully. Passing to Plain Language Reader...")
        
        from actions.plain_reader import read_plain_language
        return read_plain_language({"raw_text": email_text}, response, player, session_memory)
        
    except Exception as e:
        return f"Failed to automate Gmail: {e}"
