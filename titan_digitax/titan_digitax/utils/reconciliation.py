"""Periodic reconciliation for invoices whose Digitax callback may have been lost.

The normal path is push-based: send a sale, Digitax calls back later with the
final eTIMS details (custom_etims_url). Digitax's own docs document GET
/sales/{id} as the way to check a sale's status independently of a callback,
but nothing in this codebase polled it proactively - an invoice whose callback
never arrived (dropped request, a transient failure on Digitax's retry, etc.)
would stay "sent" with no eTIMS URL forever, invisible to every other check in
this app (all of which key off custom_sent_to_digitax=0).

This scans for exactly that shape - custom_sent_to_digitax=1, a custom_sale_id
to poll, no custom_etims_url yet - and backfills the same fields a real
callback would have written, using the exact same field map an already-verified
duplicate-sale reconcile uses (update_invoice_with_existing_digitax_sale).

Scope: Sales Invoice only. Digitax Amendment Row callbacks (virtual
sales/reversals) can go missing the same way, but reconciling those needs the
same real-item rework tracked as N6 - out of scope here, left for that session.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from .company_config import get_digitax_settings, get_enabled_digitax_companies, run_as_digitax_sync_user
from .sales import _write_digitax_response_fields, fetch_sale_details_from_digitax

# An invoice isn't considered for polling until this long after it was sent (or,
# on a later run, this long after its last poll) - Digitax's callback usually
# lands within minutes, so polling immediately would just be racing it. This also
# doubles as the re-poll interval: a sale still PENDING at KRA gets checked again
# every window rather than hammered every run.
RECONCILIATION_GRACE_MINUTES = 120

# Bounded per company per run, oldest-needing-a-look first - this backlog should
# normally be small (a lost callback is meant to be the exception), so one flat
# batch is enough; unlike the hourly retry sweep there's no multi-round drain here.
RECONCILIATION_BATCH_SIZE = 50


def _is_due(row, cutoff):
    last_activity = row.custom_last_digitax_reconcile_at or row.custom_last_digitax_attempt_at
    if not last_activity:
        return True  # no timestamp at all - don't block on it, just check
    return frappe.utils.get_datetime(last_activity) <= cutoff


def reconcile_stale_digitax_sales(company=None):
    """Poll GET /sales/{id} for invoices that look sent but never got a callback.

    Manual/targeted calls (a specific company) run once. The scheduled cron call
    (no args) sweeps every enabled company, one bounded batch each.
    """
    logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)
    companies = [company] if company else get_enabled_digitax_companies()

    checked = 0
    backfilled = 0
    still_pending = 0
    errors = []

    for comp in companies:
        settings = get_digitax_settings(comp)
        target_country = settings.get("target_country") or "Kenya"
        company_country = frappe.db.get_value("Company", comp, "country")
        if company_country != target_country:
            continue

        candidates = frappe.get_all(
            "Sales Invoice",
            filters={
                "docstatus": 1,
                "company": comp,
                "custom_sent_to_digitax": 1,
                "custom_sale_id": ["is", "set"],
                "custom_etims_url": ["is", "not set"],
            },
            fields=["name", "custom_sale_id", "custom_last_digitax_attempt_at", "custom_last_digitax_reconcile_at"],
            order_by="posting_date asc",
            limit_page_length=RECONCILIATION_BATCH_SIZE,
        )
        if not candidates:
            continue

        cutoff = add_to_date(now_datetime(), minutes=-RECONCILIATION_GRACE_MINUTES)
        due = [row for row in candidates if _is_due(row, cutoff)]
        if not due:
            continue

        # Oldest-needing-a-look first within this bounded batch.
        due.sort(key=lambda row: row.custom_last_digitax_reconcile_at or row.custom_last_digitax_attempt_at or "")

        def _poll_company_batch():
            nonlocal checked, backfilled, still_pending

            for row in due:
                checked += 1
                try:
                    sale_details = fetch_sale_details_from_digitax(row.custom_sale_id, comp)
                except Exception as e:
                    errors.append(f"{row.name}: {e}")
                    continue

                if not sale_details:
                    # Fetch itself failed (network/API error) - already logged by
                    # fetch_sale_details_from_digitax. Stamp the poll timestamp
                    # anyway so this invoice waits a full grace window before the
                    # next attempt instead of being retried every run.
                    frappe.db.set_value(
                        "Sales Invoice",
                        row.name,
                        "custom_last_digitax_reconcile_at",
                        now_datetime(),
                        update_modified=False,
                    )
                    still_pending += 1
                    continue

                etims_url = sale_details.get("etims_url", "")
                field_mapping = {
                    "custom_sale_id": sale_details.get("id", "") or row.custom_sale_id,
                    "custom_offline_url": sale_details.get("offline_url", ""),
                    "custom_sale_detail_url": sale_details.get("sale_detail_url", ""),
                    "custom_serial_number": sale_details.get("serial_number", ""),
                    "custom_invoice_number": sale_details.get("invoice_number", ""),
                    "custom_digitax_status": sale_details.get("status", ""),
                    "custom_date": sale_details.get("date", ""),
                    "custom_time": sale_details.get("time", ""),
                    "custom_receipt_type_code": sale_details.get("receipt_type_code", ""),
                    "custom_original_sale_id": sale_details.get("original_sale_id", ""),
                    "custom_etims_url": etims_url,
                    "custom_last_digitax_reconcile_at": now_datetime(),
                }
                if etims_url:
                    field_mapping["custom_error_message"] = ""

                _write_digitax_response_fields(
                    row.name,
                    field_mapping,
                    logger,
                    item_title=f"Digitax Response Too Long To Save: {row.name}",
                    description_intro=(
                        f"<strong>Sales Invoice:</strong> {row.name} was reconciled against Digitax "
                        f"(GET /sales/{{id}}) after its callback appeared to be lost, but part "
                        f"of the response could not be saved locally."
                    ),
                )

                if etims_url:
                    backfilled += 1
                    logger.info(f"Reconciliation backfilled eTIMS URL for {row.name}")
                else:
                    still_pending += 1

            frappe.db.commit()

        run_as_digitax_sync_user(comp, _poll_company_batch)

    return {
        "checked": checked,
        "backfilled": backfilled,
        "still_pending": still_pending,
        "errors": errors,
    }


@frappe.whitelist()
def job_reconcile_stale_digitax_sales():
    """Hourly cron entrypoint - always runs as Administrator (the scheduler's own
    identity), same as job_retry_sending_sales_invoices.
    """
    frappe.only_for("System Manager")

    if not frappe.conf.get("sync_with_digitax"):
        return {"skipped": True, "reason": "Digitax sync is disabled in site configuration"}

    return reconcile_stale_digitax_sales()
