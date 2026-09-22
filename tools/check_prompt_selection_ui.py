"""Check prompt selection against a temporary in-process API without model calls."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from backend.api.app import create_app


def main():
    output = Path(__file__).resolve().parents[1] / "artifacts/prompts"
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    live_requests = []
    with TemporaryDirectory(prefix="arena-prompts-", ignore_cleanup_errors=True) as temporary:
        with TestClient(create_app(Path(temporary))) as client, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                url = urlsplit(request.request.url)
                content = request.request.post_data_buffer
                if url.path == "/api/matches" and content:
                    body = json.loads(content)
                    body["config"] = {"time_limit_ms": 500}
                    content = json.dumps(body).encode()
                response = client.request(request.request.method, url.path, content=content,
                                          headers={"content-type": "application/json"})
                request.fulfill(status=response.status_code, body=response.content,
                                content_type=response.headers.get("content-type", "application/octet-stream"))

            def live_route(socket):
                def receive(raw):
                    body = json.loads(raw)
                    live_requests.append(body)
                    # Return a controlled started packet with the actual API's selected snapshot.
                    body.update(p1="test", p2="test", config={"time_limit_ms": 500})
                    replay = client.post("/api/matches", json=body).json()
                    replay["agents"]["p1"] = "counterfactual"
                    socket.send(json.dumps({"type": "started", "replay": replay}))
                socket.on_message(receive)

            page.route("**/*", route)
            page.route_web_socket("**/api/live", live_route)
            page.goto("http://arena.test")
            a = page.get_by_label("A-01 提示词", exact=True)
            b = page.get_by_label("B-02 提示词", exact=True)
            expect(a).to_be_enabled()
            expect(a).to_have_value("neutral")
            expect(b).to_have_value("neutral")
            with page.expect_response(lambda r: r.url.endswith('/api/preview') and
                                      r.request.post_data_json.get('prompt_variants', {}).get('p1') == 'aggressive') as preview:
                a.select_option("aggressive")
            assert preview.value.json()["config"]["characters"]["p1"]["agent"]["version"].endswith("-aggressive-v1")
            expect(b).to_have_value("neutral")
            for width in (1440, 768, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                assert page.locator('select').evaluate_all(
                    '(items) => items.every(i => i.getBoundingClientRect().right <= innerWidth)')
                page.screenshot(path=str(output / f"selection-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="开始对局", exact=True).click()
            expect(a).to_be_disabled()
            expect(a).to_have_value("aggressive")
            page.reload()
            expect(a).to_have_value("aggressive")
            expect(b).to_have_value("neutral")
            page.get_by_role("button", name="新对局", exact=True).click()
            a.select_option("neutral")
            b.select_option("aggressive")
            page.get_by_role("button", name="取消", exact=True).click()
            expect(a).to_have_value("aggressive")
            expect(b).to_have_value("neutral")
            page.get_by_role("button", name="新对局", exact=True).click()
            a.select_option("neutral")
            b.select_option("aggressive")
            page.get_by_label("A-01 策略", exact=True).select_option("counterfactual")
            page.get_by_role("button", name="开始对局", exact=True).click()
            expect(page.get_by_role("button", name="取消对局", exact=True)).to_be_visible()
            assert live_requests[0]["prompt_variants"] == {"p1": "neutral", "p2": "aggressive"}
            expect(a).to_be_disabled()
            expect(b).to_have_value("aggressive")
            assert not errors, errors
            browser.close()
    print("Prompt selection, preview, HTTP/WebSocket requests, replay restore, cancel and 4 viewports passed.")


if __name__ == "__main__":
    main()
