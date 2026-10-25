"""Capture real application screens for the registration manuals.

Uses only fictional built-in samples and an isolated temporary database.
"""
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/registration/assets"


def capture(browser_path=None):
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="medlabel-docs-") as directory:
        server = subprocess.Popen([sys.executable, "run.py", "--port", str(port), "--data-dir", directory],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                try:
                    opener.open(base + "/api/health", timeout=.3).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Application failed to start")
                    time.sleep(.1)
            else:
                raise RuntimeError("Application startup timed out")
            with sync_playwright() as p:
                browser = p.chromium.launch(**({"executable_path": browser_path} if browser_path else {}))
                page = browser.new_page(viewport={"width": 1280, "height": 940}, locale="zh-CN")
                page.goto(base)
                page.locator('[data-action="demo"]').first.click()
                expect(page.locator('.change-card').first).to_be_visible()

                def shot(name, selector=None):
                    expect(page.locator('#toasts > *')).to_have_count(0, timeout=12000)
                    target = page.locator(selector) if selector else page
                    target.screenshot(path=str(OUT / f'{name}.png'))

                def route(hash_value, selector):
                    page.goto(base + '#' + hash_value)
                    expect(page.locator(selector).first).to_be_visible()

                route('dashboard', '.activity')
                shot('01_dashboard')
                route('drugs', '#drug-list')
                shot('02_drugs')
                page.locator('[data-action="new-drug"]').first.click()
                page.locator('#drug-name').fill('示例药品 B（模拟）')
                page.locator('#drug-manufacturer').fill('虚构教学制药公司')
                page.locator('#drug-specification').fill('仅用于软件演示')
                page.locator('#drug-approval').fill('SIMULATED-NOT-APPROVED')
                shot('03_drug_form', '#modal')
                page.locator('#modal-close').click()
                route('drugs/1', '.version-card')
                shot('04_versions')
                page.locator('[data-action="import-version"]').first.click()
                page.locator('#version-label').fill('章节校对示例版')
                page.locator('#version-date').fill('2026-09-01')
                page.locator('#version-source').fill('项目内置模拟文件，不用于临床')
                page.locator('#version-file').set_input_files(str(ROOT / 'examples/demo-v1.txt'))
                shot('05_import_file', '#modal')
                page.locator('#import-form button[type="submit"]').click()
                expect(page.locator('#sections-form')).to_be_visible()
                shot('07_sections')
                page.locator('[data-action="version-tab"][data-value="raw"]').click()
                shot('08_raw_text', '#modal')
                page.locator('[data-action="version-tab"][data-value="source"]').click()
                shot('09_source', '#modal')
                page.locator('#modal-close').click()
                page.locator('[data-action="import-version"]').first.click()
                page.locator('#version-label').fill('粘贴文本示例版')
                page.locator('#version-date').fill('2026-09-02')
                page.locator('#version-source').fill('项目内置模拟文件，不用于临床')
                page.locator('[data-action="import-tab"][data-value="text"]').click()
                page.locator('#version-text').fill((ROOT / 'examples/demo-v2.txt').read_text(encoding='utf-8'))
                shot('06_import_text', '#modal')
                page.locator('#modal-close').click()
                page.locator('[data-action="new-comparison"]').click()
                page.locator('#old-version').select_option('1')
                page.locator('#new-version').select_option('2')
                shot('10_compare_form', '#modal')
                page.locator('#modal-close').click()
                route('comparisons/1', '.change-card')
                shot('11_comparison')
                card = page.locator('.change-card').first
                card.locator('textarea').fill('已对照模拟原文，确认数值字段由 10 mg 变为 15 mg；仅为演示数据。')
                card.locator('[data-action="review"][data-status="confirmed"]').click()
                expect(page.locator('.change-card').first).to_contain_text('已确认')
                shot('12_reviewed', '.change-card:first-of-type')
                with page.expect_download() as download:
                    page.locator('a[href*="format=html"]').click()
                report = OUT / 'sample-review-report.html'
                download.value.save_as(str(report))
                report_page = browser.new_page(viewport={"width": 1280, "height": 940})
                report_page.set_content(report.read_text(encoding='utf-8'), wait_until='load')
                report_page.screenshot(path=str(OUT / '13_report.png'))
                report_page.close()
                route('audit', '#audit-list')
                shot('14_audit')
                browser.close()
                print('Captured 14 real application screenshots from isolated sample data.')
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser-path')
    capture(parser.parse_args().browser_path)
