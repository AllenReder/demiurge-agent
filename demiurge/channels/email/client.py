from __future__ import annotations

import imaplib
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from typing import Iterable


@dataclass(slots=True)
class EmailInboundMessage:
    uid: str
    sender: str
    subject: str
    body: str
    message_id: str | None = None
    references: str | None = None


class EmailClient:
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str | None,
        smtp_password: str | None,
        smtp_starttls: bool,
        imap_host: str,
        imap_port: int,
        imap_username: str | None,
        imap_password: str | None,
        mailbox: str = "INBOX",
        from_address: str | None = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_starttls = smtp_starttls
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_username = imap_username
        self.imap_password = imap_password
        self.mailbox = mailbox
        self.from_address = from_address or smtp_username or imap_username

    def poll_unseen(self) -> list[EmailInboundMessage]:
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as client:
            if self.imap_username and self.imap_password:
                client.login(self.imap_username, self.imap_password)
            client.select(self.mailbox)
            status, data = client.uid("search", None, "UNSEEN")
            if status != "OK" or not data:
                return []
            uids = data[0].split()
            messages: list[EmailInboundMessage] = []
            for raw_uid in uids:
                uid = raw_uid.decode("ascii", errors="ignore")
                status, fetch_data = client.uid("fetch", raw_uid, "(RFC822)")
                if status != "OK" or not fetch_data:
                    continue
                raw_message = next((item[1] for item in fetch_data if isinstance(item, tuple) and len(item) > 1), None)
                if not isinstance(raw_message, bytes):
                    continue
                parsed = BytesParser(policy=default).parsebytes(raw_message)
                messages.append(
                    EmailInboundMessage(
                        uid=uid,
                        sender=str(parsed.get("From") or ""),
                        subject=str(parsed.get("Subject") or ""),
                        body=_plain_text(parsed),
                        message_id=str(parsed.get("Message-ID") or "") or None,
                        references=str(parsed.get("References") or "") or None,
                    )
                )
                client.uid("store", raw_uid, "+FLAGS", "(\\Seen)")
            return messages

    def send_text(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
        references: Iterable[str] | None = None,
    ) -> None:
        message = EmailMessage()
        if not self.from_address:
            raise RuntimeError("email channel requires from_address or SMTP username")
        message["From"] = self.from_address
        message["To"] = to_address
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        refs = [ref for ref in (references or []) if ref]
        if refs:
            message["References"] = " ".join(refs)
        message.set_content(body)
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
            if self.smtp_starttls:
                smtp.starttls()
            if self.smtp_username and self.smtp_password:
                smtp.login(self.smtp_username, self.smtp_password)
            smtp.send_message(message)


def _plain_text(message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition") or ""):
                return str(part.get_content() or "").strip()
        return ""
    if message.get_content_type() == "text/plain":
        return str(message.get_content() or "").strip()
    return ""
