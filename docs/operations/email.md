# Email Channel

The email channel polls an IMAP mailbox for unread plain-text messages and sends
plain-text SMTP replies.

Attachments and HTML-only email are ignored.

## Configure

```yaml
channels:
  email:
    enabled: true
    smtp_host: smtp.example.com
    smtp_port: 587
    smtp_starttls: true
    smtp_username_env: DEMIURGE_SMTP_USERNAME
    smtp_password_env: DEMIURGE_SMTP_PASSWORD
    imap_host: imap.example.com
    imap_port: 993
    imap_username_env: DEMIURGE_IMAP_USERNAME
    imap_password_env: DEMIURGE_IMAP_PASSWORD
    from_address: demiurge@example.com
    allowed_senders:
      - alice@example.com
    allowed_recipients:
      - alice@example.com
    trust_from_headers: false
    poll_interval: 30
```

## Behavior

- Polls unread messages from the configured mailbox.
- Extracts the first non-attachment `text/plain` part.
- Marks accepted messages as seen after fetching them.
- Replies with `In-Reply-To` and `References` headers when available.

## Scheduled Delivery

```yaml
delivery:
  mode: email
  target: alice@example.com
```

If `allowed_recipients` is configured, the target must be listed there.

## Security

Email sender identity depends on mailbox trust and upstream mail authentication.
Demiurge treats RFC5322 `From` headers as spoofable by default; if you use
`allowed_senders`, set `trust_from_headers: true` only after your mailbox or mail
provider enforces SPF/DKIM/DMARC or another authenticated sender boundary. Keep
credentials in environment variables and avoid using this channel for high-risk
approval workflows.
