"""
config.py — Centralized configuration for Invoice Automation
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GmailConfig:
    # Path to OAuth2 credentials JSON downloaded from Google Cloud Console
    credentials_file: str = "credentials.json"
    # Token cache (auto-generated after first login)
    token_file: str = "token.json"
    # Gmail label/query to watch for invoices
    search_query: str = "subject:(invoice OR bill OR счет) is:unread"
    # How often to poll Gmail (seconds)
    poll_interval: int = 30
    # Mark processed emails with this label (create it in Gmail first)
    processed_label: str = "QB-Processed"


@dataclass
class AnthropicConfig:
    # Set via environment variable: export ANTHROPIC_API_KEY=sk-ant-...
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 2000


@dataclass
class QuickBooksConfig:
    # Integration mode: "sdk" (Windows COM) or "iif" (file import)
    mode: str = "iif"

    # --- SDK mode (Windows only, requires QB Desktop running) ---
    company_file: str = ""          # e.g. "C:/Company/MyCompany.qbw"
    app_name: str = "InvoiceBot"
    qb_sdk_version: str = "16.0"    # Match your QB Desktop version

    # --- IIF mode (cross-platform fallback) ---
    iif_output_dir: str = "./iif_exports"
    # Default expense account when none can be determined
    default_expense_account: str = "Accounts Payable"
    # Default AP account
    ap_account: str = "Accounts Payable"


@dataclass
class AppConfig:
    gmail: GmailConfig = field(default_factory=GmailConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    quickbooks: QuickBooksConfig = field(default_factory=QuickBooksConfig)

    # Directory to save downloaded attachments temporarily
    attachments_dir: str = "./attachments"
    # Directory for logs
    log_dir: str = "./logs"
    # Stop after N errors in a row (0 = never stop)
    max_consecutive_errors: int = 5


# Singleton — import this everywhere
config = AppConfig()
