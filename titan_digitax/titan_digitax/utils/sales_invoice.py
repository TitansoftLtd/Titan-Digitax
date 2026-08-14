import frappe
from frappe import _
from .sales import send_sales_invoice_to_digitax, validate_credit_note_against_active_amendments


def validate(doc, method):
    validate_credit_note_against_active_amendments(doc, method)


def on_submit(doc, method):
    if not frappe.conf.get("sync_with_digitax"):
        return

    # Sending to Digitax is a synchronous HTTP call (up to api_request_timeout,
    # default 30s) that also writes and commits mid-function. Calling it directly
    # here would hold the submit's database locks (invoice, GL entries, party
    # ledger) open for that whole call, and its internal commit would split the
    # submit transaction - a later failure in the same submit pipeline (another
    # app's hook, a notification) could then roll back everything except the
    # already-committed Digitax fields. Enqueue it to run after this transaction
    # commits instead, so submit finishes on ERPNext's own timing and Digitax
    # status fields populate moments later (as already happens whenever the
    # hourly retry sweep is what actually sends an invoice).
    #
    # All gating (global enable, target_country, company eligibility, and
    # send-block/amend-allow) is still delegated to send_sales_invoice_to_digitax
    # itself, inside the queued job.
    frappe.enqueue(
        send_sales_invoice_to_digitax,
        queue="default",
        enqueue_after_commit=True,
        docname=doc.name,
    )


def on_cancel(doc, method):
    """Once a sale is filed with DigiTax/KRA, cancelling it in ERPNext alone would
    leave the two records permanently out of sync - KRA has no idea the sale was
    voided. Block the cancel and point the user at the correct correction path
    (a credit note against this invoice) instead of silently letting them diverge.
    """
    if not doc.custom_sent_to_digitax:
        return

    if doc.is_return:
        frappe.throw(
            _(
                "This credit note was already filed with Digitax/KRA and cannot be cancelled. "
                "If it was raised in error, create a new Sales Invoice for the client instead."
            ),
            title=_("Cannot Cancel"),
        )

    frappe.throw(
        _(
            "This invoice was already filed with Digitax/KRA and cannot be cancelled directly. "
            "Create a Credit Note against it instead, so the correction is also filed with KRA."
        ),
        title=_("Cannot Cancel"),
    )
