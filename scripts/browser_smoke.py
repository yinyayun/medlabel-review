"""Run the real UI against an isolated temporary local database.

python -m playwright install chromium
python scripts/browser_smoke.py --screenshots docs/screenshots
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


def run(screenshots: Path):
    screenshots.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="medlabel-ui-") as data_dir:
        server = subprocess.Popen(
            [sys.executable, "run.py", "--port", str(port), "--data-dir", data_dir],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Temporary app server exited before startup")
                try:
                    opener.open(base + "/api/health", timeout=0.3)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Temporary app server did not start")
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1050}, locale="zh-CN")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.locator('[data-action="demo"]').first).to_be_visible()
                page.locator('[data-action="demo"]').first.click()
                expect(page.locator(".change-card").first).to_be_visible()
                expect(page.locator("#toasts > *")).to_have_count(0, timeout=10000)
                page.screenshot(path=str(screenshots / "comparison.png"))

                card = page.locator(".change-card").first
                card.locator("textarea").fill("已对照模拟原文，确认演示变更。")
                card.locator('[data-action="review"][data-status="confirmed"]').click()
                expect(page.locator(".change-card").first).to_contain_text("已确认")
                with page.expect_download() as download_info:
                    page.locator('a[href*="format=html"]').click()
                download = download_info.value
                assert download.suggested_filename.endswith(".html")
                report_path = screenshots / "review-report.html"
                download.save_as(str(report_path))
                assert "已对照模拟原文" in report_path.read_text(encoding="utf-8")

                page.locator('a[data-nav="dashboard"]').click()
                expect(page.locator("h1")).to_have_text("每一次变更，都清晰可见")
                expect(page.locator("#toasts > *")).to_have_count(0, timeout=10000)
                page.screenshot(path=str(screenshots / "dashboard.png"))
                page.locator('a[data-nav="drugs"]').click()
                page.locator('[data-action="new-drug"]').first.click()
                page.locator("#drug-name").fill("浏览器验收样例（模拟）")
                page.locator("#drug-manufacturer").fill("虚构测试资料")
                page.locator('#drug-form button[type="submit"]').click()
                expect(page.locator('[data-action="import-version"]').first).to_be_visible()
                for index, suffix in [(1, "txt"), (2, "docx")]:
                    if index == 1:
                        # Moving focus immediately after opening must survive
                        # any delayed initialization; otherwise rapid typing
                        # can silently land in the wrong input field.
                        focused = page.evaluate("""async () => {
                            document.querySelector('[data-action="import-version"]').click();
                            document.querySelector('#version-source').focus();
                            await new Promise(resolve => setTimeout(resolve, 50));
                            return document.activeElement.id;
                        }""")
                        assert focused == "version-source", "Dialog stole focus after the user moved to another field"
                    else:
                        page.locator('[data-action="import-version"]').first.click()
                    page.locator("#version-label").fill(f"浏览器模拟版 {index}")
                    page.locator("#version-date").fill(f"2026-0{index}-01")
                    page.locator("#version-source").fill("项目内置模拟文件")
                    expect(page.locator("#version-label")).to_have_value(f"浏览器模拟版 {index}")
                    expect(page.locator("#version-source")).to_have_value("项目内置模拟文件")
                    page.locator("#version-file").set_input_files(str(ROOT / f"examples/demo-v{index}.{suffix}"))
                    page.locator('#import-form button[type="submit"]').click()
                    expect(page.locator("#sections-form")).to_be_visible()
                    page.locator("#modal-close").click()
                    expect(page.locator(".version-card")).to_have_count(index)
                expect(page.locator("#toasts > *")).to_have_count(0, timeout=10000)
                page.screenshot(path=str(screenshots / "versions.png"))

                page.locator('[data-action="view-version"]').first.click()
                expect(page.locator("#section-editors .section-editor").first).to_be_visible()
                page.locator('textarea[name="section_text"]').first.fill("模拟示例，人工校正以核对来源。")
                page.locator('#sections-form button[type="submit"]').click()
                expect(page.locator("#modal")).to_contain_text("章节经人工校正")
                page.locator("#modal-close").click()
                page.locator('[data-action="new-comparison"]').click()
                page.locator('#comparison-form button[type="submit"]').click()
                expect(page.locator(".change-card").first).to_be_visible()

                page.locator('a[data-nav="audit"]').click()
                expect(page.locator("#audit-list")).to_contain_text("校正章节")
                expect(page.locator("#toasts > *")).to_have_count(0, timeout=10000)
                page.screenshot(path=str(screenshots / "audit.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator('a[data-nav="comparisons"]').click()
                expect(page.locator("#comparison-list")).to_be_visible()
                page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
                overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2")
                assert not overflow, "Mobile viewport has horizontal page overflow"
                assert not errors, errors
                browser.close()
                print("PASS: demo, review, HTML download, TXT/DOCX upload, chapter correction, comparison, audit, mobile, no JS errors")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path, default=ROOT / "test-results")
    args = parser.parse_args()
    run(args.screenshots.resolve())
