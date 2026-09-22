import json

from jev_search import run_search
from jev_security import redact
from jev_usage import aggregate, format_stats, read_ledger

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    MCPServer = None


if MCPServer is not None:
    server = MCPServer(
        name="jev",
        version="0.1.0",
        instructions=(
            "Jev is a real Chrome browser agent. Use jev_search whenever you need live web "
            "information, a page fetched or read, a form filled, or a result found that you "
            "cannot get from your own knowledge. Give it a URL and one natural-language goal; "
            "it picks the clicks and typing itself and returns the final URL plus the visible "
            "page text for you to work with."
        ),
    )

    @server.tool(
        description=(
            "Live web lookup: drives a real Chrome browser to a URL and returns the page's visible "
            "text. Use ONLY for current, real-world info you cannot know from training (prices, "
            "availability, live pages) - not for general knowledge, math, or code. ALWAYS pass url: "
            "the exact page to open (e.g. 'https://www.google.com/travel/flights?hl=en'). Omitting "
            "url runs a Google keyword search of your goal sentence - useless for sentence-like "
            "goals. Takes 15-60s; wait, do not retry. content is the page text capped at "
            "max_content_chars; full_content is the complete untruncated text, kept for you to "
            "analyze at the end. Every run is also saved as JSON under artifacts/jev_outputs/ "
            "(path in output_file). final_url "
            "is ground truth. status:'done' is the agent's claim, not proof of success. Every "
            "result ends with a cumulative totals counter (searches, total time, total cost); "
            "pass include_log=true to also get the recent search log."
        )
    )
    def jev_search(
        goal: str, url: str = "", max_content_chars: int = 6000, include_log: bool = False
    ) -> str:
        try:
            result = run_search(goal, url or None, max_content_chars, include_log)
        except Exception as error:
            result = {"status": "error", "goal": goal, "error": redact(f"{type(error).__name__}: {error}")}
        return json.dumps(result, indent=2, ensure_ascii=False)

    @server.tool(
        description=(
            "Usage/cost telemetry for the Jev browser agent. Call ONLY after a jev_search run, when "
            "you need to report or verify how many searches ran, how long they took, or their "
            "estimated cost. Do NOT call to perform a search or to answer the user's question - it "
            "returns no page content. Returns per-run totals: search count, total/average elapsed "
            "time, model decision count, browser actions, text-helper token totals, and estimated "
            "cost (only when price env vars are configured). Takes under a second."
        )
    )
    def jev_stats(limit: int = 10) -> str:
        rows = read_ledger()
        totals = aggregate(rows)
        recent = rows[-limit:] if limit and limit > 0 else []
        return json.dumps({"totals": totals, "recent": recent, "summary": format_stats(totals, recent)},
                          indent=2, ensure_ascii=False)

    @server.tool(
        description=(
            "Local typed decision engine (Laya, Apache-2.0): runs fully offline on this machine, "
            "~100ms per answer, no cloud call. Use it for routing, triage, moderation, guardrail "
            "checks and any classification of text or JSON state - including content you would "
            "rather not send to an API. state is a string or a JSON object as text. questions is "
            "either a JSON object mapping question_id to {type: choice|score|noul, criteria, "
            "instructions}, or a preset name in {triage, email, guard, moderation, router}. "
            "Choice answers include probabilities per option plus a calibrated confidence score."
        )
    )
    def laya_ask(state: str, questions: str = "") -> str:
        try:
            from laya_ask import ask, load_questions

            state_obj = json.loads(state) if state.lstrip().startswith(("{", "[")) else state
            if not questions:
                return json.dumps(
                    {"error": "no questions: pass a JSON questions object or a preset name"},
                    indent=2,
                )
            result = ask(state_obj, load_questions(questions))
        except (Exception, SystemExit) as error:
            result = {"error": redact(f"{type(error).__name__}: {error}")}
        return json.dumps(result, indent=2, ensure_ascii=False)

    @server.tool(
        description=(
            "Local Computer Use (Laya, offline): drives the Windows desktop - clicks, typing, "
            "scrolling in ONE window - from a natural-language goal. pywinauto reads the window's "
            "UI tree as text, Laya picks the operation and element locally, Win32 input executes. "
            "Window is the FOREGROUND window unless you pass window= (title substring). SAFETY: "
            "execute defaults to False, which only plans (recommended: plan first, then execute). "
            "Even with execute=True, destructive targets (delete/send/pay/confirm/install/system "
            "settings/close/...) are always blocked. CEF-rendered apps (Steam, most games) expose "
            "no UI tree and will return 'no actionable elements'. Takes seconds per step; the "
            "first call per process loads the model (~35s)."
        )
    )
    def jev_cu(goal: str, window: str = "", execute: bool = False, max_steps: int = 6) -> str:
        try:
            from laya_cu.jev_cu import run_goal

            lines = []
            result = run_goal(
                goal, window or None, execute, max_steps, allow_sensitive=False, log=lines.append
            )
        except (Exception, SystemExit) as error:
            return json.dumps(
                {"status": "error", "error": redact(f"{type(error).__name__}: {error}")},
                indent=2,
                ensure_ascii=False,
            )
        return json.dumps({**result, "log": lines}, indent=2, ensure_ascii=False)