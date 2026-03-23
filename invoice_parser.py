"""
invoice_parser.py — Uses Claude AI (vision + text) to extract structured
invoice data from PDFs, images, and plain email text.

Works with ANY invoice layout — no hardcoded templates needed.
"""

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic
import pdfplumber

from config import config
from logger import log


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------

@dataclass
class LineItem:
    description: str
    quantity: float
    unit_price: float
    amount: float
    account: str = ""        # QB expense account (may be inferred)
    item_code: str = ""      # Vendor's part/SKU number


@dataclass
class InvoiceData:
    # Core identifiers
    invoice_number: str
    vendor_name: str
    vendor_address: str
    vendor_email: str
    vendor_phone: str

    # Dates
    invoice_date: str        # ISO format: YYYY-MM-DD
    due_date: str

    # Amounts
    subtotal: float
    tax_amount: float
    discount_amount: float
    total_amount: float

    # Payment terms
    payment_terms: str       # e.g. "Net 30"

    # Line items
    line_items: list = field(default_factory=list)  # list[LineItem]

    # Source tracking
    source_file: str = ""
    raw_text: str = ""
    confidence: str = "high"   # high / medium / low
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "invoice_number": self.invoice_number,
            "vendor_name": self.vendor_name,
            "vendor_address": self.vendor_address,
            "vendor_email": self.vendor_email,
            "vendor_phone": self.vendor_phone,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "subtotal": self.subtotal,
            "tax_amount": self.tax_amount,
            "discount_amount": self.discount_amount,
            "total_amount": self.total_amount,
            "payment_terms": self.payment_terms,
            "line_items": [
                {
                    "description": li.description,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                    "amount": li.amount,
                    "account": li.account,
                    "item_code": li.item_code,
                }
                for li in self.line_items
            ],
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are an expert accounting data extractor specializing in vendor invoices.
Your job is to extract ALL relevant fields from invoice documents with perfect accuracy.
You MUST respond with ONLY valid JSON — no markdown, no explanation, no backticks.

Extract these fields:
{
  "invoice_number": "string (required)",
  "vendor_name": "string (required)",
  "vendor_address": "string",
  "vendor_email": "string",
  "vendor_phone": "string",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD",
  "subtotal": number,
  "tax_amount": number,
  "discount_amount": number,
  "total_amount": number (required),
  "payment_terms": "string e.g. Net 30",
  "line_items": [
    {
      "description": "string",
      "quantity": number,
      "unit_price": number,
      "amount": number,
      "item_code": "string or empty"
    }
  ],
  "confidence": "high|medium|low",
  "warnings": ["list any unclear or missing fields"]
}

Rules:
- All monetary values must be numbers (float), never strings
- If a field is missing, use null for strings and 0 for numbers
- invoice_number and total_amount are REQUIRED — if missing, set confidence to "low"
- For dates, convert any format (Jan 5 2024, 05/01/2024) to YYYY-MM-DD
- line_items must be an array even if only one item
- Subtotal + tax - discount should equal total_amount (flag in warnings if not)
"""


# ------------------------------------------------------------------
# Main parser class
# ------------------------------------------------------------------

class InvoiceParser:
    def __init__(self):
        if not config.anthropic.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. "
                "Run: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        self.client = anthropic.Anthropic(api_key=config.anthropic.api_key)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self, email_text: str = "", attachment_paths: list = None) -> Optional[InvoiceData]:
        """
        Main entry point. Tries attachments first, falls back to email body.
        Returns None if parsing fails completely.
        """
        attachment_paths = attachment_paths or []

        # Try each attachment
        for path in attachment_paths:
            log.info("Parsing attachment: %s", path)
            try:
                invoice = self._parse_file(path)
                if invoice and invoice.invoice_number:
                    return invoice
            except Exception as exc:
                log.warning("Failed to parse %s: %s", path, exc)

        # Fallback: parse email body text
        if email_text:
            log.info("Parsing email body text as invoice")
            return self._parse_text(email_text)

        log.error("No parseable content found")
        return None

    # ------------------------------------------------------------------
    # File dispatch
    # ------------------------------------------------------------------

    def _parse_file(self, path: str) -> Optional[InvoiceData]:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return self._parse_pdf(path)
        elif ext in (".jpg", ".jpeg", ".png", ".tiff"):
            return self._parse_image(path)
        else:
            log.warning("Unsupported file type: %s", ext)
            return None

    # ------------------------------------------------------------------
    # PDF parsing
    # ------------------------------------------------------------------

    def _parse_pdf(self, path: str) -> Optional[InvoiceData]:
        """Extract text from PDF, send to Claude. If text-poor, use vision."""
        text = self._extract_pdf_text(path)

        if len(text.strip()) > 100:
            # Good text extraction — use text API (cheaper, faster)
            log.debug("PDF has readable text (%d chars), using text API", len(text))
            return self._call_claude_text(
                f"Extract all invoice data from this invoice document:\n\n{text}",
                source_file=path,
                raw_text=text,
            )
        else:
            # Scanned/image PDF — use vision
            log.debug("PDF is image-based, using vision API")
            return self._parse_image(path)

    def _extract_pdf_text(self, path: str) -> str:
        """Extract text from all pages of a PDF."""
        pages_text = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
        return "\n\n".join(pages_text)

    # ------------------------------------------------------------------
    # Image parsing (vision)
    # ------------------------------------------------------------------

    def _parse_image(self, path: str) -> Optional[InvoiceData]:
        """Send image to Claude vision API."""
        ext = os.path.splitext(path)[1].lower()
        media_map = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".tiff": "image/tiff",
        }
        media_type = media_map.get(ext, "application/octet-stream")

        with open(path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        log.debug("Sending %s to Claude vision API", path)

        response = self.client.messages.create(
            model=config.anthropic.model,
            max_tokens=config.anthropic.max_tokens,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all invoice data from this image. Return ONLY JSON.",
                        },
                    ],
                }
            ],
        )

        raw_json = response.content[0].text
        return self._build_invoice(raw_json, source_file=path)

    # ------------------------------------------------------------------
    # Plain text parsing
    # ------------------------------------------------------------------

    def _parse_text(self, text: str, source_file: str = "") -> Optional[InvoiceData]:
        return self._call_claude_text(
            f"Extract all invoice data from this email/text:\n\n{text}",
            source_file=source_file,
            raw_text=text,
        )

    def _call_claude_text(self, prompt: str, source_file: str = "", raw_text: str = "") -> Optional[InvoiceData]:
        response = self.client.messages.create(
            model=config.anthropic.model,
            max_tokens=config.anthropic.max_tokens,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_json = response.content[0].text
        return self._build_invoice(raw_json, source_file=source_file, raw_text=raw_text)

    # ------------------------------------------------------------------
    # JSON → InvoiceData
    # ------------------------------------------------------------------

    def _build_invoice(self, raw_json: str, source_file: str = "", raw_text: str = "") -> Optional[InvoiceData]:
        """Parse Claude's JSON response into InvoiceData."""
        # Strip markdown fences if Claude added them
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_json.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            log.error("Claude returned invalid JSON: %s\nRaw: %s", exc, raw_json[:500])
            return None

        # Build line items
        line_items = []
        for li in data.get("line_items") or []:
            line_items.append(LineItem(
                description=str(li.get("description") or ""),
                quantity=float(li.get("quantity") or 1),
                unit_price=float(li.get("unit_price") or 0),
                amount=float(li.get("amount") or 0),
                item_code=str(li.get("item_code") or ""),
            ))

        invoice = InvoiceData(
            invoice_number=str(data.get("invoice_number") or "UNKNOWN"),
            vendor_name=str(data.get("vendor_name") or "Unknown Vendor"),
            vendor_address=str(data.get("vendor_address") or ""),
            vendor_email=str(data.get("vendor_email") or ""),
            vendor_phone=str(data.get("vendor_phone") or ""),
            invoice_date=self._safe_date(data.get("invoice_date")),
            due_date=self._safe_date(data.get("due_date")),
            subtotal=float(data.get("subtotal") or 0),
            tax_amount=float(data.get("tax_amount") or 0),
            discount_amount=float(data.get("discount_amount") or 0),
            total_amount=float(data.get("total_amount") or 0),
            payment_terms=str(data.get("payment_terms") or ""),
            line_items=line_items,
            source_file=source_file,
            raw_text=raw_text,
            confidence=data.get("confidence", "medium"),
            warnings=data.get("warnings") or [],
        )

        log.info(
            "Parsed invoice: #%s | Vendor: %s | Total: $%.2f | Lines: %d | Confidence: %s",
            invoice.invoice_number,
            invoice.vendor_name,
            invoice.total_amount,
            len(invoice.line_items),
            invoice.confidence,
        )

        if invoice.warnings:
            log.warning("Parser warnings: %s", "; ".join(invoice.warnings))

        return invoice

    def _safe_date(self, value) -> str:
        if not value:
            return ""
        return str(value)
