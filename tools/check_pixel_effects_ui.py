"""Render real skill recordings offline; check pause/seek and save an effect sheet."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

from backend.agents.scripted import AGENTS
from backend.api.app import create_app
from backend.game import GameConfig
from backend.game.characters import with_characters
from backend.game.models import Action
from backend.replay.recording import record_match


class Once:
    def __init__(self, action):
        self.action = action

    def decide(self, observation):
        action, self.action = self.action, Action(skill="rest")
        return action


def recording_for(character, skill, mirrored=False):
    positions = (7, 3) if mirrored else (3, 7)
    if skill in ("heavy_punch", "kick") or (skill == "jab" and character == "crimson_blade"):
        positions = (6, 4.6) if mirrored else (4.6, 6)
    config = with_characters(GameConfig(starting_positions=positions, time_limit_ms=1500, decision_ms=50),
                             {"p1": character, "p2": "frost_bell" if character == "crimson_blade" else "crimson_blade"})
    action = Action(skill=skill, **({"position": 9.0} if character == "frost_bell" and skill == "dash"
                                  else {"direction": "forward"} if skill in ("move", "dash") else {}))
    with patch.dict(AGENTS, aggressive=lambda: Once(action), counter=lambda: Once(Action(skill="rest"))):
        recording = record_match(config=config)
    assert not any(event.status == "rejected" for event in recording.events)
    return recording


def main():
    output = Path(__file__).resolve().parents[1] / "artifacts/pixel-effects"
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    shots = []
    with TemporaryDirectory(prefix="arena-pixel-effects-") as temporary:
        app = create_app(Path(temporary))
        with TestClient(app) as client, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                url = urlsplit(request.request.url)
                response = client.request(request.request.method, url.path,
                    content=request.request.post_data_buffer,
                    headers={"content-type": request.request.headers.get("content-type", "application/json")})
                assert response.status_code < 400, (url.path, response.status_code)
                request.fulfill(status=response.status_code, body=response.content,
                                content_type=response.headers.get("content-type", "application/octet-stream"))

            page.route("**/*", route)
            for character in ("crimson_blade", "frost_bell"):
                for skill in ("jab", "heavy_punch", "kick", "guard", "dash", "rest"):
                    recording = recording_for(character, skill)
                    app.state.store.save(recording)
                    page.goto("http://arena.test")
                    expect(page.locator("canvas")).to_be_visible(timeout=30000)
                    expect(page.locator(".match-id")).to_contain_text(recording.replay_id[:8])
                    canvas = page.locator("canvas")
                    timeline = page.get_by_role("slider", name="对局时间轴")
                    spec = recording.config.characters["p1"].skills[skill]
                    time = .1 if spec.teleport else spec.windup_ms / 1000 + min(.1, spec.active_ms / 2000)
                    if character == "frost_bell" and skill == "jab":
                        time = .45  # Bolt is between the fighters, before its hit.

                    def seek(value):
                        timeline.fill(str(round(value, 2)))
                        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

                    seek(time)
                    filename = output / f"{character}-{skill}.png"
                    saved = canvas.screenshot(path=str(filename))
                    page.wait_for_timeout(80)
                    assert saved == canvas.screenshot(), (character, skill, "pause")
                    seek(0)
                    seek(time)
                    assert saved == canvas.screenshot(), (character, skill, "seek")
                    shots.append((spec.display_name, filename))
                    if skill in ("jab", "heavy_punch", "kick"):
                        # A short attack must retain its recovery afterglow reproducibly.
                        recovery = (spec.windup_ms + spec.active_ms) / 1000 + .05
                        seek(recovery)
                        saved = canvas.screenshot(path=str(output / f"{character}-{skill}-recovery.png"))
                        seek(0)
                        seek(recovery)
                        assert saved == canvas.screenshot(), (character, skill, "recovery seek")

            mirrored = recording_for("frost_bell", "jab", mirrored=True)
            app.state.store.save(mirrored)
            page.goto("http://arena.test")
            expect(page.locator("canvas")).to_be_visible()
            page.get_by_role("slider", name="对局时间轴").fill("0.45")
            page.locator("canvas").screenshot(path=str(output / "projectile-left.png"))
            hit = next(event for event in mirrored.events if event.status == "hit")
            page.get_by_role("slider", name="对局时间轴").fill(str(hit.simulation_time + .05))
            page.locator("canvas").screenshot(path=str(output / "ice-impact.png"))
            for width in (768, 390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                page.screenshot(path=str(output / f"mobile-{width}.png"), full_page=True)
            assert not errors, errors
            browser.close()

    # Paired rows compare each fighter's attack, defense, movement and recovery.
    sheet = Image.new("RGB", (1200, 6 * 270), "#262735")
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    for i, (name, filename) in enumerate(shots):
        col, row = i // 6, i % 6
        draw.text((col * 600 + 16, row * 270 + 7), name, font=font, fill="#f5deed")
        image = Image.open(filename).convert("RGB").resize((600, 240), Image.Resampling.NEAREST)
        sheet.paste(image, (col * 600, row * 270 + 30))
    sheet.save(output / "overview.png")
    print("12 skills, attack recovery, mirrored projectile, pause/seek and 4 viewports passed; no service started.")


if __name__ == "__main__":
    main()
