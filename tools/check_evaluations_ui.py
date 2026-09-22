"""Check evaluation reports, replay navigation and mobile layout without a server or paid calls."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from backend.api.app import create_app
from backend.evaluation.runner import default_suite, run_evaluation
from backend.evaluation.store import EvaluationStore


def main():
    output = Path(__file__).resolve().parents[1] / "artifacts/evaluations"
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="arena-evaluations-") as temporary:
        data = Path(temporary)
        suite = default_suite(500)
        suite = suite.model_copy(update={"scenarios": [suite.scenarios[0].model_copy(update={"name": "界面验收短局"})]})
        baseline = asyncio.run(run_evaluation(EvaluationStore(data / "evaluations"),
            suite,
            ["heuristic", "counterfactual"]))
        report = asyncio.run(run_evaluation(EvaluationStore(data / "evaluations"),
            suite,
            ["heuristic", "counterfactual"]))
        app = create_app(data)
        errors = []
        with TestClient(app) as client, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                url = urlsplit(request.request.url)
                response = client.request(request.request.method, url.path,
                    content=request.request.post_data_buffer,
                    headers={"content-type": request.request.headers.get("content-type", "application/json")})
                request.fulfill(status=response.status_code, body=response.content,
                                headers={key: value for key, value in response.headers.items()
                                         if key == "content-disposition"},
                                content_type=response.headers.get("content-type", "application/octet-stream"))

            page.route("**/*", route)
            page.route("**/api/evaluations", lambda route: route.fulfill(
                status=200, body="[]", content_type="application/json"), times=1)
            page.goto("http://arena.test")
            page.get_by_role("button", name="评测结果", exact=True).click()
            expect(page.get_by_text("暂无评测报告", exact=True)).to_be_visible()
            page.get_by_role("button", name="刷新报告").click()
            expect(page.locator(".evaluation-page > .evaluation-table-wrap tbody tr")).to_have_count(2)
            expect(page.locator(".evaluation-matches article")).to_have_count(4)
            analysis = page.get_by_role("region", name="行为统计，可横向滚动", exact=True)
            expect(analysis.locator("tbody tr")).to_have_count(2)
            expect(analysis).to_contain_text("4 / 4 场")
            expect(page.get_by_role("region", name="历史指标对比，可横向滚动", exact=True)).to_be_visible()
            page.get_by_label("统计维度").select_option("role")
            expect(analysis.locator("tbody tr")).to_have_count(4)
            expect(analysis).to_contain_text("绯刃")
            expect(page.get_by_text("动作分布", exact=True)).to_have_count(0)
            page.get_by_text("双方交手结果", exact=True).click()
            matchups = page.get_by_role("region", name="双方交手结果，可横向滚动", exact=True)
            expect(matchups.locator("tbody tr")).to_have_count(4)
            expect(matchups.locator("tbody tr").first.locator("th")).to_contain_text("绯刃")
            expect(matchups.locator("tbody tr").first.locator("td").first).to_contain_text("霜铃")
            page.screenshot(path=str(output / "desktop.png"), full_page=True)
            page.get_by_text("实验配置与统计口径", exact=True).click()
            expect(page.get_by_text("界面验收短局：0.5s", exact=False)).to_be_visible()
            # Chromium's download manager bypasses this page's in-process routing.
            # Verify the visible link and attachment through the same local API.
            export_url = f"/api/evaluations/{report['run_id']}/export"
            expect(page.get_by_role("link", name="下载完整报告与配置快照")).to_have_attribute("href", export_url)
            exported = client.get(export_url)
            assert exported.json() == report
            assert "evaluation-" in exported.headers["content-disposition"]
            page.get_by_role("button", name="查看回放 #1", exact=True).click()
            expect(page.locator("canvas")).to_be_visible(timeout=30000)
            expect(page.get_by_text("评测回放", exact=True)).to_be_visible()
            expect(page.get_by_role("link", name="导出回放")).to_have_attribute(
                "href", f"/api/evaluations/{report['run_id']}/replays/{report['matches'][0]['replay_id']}/export")
            page.get_by_role("button", name="评测结果", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator(".evaluation-page > .evaluation-table-wrap tbody tr")).to_have_count(2)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
            page.get_by_role("button", name="删除报告", exact=True).click()
            expect(page.get_by_role("alertdialog")).to_contain_text("全部关联回放将永久删除")
            page.get_by_role("button", name="取消删除", exact=True).click()
            expect(page.get_by_role("alertdialog")).to_have_count(0)
            assert client.get(export_url).status_code == 200
            page.route(f"**/api/evaluations/{report['run_id']}", lambda route: route.fulfill(
                status=500, body='{"detail":"delete failed for UI check"}', content_type="application/json"), times=1)
            page.get_by_role("button", name="删除报告", exact=True).click()
            page.get_by_role("button", name="确认删除", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("delete failed for UI check")
            expect(page.locator(".evaluation-page > .evaluation-table-wrap tbody tr")).to_have_count(2)
            page.get_by_role("button", name="确认删除", exact=True).click()
            expect(page.locator(".evaluation-page > .evaluation-picker select")).to_have_value(baseline["run_id"])
            assert client.get(export_url).status_code == 404
            assert not (data / "evaluations" / report["run_id"]).exists()
            expect(page.locator(".evaluation-matches article")).to_have_count(4)
            expect(page.get_by_role("region", name="历史指标对比，可横向滚动", exact=True)).to_have_count(0)
            page.get_by_role("button", name="删除报告", exact=True).click()
            page.get_by_role("button", name="确认删除", exact=True).click()
            expect(page.get_by_text("暂无评测报告", exact=True)).to_be_visible()
            assert client.get(f"/api/evaluations/{baseline['run_id']}").status_code == 404
            page.get_by_role("button", name="对局观战", exact=True).click()
            expect(page.get_by_role("link", name="导出回放")).to_have_count(0)
            page.get_by_role("button", name="评测结果", exact=True).click()
            page.route("**/api/evaluations", lambda route: route.fulfill(status=503, body="{}", content_type="application/json"))
            page.get_by_role("button", name="刷新报告").click()
            expect(page.get_by_role("alert")).to_contain_text("503")
            assert not errors, errors
            browser.close()
    print("Evaluation UI passed: reports, export, replay, mobile, delete/cancel/retry, empty and error states.")


if __name__ == "__main__":
    main()
