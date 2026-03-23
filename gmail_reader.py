"""
gmail_reader.py — Connects to Gmail via OAuth2, finds invoice emails,
downloads attachments (PDF/images), and extracts inline text.
"""

import base64
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import config
from logger import log

# Scopes needed: read mail + modify labels
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

SUPPORTED_ATTACHMENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
}


@dataclass
class EmailMessage:
    message_id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    body_text: str           # plain text body
    body_html: str           # HTML body (for fallback parsing)
    attachments: list = field(default_factory=list)   # list of local file paths


class GmailReader:
    def __init__(self):
        self.service = None
        self._processed_label_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self):
        """Run OAuth2 flow. Opens browser on first run, uses cached token after."""
        creds = None
        token_path = config.gmail.token_file
        creds_path = config.gmail.credentials_file

        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log.info("Refreshing Gmail OAuth token...")
                creds.refresh(Request())
            else:
                log.info("Starting Gmail OAuth2 flow — browser will open...")
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                creds = flow.run_local_server(port=0)

            with open(token_path, "w") as f:
                f.write(creds.to_json())
            log.info("Gmail token saved to %s", token_path)

        self.service = build("gmail", "v1", credentials=creds)
        log.info("Gmail authenticated successfully.")
        return self

    # ------------------------------------------------------------------
    # Label helpers
    # ------------------------------------------------------------------

    def _get_or_create_label(self, name: str) -> str:
        """Return label ID, creating it if it doesn't exist."""
        labels = self.service.users().labels().list(userId="me").execute()
        for lbl in labels.get("labels", []):
            if lbl["name"] == name:
                return lbl["id"]

        # Create label
        created = self.service.users().labels().create(
            userId="me",
            body={"name": name, "labelListVisibility": "labelShow",
                  "messageListVisibility": "show"}
        ).execute()
        log.info("Created Gmail label: %s (id=%s)", name, created["id"])
        return created["id"]

    def _mark_processed(self, message_id: str):
        """Add 'QB-Processed' label and mark as read."""
        if not self._processed_label_id:
            self._processed_label_id = self._get_or_create_label(
                config.gmail.processed_label
            )
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={
                "addLabelIds": [self._processed_label_id],
                "removeLabelIds": ["UNREAD"],
            }
        ).execute()

    # ------------------------------------------------------------------
    # Core fetching
    # ------------------------------------------------------------------

    def fetch_invoice_emails(self) -> list[EmailMessage]:
        """Search Gmail for unread invoice emails, return parsed EmailMessage objects."""
        if not self.service:
            raise RuntimeError("Call authenticate() first")

        log.info("Searching Gmail: %s", config.gmail.search_query)
        results = self.service.users().messages().list(
            userId="me",
            q=config.gmail.search_query,
            maxResults=50,
        ).execute()

        messages = results.get("messages", [])
        log.info("Found %d candidate email(s)", len(messages))

        emails = []
        for msg_ref in messages:
            try:
                email = self._parse_message(msg_ref["id"])
                if email:
                    emails.append(email)
            except Exception as exc:
                log.error("Failed to parse message %s: %s", msg_ref["id"], exc)

        return emails

    def _parse_message(self, message_id: str) -> Optional[EmailMessage]:
        """Fetch full message and extract body + attachments."""
        raw = self.service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in raw["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender  = headers.get("From", "")
        date    = headers.get("Date", "")

        log.info("Processing email: '%s' from %s", subject, sender)

        body_text, body_html = self._extract_body(raw["payload"])
        attachments = self._download_attachments(message_id, raw["payload"])

        # Skip emails with neither text nor attachments
        if not body_text and not attachments:
            log.warning("Email '%s' has no parseable content — skipping", subject)
            return None

        return EmailMessage(
            message_id=message_id,
            thread_id=raw["threadId"],
            subject=subject,
            sender=sender,
            date=date,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )

    # ------------------------------------------------------------------
    # Body extraction
    # ------------------------------------------------------------------

    def _extract_body(self, payload: dict) -> tuple[str, str]:
        """Recursively walk MIME parts, collect plain text and HTML."""
        text, html = "", ""

        def walk(part):
            nonlocal text, html
            mime = part.get("mimeType", "")
            data = part.get("body", {}).get("data", "")

            if data:
                decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                if mime == "text/plain":
                    text += decoded
                elif mime == "text/html":
                    html += decoded

            for subpart in part.get("parts", []):
                walk(subpart)

        walk(payload)
        return text.strip(), html.strip()

    # ------------------------------------------------------------------
    # Attachment download
    # ------------------------------------------------------------------

    def _download_attachments(self, message_id: str, payload: dict) -> list[str]:
        """Download supported attachments (PDF/images), return local paths."""
        os.makedirs(config.attachments_dir, exist_ok=True)
        paths = []

        def walk(part):
            mime = part.get("mimeType", "")
            filename = part.get("filename", "")
            att_id = part.get("body", {}).get("attachmentId")

            if att_id and mime in SUPPORTED_ATTACHMENT_TYPES:
                ext = SUPPORTED_ATTACHMENT_TYPES[mime]
                safe_name = re.sub(r"[^\w\-.]", "_", filename) or f"attachment{ext}"
                local_path = os.path.join(
                    config.attachments_dir, f"{message_id}_{safe_name}"
                )

                if not os.path.exists(local_path):
                    attachment = self.service.users().messages().attachments().get(
                        userId="me", messageId=message_id, id=att_id
                    ).execute()
                    data = base64.urlsafe_b64decode(attachment["data"] + "==")
                    with open(local_path, "wb") as f:
                        f.write(data)
                    log.info("Downloaded attachment: %s", local_path)

                paths.append(local_path)

            for subpart in part.get("parts", []):
                walk(subpart)

        walk(payload)
        return paths

    def mark_processed(self, email: EmailMessage):
        """Call after successful QB import to label the email."""
        try:
            self._mark_processed(email.message_id)
            log.info("Marked email '%s' as processed", email.subject)
        except Exception as exc:
            log.error("Failed to mark email as processed: %s", exc)
