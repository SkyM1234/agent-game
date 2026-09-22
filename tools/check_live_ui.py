"""Exercise the live UI with controlled WebSocket/model responses; no paid API calls."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from backend.agents.deepseek import DeepSeekSettings
from backend.game import GameConfig
from backend.matches.live import record_live_match


async def fixture():
    packets = []

    def provider(request):
        body = json.loads(request.content)
        observation = json.loads(body["messages"][1]["content"])
        name = "move" if observation["distance"] > 2 else "jab"
        tool = next(tool["function"] for tool in body["tools"] if tool["function"]["name"] == name)
        arguments = {key: spec["enum"][0] for key, spec in tool["parameters"]["properties"].items() if key != "decision_summary"}
        arguments["decision_summary"] = "接近对手，进入有效攻击距离。" if name == "move" else "使用当前可用的刺拳攻击对手躯干。"
        return httpx.Response(200, json={"model": "controlled-ui-test", "usage": {"total_tokens": 128},
            "choices": [{"message": {"tool_calls": [{"type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }}]}}]})

    async def collect(packet):
        packets.append(packet)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        recording = await record_live_match(config=GameConfig(time_limit_ms=1500),
            settings=DeepSeekSettings(api_key="local-test", model="controlled-ui-test"),
            client=client, on_update=collect)
    return packets, recording.model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    packets, recorded = asyncio.run(fixture())
    cycles = [packet for packet in packets if packet["type"] == "cycle"]
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    sockets = []
    acknowledgments = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/agents", lambda route: route.fulfill(json=[
            {"id": "aggressive", "label": "进攻型", "available": True},
            {"id": "counter", "label": "反击型", "available": True},
            {"id": "deepseek", "label": "DeepSeek Flash", "available": True},
        ]))

        def socket_route(socket):
            sockets.append(socket)

            def receive(raw):
                message = json.loads(raw)
                if "p1" in message:
                    socket.send(json.dumps(packets[0]))
                    socket.send(json.dumps(packets[1]))
                elif message["type"] == "next":
                    acknowledgments.append(message)
                    socket.send(json.dumps({"type": "waiting", "players": ["p1", "p2"]}))
                elif message["type"] == "cancel":
                    socket.send(json.dumps({"type": "cancelled"}))
                    socket.close()

            socket.on_message(receive)

        page.route_web_socket("**/api/live", socket_route)
        page.goto(args.url)
        expect(page.locator("canvas")).to_be_visible(timeout=30000)
        page.get_by_label("A-01 策略").select_option("deepseek")
        page.get_by_label("B-02 策略").select_option("deepseek")
        page.get_by_role("button", name="新对局", exact=True).click()
        expect(page.locator(".arena-heading")).to_contain_text("等待 绯刃 · A、霜铃 · B 决策")
        expect(page.get_by_role("slider", name="对局时间轴")).to_be_disabled()
        page.wait_for_timeout(250)
        expect(page.get_by_test_id("game-time")).to_have_text("0.00")
        page.screenshot(path=str(artifacts / "live-waiting-desktop.png"), full_page=True)
        sockets[-1].send(json.dumps(cycles[0]))
        expect(page.get_by_test_id("game-time")).to_have_text("0.50", timeout=5000)
        expect(page.locator(".decision-detail")).to_have_count(2)
        expect(page.locator(".arena-heading")).to_contain_text("等待")
        page.get_by_role("button", name="暂停", exact=True).click()
        page.wait_for_timeout(100)
        expect(page.get_by_test_id("game-time")).to_have_text("0.50")
        page.get_by_role("button", name="播放", exact=True).click()
        page.wait_for_timeout(150)
        expect(page.get_by_test_id("game-time")).to_have_text("0.50")
        assert len(acknowledgments) == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "live-waiting-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="取消对局").click()
        expect(page.locator(".arena-heading")).to_contain_text("对局已取消")
        expect(page.get_by_role("button", name="新对局", exact=True)).to_be_enabled()

        page.get_by_role("button", name="新对局", exact=True).click()
        expect(page.locator(".arena-heading")).to_contain_text("等待")
        for cycle in cycles:
            sockets[-1].send(json.dumps(cycle))
            expect(page.get_by_test_id("game-time")).to_have_text(f"{cycle['frames'][-1]['simulation_time']:.2f}", timeout=5000)
        sockets[-1].send(json.dumps({"type": "finished", "replay": recorded}))
        sockets[-1].close()
        expect(page.locator(".result-overlay")).to_be_visible()
        expect(page.get_by_role("link", name="导出回放")).to_be_visible()
        page.screenshot(path=str(artifacts / "live-finished-mobile.png"), full_page=True)

        page.get_by_role("button", name="新对局", exact=True).click()
        expect(page.locator(".arena-heading")).to_contain_text("等待")
        sockets[-1].send(json.dumps({"type": "error", "code": "authentication_failed", "message": "DeepSeek 鉴权失败，请检查密钥与账号权限"}))
        sockets[-1].close()
        expect(page.get_by_role("alert")).to_contain_text("鉴权失败")
        expect(page.get_by_role("button", name="新对局", exact=True)).to_be_enabled()
        assert not errors, errors
        browser.close()
    print("Controlled live UI checks passed; no external model calls were made.")


if __name__ == "__main__":
    main()
