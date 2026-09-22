"""Check the built UI with an in-process API; no server or persistent replays."""

from pathlib import Path
import asyncio
import json
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import httpx
from playwright.sync_api import expect, sync_playwright

from backend.api.app import create_app
from backend.game import GameConfig
from backend.game.characters import with_characters
from backend.agents.deepseek import DeepSeekSettings
from backend.matches.live import record_live_match
from backend.replay.recording import record_match


async def teleport_demo(config):
    def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": "frost_teleport", "arguments": json.dumps({"position": 1.5, "decision_summary": "传送到对手身后。"})}}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
        return await record_live_match("counter", "deepseek", config.model_copy(update={"time_limit_ms": 500}),
            settings=DeepSeekSettings(api_key="test-only"), client=transport)


def main():
    output = Path(__file__).resolve().parents[1] / "artifacts/turns"
    output.mkdir(parents=True, exist_ok=True)
    config = with_characters(GameConfig(max_mana=30, time_limit_ms=15000),
                             {"p1": "crimson_blade", "p2": "frost_bell"})
    data = config.model_dump()
    for character in data["characters"].values():
        character["max_mana"] = 30
    data["characters"]["p1"].update(max_health=120, max_stamina=70)
    data["characters"]["p2"].update(max_health=80, max_stamina=130)
    config = GameConfig.model_validate(data)
    recording = record_match(config=config)
    demo = asyncio.run(teleport_demo(config))
    errors = []
    with TemporaryDirectory(prefix="arena-turns-", ignore_cleanup_errors=True) as temporary:
        app = create_app(Path(temporary))
        app.state.store.save(recording)
        with TestClient(app) as client, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                url = urlsplit(request.request.url)
                response = client.request(request.request.method, url.path,
                                          content=request.request.post_data_buffer,
                                          headers={"content-type": request.request.headers.get("content-type", "application/json")})
                request.fulfill(status=response.status_code, body=response.content,
                                content_type=response.headers.get("content-type", "application/octet-stream"))

            page.route("**/*", route)
            page.goto("http://arena.test")
            expect(page.locator("canvas")).to_be_visible(timeout=30000)
            expect(page.get_by_test_id("mana")).to_have_count(2)
            expect(page.get_by_test_id("mana").first).to_contain_text("30.0")
            expect(page.get_by_text("部位状态", exact=True)).to_have_count(0)
            expect(page.locator(".body-parts, .damage-marker")).to_have_count(0)
            mage = page.get_by_test_id("fighter-p2")
            bolt = mage.locator('[data-skill="jab"]')
            blade = page.get_by_test_id("fighter-p1")
            expect(blade.locator('.resource').nth(0)).to_contain_text('120.0 / 120')
            expect(blade.locator('.resource').nth(1)).to_contain_text('70.0 / 70')
            expect(mage.locator('.resource').nth(0)).to_contain_text('80.0 / 80')
            expect(mage.locator('.resource').nth(1)).to_contain_text('130.0 / 130')
            expect(page.locator('.skill-detail:visible')).to_have_count(0)
            expect(page.locator('.skill-item[title], .skill-row[title]')).to_have_count(0)
            bolt.locator('summary').hover()
            expect(bolt.locator('.skill-detail')).not_to_be_visible()
            for panel in (blade, mage):
                for item in panel.locator('.skill-item').all():
                    item.locator('summary').click()
                    expect(item.locator('.skill-detail')).to_be_visible()
                    expect(item.locator('.skill-detail')).to_contain_text('冷却')
                    item.locator('summary').click()
                    expect(item.locator('.skill-detail')).not_to_be_visible()
            bolt.locator('summary').focus()
            page.keyboard.press('Enter')
            expect(bolt.locator('.skill-detail')).to_contain_text("魔力消耗 16")
            expect(bolt.locator('.skill-detail')).to_contain_text("冷却 1 回合")
            page.keyboard.press('Enter')
            for panel, skill, text in [(blade, 'dash', '移动距离 1.75'), (blade, 'move', '移动距离 1'),
                                       (blade, 'rest', '恢复体力 15、魔力 20'), (mage, 'dash', '立即传送到指定位置')]:
                item = panel.locator(f'[data-skill="{skill}"]')
                item.locator('summary').click()
                expect(item.locator('.skill-detail')).to_contain_text(text)
                item.locator('summary').click()
            page.get_by_role("button", name="下一回合", exact=True).click()
            expect(page.get_by_test_id("game-time")).to_have_text("0.50")
            expect(page.locator(".arena-readout")).to_contain_text("回合 002")

            timeline = page.get_by_role("slider", name="对局时间轴")
            # Choose the last frame at each timestamp, matching the replay seek behavior.
            frames = {frame.simulation_time: frame for frame in recording.frames}
            low = next(frame for time, frame in frames.items() if time > 0 and
                       frame.tools["p2"].unavailable.get("jab") == "insufficient_mana")
            timeline.fill(str(low.simulation_time))
            expect(bolt).to_contain_text("魔力不足")
            bolt.locator('summary').click()
            expect(bolt.locator('.skill-detail')).to_be_visible()
            bolt.locator('summary').click()
            expect(mage.get_by_test_id("mana")).to_contain_text(f'{low.fighters["p2"].mana:.1f}')
            page.screenshot(path=str(output / "mana.png"), full_page=True)

            cooling = next(frame for time, frame in frames.items() if time > 0 and not frame.result
                           and frame.tools["p1"].unavailable.get("heavy_punch") == "cooldown")
            timeline.fill(str(cooling.simulation_time))
            heavy = page.get_by_test_id("fighter-p1").locator('[data-skill="heavy_punch"]')
            expect(heavy).to_contain_text(f'{cooling.fighters["p1"].cooldowns["heavy_punch"]} 回合')
            paused = heavy.inner_text()
            page.get_by_role("button", name="2×", exact=True).click()
            page.wait_for_timeout(150)
            assert heavy.inner_text() == paused
            page.screenshot(path=str(output / "cooldown.png"), full_page=True)
            for width in (768, 390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                blade.locator('[data-skill="rest"] summary').click()
                expect(blade.locator('[data-skill="rest"] .skill-detail')).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                assert page.locator('.resource, .skill-row').evaluate_all(
                    '(items) => items.every(i => i.scrollWidth <= i.clientWidth + 1)'), width
                page.screenshot(path=str(output / f"viewport-{width}.png"), full_page=True)
                blade.locator('[data-skill="rest"] summary').click()
            page.get_by_role("button", name="跳到结尾", exact=True).click()
            expect(page.locator(".result-overlay")).to_be_visible()
            custom = config.model_dump()
            custom["characters"]["p1"]["skills"]["move"]["speed"] = 6
            custom["characters"]["p1"]["skills"]["rest"].update(stamina_restore=9, mana_restore=7)
            created = client.post("/api/matches", json={"config": custom})
            assert created.status_code == 201
            custom_id = created.json()["replay_id"]
            page.reload()
            expect(page.locator('.skill-detail:visible')).to_have_count(0)
            blade.locator('[data-skill="move"] summary').click()
            expect(blade.locator('[data-skill="move"] .skill-detail')).to_contain_text("移动距离 3")
            blade.locator('[data-skill="rest"] summary').click()
            expect(blade.locator('[data-skill="rest"] .skill-detail')).to_contain_text("恢复体力 9、魔力 7")
            page.screenshot(path=str(output / "custom-skill-effects.png"), full_page=True)
            app.state.store.save(demo)
            page.reload()
            page.locator('.history-row').filter(has_text=demo.replay_id[:8]).click()
            expect(mage.locator('[data-skill="dash"] summary')).to_contain_text('传送')
            page.set_viewport_size({"width": 1440, "height": 1000})
            timeline.fill('0.1')
            expect(mage.locator('.current-action')).to_contain_text('传送 · 落点 1.5')
            expect(page.locator('.event-rows')).to_contain_text('9 → 1.5')
            canvas = page.locator('canvas')
            page.screenshot(path=str(output / 'teleport.png'), full_page=True)
            saved = canvas.screenshot()
            page.wait_for_timeout(150)
            assert canvas.screenshot() == saved, 'teleport effect advances while paused'
            timeline.fill('0')
            timeline.fill('0.1')
            assert canvas.screenshot() == saved, 'teleport effects differ after seeking'
            last_entry = page.locator('.history-entry').filter(has_text=recording.replay_id[:8])
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                last_entry.locator('.history-delete').click()
                expect(last_entry.locator('.history-confirm')).to_be_visible()
                for name in ('取消', '删除'):
                    button = last_entry.get_by_role('button', name=name, exact=True)
                    assert button.evaluate('''button => {
                        const item = button.getBoundingClientRect();
                        const list = button.closest('.history-list').getBoundingClientRect();
                        return item.top >= list.top - 1 && item.bottom <= list.bottom + 1;
                    }'''), (width, name)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                if width == 320:
                    page.screenshot(path=str(output / 'delete-last-row-mobile.png'), full_page=True)
                last_entry.get_by_role('button', name='取消').click()
            page.set_viewport_size({"width": 1440, "height": 1000})
            custom_entry = page.locator('.history-entry').filter(has_text=custom_id[:8])
            custom_entry.locator('.history-delete').click()
            expect(custom_entry.locator('.history-confirm')).to_be_visible()
            page.screenshot(path=str(output / 'delete-confirm-desktop.png'), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / 'delete-confirm-mobile.png'), full_page=True)
            custom_entry.get_by_role('button', name='取消').click()
            expect(custom_entry.locator('.history-confirm')).to_have_count(0)
            assert client.get(f'/api/replays/{custom_id}').status_code == 200
            custom_entry.locator('.history-delete').click()
            custom_entry.get_by_role('button', name='删除', exact=True).click()
            expect(custom_entry).to_have_count(0)
            assert client.get(f'/api/replays/{custom_id}').status_code == 404
            expect(page.locator('.history-row.selected')).to_contain_text(demo.replay_id[:8])
            demo_entry = page.locator('.history-entry').filter(has_text=demo.replay_id[:8])
            demo_entry.locator('.history-delete').click()
            demo_entry.get_by_role('button', name='删除', exact=True).click()
            expect(demo_entry).to_have_count(0)
            expect(page.locator('.history-row.selected')).to_contain_text(recording.replay_id[:8])
            remaining = page.locator('.history-entry').filter(has_text=recording.replay_id[:8])
            remaining.locator('.history-delete').click()
            remaining.get_by_role('button', name='删除', exact=True).click()
            expect(page.locator('.history-entry')).to_have_count(0)
            expect(page.locator('.initial-scene')).to_be_visible()
            assert client.get('/api/replays').json() == []
            assert not errors, errors
            browser.close()
    print("Mana, cooldowns, movement/recovery, replay deletion, seeking, pause and 4 viewports passed; no service was started.")


if __name__ == "__main__":
    main()
