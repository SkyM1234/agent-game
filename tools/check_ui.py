"""Browser acceptance checks. Run against a local server using the llm environment."""

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        canvas = page.locator("canvas")
        expect(canvas).to_be_visible(timeout=30000)
        expect(page.get_by_role("button", name="播放", exact=True)).to_be_enabled()
        page.wait_for_timeout(300)
        initial = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
        assert len(initial.getcolors(initial.width * initial.height) or []) > 20, "blank canvas"
        page.screenshot(path=str(artifacts / "desktop-initial.png"), full_page=True)

        page.get_by_role("button", name="下一回合", exact=True).click()
        expect(page.get_by_test_id("game-time")).to_have_text("0.50")
        stepped = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
        assert ImageChops.difference(initial, stepped).getbbox(), "fighters did not move"
        page.get_by_role("button", name="上一回合", exact=True).click()
        expect(page.get_by_test_id("game-time")).to_have_text("0.00")

        page.get_by_role("button", name="播放", exact=True).click()
        page.wait_for_function("Number(document.querySelector('[data-testid=game-time]').textContent) > 0.6")
        page.get_by_role("button", name="暂停", exact=True).click()
        paused_time = page.get_by_test_id("game-time").inner_text()
        page.wait_for_timeout(250)
        expect(page.get_by_test_id("game-time")).to_have_text(paused_time)
        page.get_by_role("button", name="2×", exact=True).click()
        expect(page.get_by_role("button", name="2×", exact=True)).to_have_attribute("aria-pressed", "true")

        expect(page.get_by_test_id("mana")).to_have_count(2)
        expect(page.locator(".body-parts, .damage-marker")).to_have_count(0)
        page.screenshot(path=str(artifacts / "desktop-resources.png"), full_page=True)
        page.get_by_role("button", name="跳到结尾", exact=True).click()
        expect(page.locator(".result-overlay")).to_be_visible()
        page.screenshot(path=str(artifacts / "desktop-result.png"), full_page=True)
        timeline = page.get_by_role("slider", name="对局时间轴")
        timeline.focus()
        timeline.press("Home")
        expect(page.get_by_test_id("game-time")).to_have_text("0.00")

        page.get_by_label("A-01 策略").select_option("counter")
        page.get_by_label("B-02 策略").select_option("aggressive")
        with page.expect_response(lambda response: response.url.endswith('/api/matches') and response.request.method == 'POST') as response:
            page.get_by_role("button", name="新对局", exact=True).click()
        assert response.value.status == 201
        expect(page.get_by_role("button", name="暂停", exact=True)).to_be_visible()
        page.get_by_role("button", name="暂停", exact=True).click()

        with page.expect_download() as download:
            page.get_by_role("link", name="导出回放", exact=True).click()
        exported = artifacts / "roundtrip.jsonl"
        download.value.save_as(exported)
        with page.expect_response(lambda response: response.url.endswith('/api/replays/import')) as response:
            page.locator('input[type="file"]').set_input_files(exported)
        assert response.value.status == 201
        expect(page.get_by_test_id("game-time")).to_have_text("0.00")
        expect(page.get_by_role("button", name="新对局", exact=True)).to_be_enabled()

        with page.expect_response(lambda response: response.url.endswith('/api/replays/import')) as response:
            page.locator('input[type="file"]').set_input_files({"name": "invalid.jsonl", "mimeType": "application/x-ndjson", "buffer": b"not-json"})
        assert response.value.status == 422
        expect(page.get_by_role("alert")).to_be_visible()
        page.get_by_role("button", name="关闭错误提示").click()
        page.locator(".history-row").last.click()
        expect(page.get_by_role("button", name="新对局", exact=True)).to_be_enabled()
        expect(page.get_by_test_id("game-time")).to_have_text("0.00")

        for width in (768, 390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"horizontal overflow at {width}"
            overflowing = page.locator('.skill-row, .resource, .transport, .match-settings, .fighter-heading').evaluate_all(
                "nodes => nodes.filter(node => node.scrollWidth > node.clientWidth + 1).map(node => node.className)"
            )
            assert not overflowing, f"content overflow at {width}: {overflowing}"
            pixels = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            assert len(pixels.getcolors(pixels.width * pixels.height) or []) > 20
            page.screenshot(path=str(artifacts / f"viewport-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "mobile-resources.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print(f"Browser checks passed. Screenshots and exported recording: {artifacts}")


if __name__ == "__main__":
    main()
