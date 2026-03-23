"""
pipeline.py — Main orchestrator: Gmail → Parse → QuickBooks

Run once:    python pipeline.py --once
Run daemon:  python pipeline.py --watch
"""

import argparse
import os
import sys
import time
import json
from datetime import datetime

from config import config
from gmail_reader import GmailReader
from invoice_parser import InvoiceParser, InvoiceData
from qb_iif_exporter import QBIIFExporter
from logger import log


class Pipeline:
    def __init__(self):
        self.gmail    = GmailReader()
        self.parser   = InvoiceParser()
        self.iif      = QBIIFExporter()

        # Lazy-import QB SDK connector (Windows only)
        self.qb_sdk = None
        if config.quickbooks.mode == "sdk":
            try:
                from qb_connector import QBConnector
                self.qb_sdk = QBConnector()
            except Exception as exc:
                log.warning("QB SDK unavailable (%s) — falling back to IIF mode", exc)
                config.quickbooks.mode = "iif"

        self.consecutive_errors = 0
        self.stats = {"processed": 0, "failed": 0, "skipped": 0}

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def run_once(self):
        """Process all currently matching emails, then exit."""
        log.info("=== Invoice Automation — Single Run ===")
        self._authenticate()
        emails = self.gmail.fetch_invoice_emails()

        if not emails:
            log.info("No unread invoice emails found.")
            return

        for email in emails:
            self._process_email(email)

        self._print_summary()

    def run_watch(self):
        """Continuously poll Gmail every N seconds."""
        log.info("=== Invoice Automation — Watch Mode (poll every %ds) ===",
                 config.gmail.poll_interval)
        self._authenticate()

        while True:
            try:
                emails = self.gmail.fetch_invoice_emails()
                for email in emails:
                    self._process_email(email)

                self.consecutive_errors = 0

            except KeyboardInterrupt:
                log.info("Stopped by user (Ctrl+C)")
                break

            except Exception as exc:
                self.consecutive_errors += 1
                log.error("Pipeline error #%d: %s", self.consecutive_errors, exc)

                if (config.max_consecutive_errors > 0 and
                        self.consecutive_errors >= config.max_consecutive_errors):
                    log.critical("Too many consecutive errors — stopping.")
                    sys.exit(1)

            log.debug("Sleeping %ds before next poll...", config.gmail.poll_interval)
            time.sleep(config.gmail.poll_interval)

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    def _authenticate(self):
        """Authenticate Gmail and (optionally) QB SDK."""
        self.gmail.authenticate()
        if self.qb_sdk:
            if not self.qb_sdk.connect():
                log.warning("QB SDK connection failed — using IIF mode instead")
                self.qb_sdk = None
                config.quickbooks.mode = "iif"

    def _process_email(self, email):
        """Full pipeline for one email: parse → validate → export → mark."""
        log.info("--- Processing: '%s' ---", email.subject)

        # 1. Parse invoice
        invoice = self.parser.parse(
            email_text=email.body_text,
            attachment_paths=email.attachments,
        )

        if not invoice:
            log.error("Failed to extract invoice from email: '%s'", email.subject)
            self.stats["failed"] += 1
            return

        # 2. Validate
        if not self._validate(invoice):
            self.stats["skipped"] += 1
            return

        # 3. Export to QuickBooks
        success = self._export_to_qb(invoice)

        # 4. Mark email as processed (only if QB import succeeded)
        if success:
            self.gmail.mark_processed(email)
            self.stats["processed"] += 1
            self._save_audit_record(email, invoice)
        else:
            self.stats["failed"] += 1

    def _validate(self, invoice: InvoiceData) -> bool:
        """Basic validation checks before sending to QB."""
        if invoice.total_amount <= 0:
            log.warning("Invoice #%s has zero/negative total (%.2f) — skipping",
                        invoice.invoice_number, invoice.total_amount)
            return False

        if invoice.invoice_number == "UNKNOWN":
            log.warning("Invoice has no number and confidence=%s — skipping",
                        invoice.confidence)
            return False

        if invoice.confidence == "low":
            log.warning("Low confidence parse for invoice #%s — flagging for review",
                        invoice.invoice_number)
            # Still proceed, but log clearly

        return True

    def _export_to_qb(self, invoice: InvoiceData) -> bool:
        """Route to SDK or IIF based on config."""
        if config.quickbooks.mode == "sdk" and self.qb_sdk:
            return self.qb_sdk.import_invoice(invoice)
        else:
            # IIF mode
            try:
                iif_path = self.iif.export(invoice)
                log.info(
                    "IIF file ready for QB import: %s\n"
                    "  → In QuickBooks: File > Utilities > Import > IIF Files",
                    iif_path
                )
                return True
            except Exception as exc:
                log.error("IIF export failed: %s", exc)
                return False

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def _save_audit_record(self, email, invoice: InvoiceData):
        """Save JSON audit record for every processed invoice."""
        audit_dir = os.path.join(config.log_dir, "audit")
        os.makedirs(audit_dir, exist_ok=True)

        record = {
            "timestamp": datetime.now().isoformat(),
            "email_subject": email.subject,
            "email_from": email.sender,
            "email_date": email.date,
            "invoice": invoice.to_dict(),
            "mode": config.quickbooks.mode,
        }

        filename = f"audit_{invoice.invoice_number.replace('/', '-')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = os.path.join(audit_dir, filename)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

        log.debug("Audit record saved: %s", path)

    def _print_summary(self):
        log.info(
            "=== Run complete: %d processed, %d failed, %d skipped ===",
            self.stats["processed"], self.stats["failed"], self.stats["skipped"]
        )


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Invoice Automation: Gmail → QuickBooks"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process current emails and exit (default)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously watch Gmail for new invoices",
    )
    parser.add_argument(
        "--mode",
        choices=["sdk", "iif"],
        default=None,
        help="QuickBooks integration mode (overrides config)",
    )
    args = parser.parse_args()

    if args.mode:
        config.quickbooks.mode = args.mode

    pipeline = Pipeline()

    if args.watch:
        pipeline.run_watch()
    else:
        pipeline.run_once()


if __name__ == "__main__":
    main()
