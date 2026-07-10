"""
Standalone reproduction harness for three confirmed bugs in draft_gmail_message.
Calls the real, fully-decorated tool function directly (bypassing the MCP
transport layer) against a live Gmail account, using stored OAuth credentials.
Not part of the shipped package -- diagnostic script only.

Usage:
    python scripts/repro_bugs.py bug1   # reply-header crash
    python scripts/repro_bugs.py bug2   # threadId not forwarded
    python scripts/repro_bugs.py bug3   # signature line breaks lost
"""

import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

USER_EMAIL = "erik.holzhauer@purplewave.com"
TEST_THREAD_ID = "19ef1d2943b4ce56"

# Needed by google_auth token refresh path if the cached access token is
# expired (the stored credential file has a refresh_token + client_id/secret
# already embedded, but set these for parity with the real deployed config).
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


async def main():
    import gmail.gmail_tools as gmail_tools

    bug = sys.argv[1] if len(sys.argv) > 1 else "bug1"

    draft_fn = gmail_tools.draft_gmail_message

    if bug == "bug1":
        print("=== Bug 1 repro: thread_id set, no in_reply_to/references ===")
        try:
            result = await draft_fn(
                user_google_email=USER_EMAIL,
                subject="Bug1 repro - please ignore",
                body="Testing auto-derived reply headers.",
                to=USER_EMAIL,
                thread_id=TEST_THREAD_ID,
            )
            print("SUCCESS (no crash):", result)
        except Exception:
            print("CRASHED. Full traceback follows:\n")
            traceback.print_exc()

    elif bug == "bug2":
        print("=== Bug 2 repro: explicit in_reply_to/references, check threadId ===")
        # The test thread's messages are all trashed (see Bug 1 repro), so
        # _fetch_thread_reply_context can't auto-derive headers from it.
        # Pull the real RFC Message-ID headers directly and supply them
        # explicitly, which is exactly the scenario Bug 2 describes:
        # manually-supplied in_reply_to/references that come back correct
        # on the draft, but threadId never actually gets forwarded.
        real_message_ids = [
            "<CABs+Ajrxn2F7ubBN6zn_hCqRCvi9VirsxmZVDL0fyCUe1LwjOQ@mail.gmail.com>",
            "<CABs+AjqgPwfbBVQ55_hs7p1wrh+eB9o0Ui=oL1Qasf3F+FsrhQ@mail.gmail.com>",
            "<CABs+AjqADi4CjDodPKxiLGM7+jZxMrZtoh-YsWNX5377AN9ntA@mail.gmail.com>",
        ]
        in_reply_to = real_message_ids[-1]
        references = " ".join(real_message_ids)
        print("in_reply_to:", in_reply_to)
        print("references:", references)

        result = await draft_fn(
            user_google_email=USER_EMAIL,
            subject="Bug2 repro - please ignore",
            body="Testing threadId forwarding.",
            to=USER_EMAIL,
            thread_id=TEST_THREAD_ID,
            in_reply_to=in_reply_to,
            references=references,
        )
        print("draft_gmail_message result:", result)

        # Extract the draft ID and fetch it back to check the real threadId
        import re as _re

        m = _re.search(r"Draft ID:\s*(\S+)", result)
        if not m:
            print("Could not parse draft ID from result")
            return
        draft_id = m.group(1)
        service = await _get_service()
        draft = service.users().drafts().get(userId="me", id=draft_id).execute()
        actual_thread_id = draft.get("message", {}).get("threadId")
        print(f"\nRequested thread_id: {TEST_THREAD_ID}")
        print(f"Draft's actual threadId: {actual_thread_id}")
        print(
            "MATCH -- attached to original thread"
            if actual_thread_id == TEST_THREAD_ID
            else "MISMATCH -- draft landed in a NEW thread"
        )

    elif bug == "bug3":
        print("=== Bug 3 repro: signature line-break preservation ===")
        service = await _get_service()
        sig_html = await gmail_tools._get_send_as_signature_html(
            service, from_email=USER_EMAIL
        )
        print("Raw signature HTML:\n", repr(sig_html), "\n")
        converted = gmail_tools._html_to_text(sig_html)
        print("Converted plain text:\n", repr(converted))

        appended = gmail_tools._append_signature_to_body(
            "Thanks,\n-Erik", "plain", sig_html
        )
        print("\nFull appended body:\n", repr(appended))
        print("\n--- rendered ---\n")
        print(appended)

    else:
        print(f"Unknown bug id: {bug}")


async def _get_service():
    """Build a real gmail service using the stored credential file, mirroring
    what require_google_service does internally, for helper-level calls that
    aren't wrapped by the decorator."""
    from auth.credential_store import get_credential_store
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    store = get_credential_store()
    creds = store.get_credential(USER_EMAIL)
    if creds is None:
        raise RuntimeError(f"No stored credentials found for {USER_EMAIL}")
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            store.store_credential(USER_EMAIL, creds)
        else:
            raise RuntimeError("Credentials invalid and no refresh_token present")
    return build("gmail", "v1", credentials=creds)


if __name__ == "__main__":
    asyncio.run(main())
