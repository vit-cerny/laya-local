"""Run the browser-use framework with the local Laya decision engine.

Attaches to the Chrome already listening on the debug port (start it with
scripts/laya-up.ps1 or scripts/browser-sandbox.ps1), drives it with browser-use's own
loop, and makes every decision locally through Laya.

    uv run --env-file .env python examples/browser_use_laya.py \
        --url "https://example.com" --goal "Click the link that says Learn more"

The Laya model is loaded on first use (~35 s), then each decision is ~100 ms.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser_use import Agent  # noqa: E402
from browser_use.browser.session import BrowserSession  # noqa: E402

from browser_use_laya import LayaChatModel  # noqa: E402


async def run(url, goal, max_steps):
    session = BrowserSession(cdp_url="http://127.0.0.1:9222")
    initial_actions = [{"navigate": {"url": url}}] if url else None
    agent = Agent(task=goal, llm=LayaChatModel(), browser_session=session, initial_actions=initial_actions)
    try:
        history = await agent.run(max_steps=max_steps)
        print(f"\nsteps: {len(history.history) if history else 0}")
        print(f"final url: {await session.get_current_page_url()}")
        if history and history.history:
            last = history.history[-1]
            results = getattr(last, "result", None)
            print(f"last result: {[getattr(r, 'extracted_content', None) for r in results] if results else None}")
        return 0
    finally:
        await session.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="", help="page to open before the goal")
    parser.add_argument("--goal", required=True, help="one natural-language goal")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.goal, args.max_steps))


if __name__ == "__main__":
    raise SystemExit(main())
