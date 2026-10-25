"""Nova's brain: a thin wrapper around Gemini's tool-calling API (free tier, your own GEMINI_API_KEY in
.env). Conversation history is replayed as plain text every time -- we don't persist the function-call
plumbing between requests -- so each call here rebuilds its own short-lived tool-calling loop."""
import os

from ai import tools

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
MAX_HOPS = 4
HISTORY_TURNS = 12

SYSTEM = (
    "You are Nova, the AI built into the AI Sponsorship Matchmaker platform. You help innovators and "
    "sponsors: drafting pitches and messages, explaining a deal's history, running a quick calculation, "
    "and remembering what matters to this person across conversations. Speak plainly and briefly. Use "
    "your tools instead of guessing at data you don't have -- never invent numbers for a project or a "
    "deal. Anything that would message someone or post something publicly must go through draft_dm or "
    "draft_project_update so the person can approve it themselves; never claim you already sent or "
    "posted something unless a tool result said so. When you learn something durable and specific about "
    "this person, call remember.")


def available():
    return bool(os.getenv("GEMINI_API_KEY"))


def _client():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    from google import genai
    from google.genai import types
    # The SDK's defaults (up to 5 attempts, backoff up to 60s each) can leave a single reply hanging for
    # minutes when Google's free tier is under load. Fail faster and let the person retry or use Stop.
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=20_000, retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=3.0)))


def _run_loop(client, config, contents, ctx, hops):
    from google.genai import types
    try:
        for _ in range(hops):
            response = client.models.generate_content(model=MODEL, contents=contents, config=config)
            candidate = response.candidates[0]
            calls = [p.function_call for p in candidate.content.parts if getattr(p, "function_call", None)]
            if not calls:
                return response.text or "..."
            contents.append(candidate.content)
            parts = [types.Part.from_function_response(
                        name=call.name, response={"result": tools.dispatch(call.name, dict(call.args or {}), ctx)})
                     for call in calls]
            contents.append(types.Content(role="user", parts=parts))
        return "That took more steps than I'm allowed in one go -- try asking in a simpler way."
    except Exception as e:
        return f"Nova hit a snag talking to its model: {e}"


def stream_reply(db, user_id, history, user_text, memory_facts, image=None):
    """`history` is [{'role': 'user'|'model', 'body': str}, ...] already in order. `image` is an optional
    (bytes, mime_type) tuple. Yields {'type': 'token', 'text': ...} as words arrive, and once
    {'type': 'tool', 'names': [...]} whenever Nova pauses to use a tool."""
    client = _client()
    if client is None:
        yield {"type": "token", "text": ("Nova isn't connected yet -- ask whoever runs this site to add a "
                                         "free GEMINI_API_KEY (from Google AI Studio) to the .env file.")}
        return
    from google.genai import types
    memory_block = ""
    if memory_facts:
        memory_block = "\n\nWhat you already know about this person:\n" + "\n".join(f"- {f}" for f in memory_facts)
    contents = [types.Content(role=h["role"], parts=[types.Part(text=h["body"])]) for h in history]
    user_parts = [types.Part(text=user_text)] if user_text else []
    if image is not None:
        data, mime = image
        user_parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    contents.append(types.Content(role="user", parts=user_parts))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM + memory_block,
        tools=[types.Tool(function_declarations=tools.DECLARATIONS)])
    ctx = {"db": db, "user_id": user_id}
    try:
        for _ in range(MAX_HOPS):
            text_buf, call_parts = "", []
            for chunk in client.models.generate_content_stream(model=MODEL, contents=contents, config=config):
                if not chunk.candidates or not chunk.candidates[0].content:
                    continue
                for part in chunk.candidates[0].content.parts or []:
                    if getattr(part, "function_call", None):
                        call_parts.append(part)   # keep the original Part -- it carries a thought_signature
                    elif getattr(part, "text", None):          # newer models require when replayed back
                        text_buf += part.text
                        yield {"type": "token", "text": part.text}
            if not call_parts:
                return
            yield {"type": "tool", "names": [p.function_call.name for p in call_parts]}
            text_part = [types.Part(text=text_buf)] if text_buf else []
            contents.append(types.Content(role="model", parts=text_part + call_parts))
            result_parts = [types.Part.from_function_response(
                                name=p.function_call.name,
                                response={"result": tools.dispatch(p.function_call.name, dict(p.function_call.args or {}), ctx)})
                            for p in call_parts]
            contents.append(types.Content(role="user", parts=result_parts))
        yield {"type": "token", "text": "\n\n(That took more steps than I'm allowed in one go -- try asking more simply.)"}
    except Exception as e:
        yield {"type": "token", "text": f"\n\nNova hit a snag talking to its model: {e}"}


def ask_in_server(db, question, project_context=None, history_text=""):
    """A single-shot, read-only answer for an @nova mention or the /ask command, in a server channel or a
    DM: no memory, no draft/action tools, no sandbox -- just Nova's reasoning plus one read-only tool,
    since the reply is visible to everyone in the conversation. `history_text` is the recent conversation,
    as plain "name: message" lines, so Nova actually knows what's being talked about."""
    client = _client()
    if client is None:
        return "Nova isn't connected yet (no GEMINI_API_KEY set)."
    from google.genai import types
    extra = f" This question comes from inside the project server for: {project_context}." if project_context else ""
    if history_text:
        extra += f"\n\nThe recent conversation here (for context -- you're only being asked the latest " \
                 f"message, not every line of it):\n{history_text}"
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM + "\nYou are answering inside a shared chat, visible to everyone in "
        "it. Keep it short (a few sentences). You have no memory of this person and no drafting tools "
        "here, only get_platform_trends." + extra,
        tools=[types.Tool(function_declarations=[d for d in tools.DECLARATIONS if d["name"] == "get_platform_trends"])])
    contents = [types.Content(role="user", parts=[types.Part(text=question)])]
    text = _run_loop(client, config, contents, {"db": db, "user_id": None}, 3)
    return text[:1800]
