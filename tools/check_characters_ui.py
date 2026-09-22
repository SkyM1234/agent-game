"""Exercise character selection, animation, effects and replay on a running server."""

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "artifacts/characters"
    output.mkdir(parents=True, exist_ok=True)
    errors, missing = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: missing.append(response.url) if response.status >= 400 else None)
        page.goto(args.url)
        canvas = page.locator("canvas")
        expect(canvas).to_be_visible(timeout=30000)
        page.get_by_label("A-01 策略").select_option("aggressive")
        page.get_by_label("B-02 策略").select_option("counter")
        page.get_by_label("A-01 人物").select_option("crimson_blade")
        page.get_by_label("B-02 人物").select_option("frost_bell")
        with page.expect_response(lambda r: r.url.endswith('/api/matches') and r.request.method == 'POST') as response:
            page.get_by_role("button", name="新对局", exact=True).click()
        recording = response.value.json()
        assert response.value.status == 201
        expect(page.get_by_role("button", name="暂停", exact=True)).to_be_visible()
        page.get_by_role("button", name="暂停", exact=True).click()
        page.get_by_role("button", name="回到开头", exact=True).click()
        expect(page.get_by_test_id("fighter-p1").locator("h2")).to_have_text("绯刃")
        expect(page.get_by_test_id("fighter-p2").locator("h2")).to_have_text("霜铃")
        expect(page.get_by_test_id("fighter-p1")).to_contain_text("拔刀斩")
        expect(page.get_by_test_id("fighter-p2")).to_contain_text("冰弹")
        assert page.locator('img').evaluate_all('(items) => items.every(i => i.complete && i.naturalWidth > 0)')
        initial = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
        assert len(initial.getcolors(initial.width*initial.height)) > 100, "blank scene"
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.get_by_role("button", name="下一回合", exact=True).click()
        moved = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
        assert ImageChops.difference(initial, moved).getbbox(), "no animation or movement"
        page.get_by_role("button", name="播放", exact=True).click()
        page.wait_for_function("Number(document.querySelector('[data-testid=game-time]').textContent) > 0.9")
        page.get_by_role("button", name="暂停", exact=True).click()
        paused = canvas.screenshot()
        page.wait_for_timeout(200)
        assert paused == canvas.screenshot(), "animation advances while paused"

        timeline = page.get_by_role("slider", name="对局时间轴")

        def seek(time):
            timeline.fill(str(round(time, 2)))
            page.wait_for_timeout(80)

        projectile = next(e for e in recording["events"] if e["status"] == "projectile")
        seek(projectile["simulation_time"] + .1)
        page.screenshot(path=str(output / "projectile.png"), full_page=True)
        hit = next(e for e in recording["events"] if e["status"] == "hit")
        seek(hit["simulation_time"] + .05)
        page.screenshot(path=str(output / "hit.png"), full_page=True)
        saved = canvas.screenshot()
        seek(0)
        seek(hit["simulation_time"] + .05)
        assert saved == canvas.screenshot(), "seek does not reconstruct deterministic effects"

        with page.expect_download() as download:
            page.get_by_role("link", name="导出回放", exact=True).click()
        exported = output / "characters.jsonl"
        download.value.save_as(exported)
        with page.expect_response(lambda r: r.url.endswith('/api/replays/import')) as imported:
            page.locator('input[type=file]').set_input_files(exported)
        assert imported.value.status == 201
        expect(page.get_by_test_id("game-time")).to_have_text("0.00")
        expect(page.get_by_role("button", name="新对局", exact=True)).to_be_enabled()
        export_link = page.get_by_role("link", name="导出回放", exact=True).get_attribute('href')
        imported_recording = page.request.get(args.url + export_link.removesuffix('/export')).json()
        assert imported_recording["config"]["characters"] == recording["config"]["characters"]
        assert imported_recording["replay_id"] != recording["replay_id"]

        page.get_by_label("A-01 人物").select_option("frost_bell")
        page.get_by_label("B-02 人物").select_option("frost_bell")
        with page.expect_response(lambda r: r.url.endswith('/api/matches') and r.request.method == 'POST') as mirrored:
            page.get_by_role("button", name="新对局", exact=True).click()
        assert mirrored.value.json()["config"]["characters"]["p1"]["character_id"] == "frost_bell"
        expect(page.get_by_test_id("fighter-p1").locator("h2")).to_have_text("霜铃")
        page.get_by_role("button", name="暂停", exact=True).click()
        page.get_by_role("button", name="跳到结尾", exact=True).click()
        expect(page.locator('.result-overlay')).to_be_visible()
        page.screenshot(path=str(output / "result.png"), full_page=True)
        seek(0)
        for width in (768, 390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(100)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}"
            assert page.locator('select').evaluate_all('(items) => items.every(i => i.getBoundingClientRect().right <= innerWidth)')
            assert page.locator('.skill-row').evaluate_all('(items) => items.every(i => i.scrollWidth <= i.clientWidth + 1)')
            pixels = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            assert len(pixels.getcolors(pixels.width*pixels.height)) > 100
            page.screenshot(path=str(output / f"mobile-{width}.png"), full_page=True)
        assert not errors, errors
        assert not missing, missing
        browser.close()
    print("Character selection, mirror match, animation, pause, effects, replay roundtrip and 4 viewports passed.")


if __name__ == '__main__':
    main()
