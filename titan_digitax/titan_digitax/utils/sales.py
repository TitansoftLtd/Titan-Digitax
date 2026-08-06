import frappe
import json
import requests
from datetime import datetime, timezone
from frappe import _
from frappe.utils import now_datetime
from .utils import get_digitax_credentials, get_digitax_callback_url_for_sales_with_items
from .company_config import (
    is_digitax_enabled_for_company,
    get_enabled_digitax_companies,
    get_digitax_settings,
)
from .sales_items import build_digitax_items_payload


@frappe.whitelist()
def send_sales_invoice_to_digitax(docname):
    if not frappe.conf.get("sync_with_digitax"):
        return {
            "skipped": True,
            "reason": "Digitax sync is disabled in site configuration",
            "message": "To enable, set 'sync_with_digitax: true' in common_site_config.json"
        }

    logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)

    logger.info(f"=" * 80)
    logger.info(f"DIGITAX SEND: Starting process for Sales Invoice: {docname}")
    logger.info(f"=" * 80)

    try:
        doc = frappe.get_doc("Sales Invoice", docname)
        logger.info(f"Invoice Details: Company={doc.company}, Grand Total={doc.grand_total}, Is Return={doc.is_return}, Docstatus={doc.docstatus}")
    except Exception as e:
        logger.error(f"Failed to get Sales Invoice document: {str(e)}")
        return {"error": "Failed to load invoice", "message": str(e)}

    # Load this company's Digitax Company Settings first (needed for all subsequent checks)
    if not frappe.db.exists("Digitax Company Settings", doc.company):
        logger.info(f"Skipping: No Digitax Company Settings configured for {doc.company}")
        return {"skipped": True, "reason": "Digitax not configured for this company"}

    digitax_settings = get_digitax_settings(doc.company)
    logger.info(f"Digitax Company Settings loaded for {doc.company}")

    # Check company country against this company's own target country
    company_country = frappe.db.get_value("Company", doc.company, "country")
    target_country = digitax_settings.get("target_country") or "Kenya"
    logger.info(f"Company Country Check: {company_country}, Target: {target_country}")
    if not company_country == target_country:
        logger.info(f"Skipping: Company not in {target_country} (Country: {company_country})")
        return {"skipped": True, "reason": f"Company not in {target_country}"}

    # Check if this company is enabled for DigiTax.
    # Credit notes (is_return=1) and invoices that already have a custom_sale_id are allowed
    # through regardless so that reversals and amendments never get blocked even when a company
    # is later disabled (send-block, amend-allow policy).
    is_amendment_or_return = bool(doc.is_return or doc.get("custom_sale_id"))
    if not is_amendment_or_return:
        if not is_digitax_enabled_for_company(doc.company):
            logger.info(f"Skipping: Company {doc.company} is not enabled for Digitax")
            return {"skipped": True, "reason": "Company not enabled for Digitax"}
    else:
        logger.info(f"Company eligibility check bypassed for credit note / amendment (is_return={doc.is_return}, custom_sale_id={doc.get('custom_sale_id')})")

    # Increment retry count if this is a retry (error message exists)
    if doc.custom_error_message:
        current_retry_count = doc.custom_retry_count or 0
        logger.info(f"Retry detected: Current retry count = {current_retry_count}")
        frappe.db.set_value("Sales Invoice", doc.name, "custom_retry_count", current_retry_count + 1, update_modified=False)

    endpoint = ""
    # Get invoice status codes from settings
    submitted_status = digitax_settings.get("submitted_invoice_status_code") or "02"
    cancelled_status = digitax_settings.get("cancelled_invoice_status_code") or "04"
    
    # Persist the trader invoice number BEFORE sending. DigiTax echoes this value back
    # on the async callback, which resolves the invoice by
    # {"custom_trader_invoice_number": ...} first. The field used to be read in four
    # places and never written, so resolution fell through to matching the docname —
    # and since "/" is rewritten to "_" here, any invoice whose name contains a slash
    # could never be matched and silently lost every callback.
    trader_invoice_number = _get_trader_invoice_base(doc)
    if doc.custom_trader_invoice_number != trader_invoice_number:
        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            "custom_trader_invoice_number",
            trader_invoice_number,
            update_modified=False,
        )
        doc.custom_trader_invoice_number = trader_invoice_number

    payload = {
        "trader_invoice_number": trader_invoice_number,
        "items": [],
        "invoice_status_code": submitted_status if doc.docstatus == 1 else cancelled_status,
        "callback_url": get_digitax_callback_url_for_sales_with_items(doc.company),
    }

    customer_pin = resolve_digitax_customer_pin(doc)
    if customer_pin.get("pin"):
        payload["customer_tin"] = str(customer_pin.get("pin"))
        payload["customer_name"]= str(doc.customer_name)

    if not doc.is_return:
        endpoint = "sales-with-items"
        payload["sale_date"] = str(doc.posting_date)
        payload["receipt_type_code"] = digitax_settings.get("default_receipt_type_code") or "S"
        payload["payment_type_code"] = digitax_settings.get("default_payment_type_code") or "01"
        logger.info(f"Invoice Type: Regular Sales Invoice")
    else:
        endpoint = "credit-notes-with-barcode"
        payload["return_date"] = str(doc.posting_date)
        payload["sale_id"] = frappe.db.get_value("Sales Invoice", doc.return_against, "custom_sale_id")
        logger.info(f"Invoice Type: Credit Note (Return against: {doc.return_against})")

        if not payload["sale_id"]:
            frappe.db.set_value(
                "Sales Invoice",
                doc.name,
                "custom_error_message",
                "Original sale not found in Digitax. Cannot process credit note.",
                update_modified=False
            )
            frappe.db.commit()
            frappe.msgprint(f"Original sale not found in Digitax. Cannot process credit note for {doc.name}.")
            return

    # D9/D14: aggregate SI lines by DigiTax display name (Digitax Item Name when required)
    built = build_digitax_items_payload(doc, digitax_settings, logger)
    if not built.get("ok"):
        return {
            "skipped": True,
            "reason": built.get("reason"),
            "message": built.get("message"),
        }

    payload["items"] = built["items"]
    logger.info(f"Items added to payload ({len(payload['items'])} line(s)): {payload['items']}")

    response_data, status_code = _post_to_digitax(
        endpoint, payload, digitax_settings, logger, company=doc.company
    )

    if status_code == 0:
        error_msg = response_data.get("message", "Connection error sending to Digitax")
        frappe.db.set_value(
            "Sales Invoice", doc.name, "custom_error_message", error_msg, update_modified=False
        )
        frappe.log_error(
            message=f"Connection error sending Sales Invoice {doc.name} to Digitax: {error_msg}",
            title="Digitax Send Error",
        )
    elif 200 <= status_code < 300:
        logger.info(f"SUCCESS: Invoice sent successfully to Digitax")
        logger.info(f"Sale ID: {response_data.get('id')}, Status: {response_data.get('status')}")

        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            {
                "custom_offline_url": response_data.get("offline_url", ""),
                "custom_sale_detail_url": response_data.get("sale_detail_url", ""),
                "custom_serial_number": response_data.get("serial_number", ""),
                "custom_invoice_number": response_data.get("invoice_number", ""),
                "custom_digitax_status": response_data.get("status", ""),
                "custom_sale_id": response_data.get("id", ""),
                "custom_date": response_data.get("date", ""),
                "custom_time": response_data.get("time", ""),
                "custom_receipt_type_code": response_data.get("receipt_type_code", ""),
                "custom_original_sale_id": response_data.get("original_sale_id", ""),
                "custom_sent_to_digitax": 1,
            },
            update_modified=False,
        )
        logger.info(f"Invoice fields updated in ERPNext")
    elif status_code == 409:
        error_message = response_data.get("message", "").lower()
        logger.info(f"409 Conflict received. Message: {response_data.get('message', '')}")
        is_duplicate = "trader_invoice_number has already been used" in error_message

        if is_duplicate:
            logger.info(f"DUPLICATE DETECTED: Invoice already exists in Digitax (409)")
            metadata = response_data.get("metadata", {})
            existing_sale_id = metadata.get("existing_sale_id", "")
            trader_invoice_number = metadata.get("trader_invoice_number", "")
            logger.info(f"Existing Sale ID: {existing_sale_id}, Trader Invoice Number: {trader_invoice_number}")
            response_data = update_invoice_with_existing_digitax_sale(doc, existing_sale_id, logger)
        else:
            logger.error(f"409 Conflict (NOT duplicate): {response_data.get('message', 'Unknown conflict')}")
            frappe.db.set_value(
                "Sales Invoice",
                doc.name,
                "custom_error_message",
                response_data.get("message", "Conflict error (409)"),
                update_modified=False,
            )
            logger.info(f"Error message saved to invoice")
    else:
        error_msg = response_data.get("message", "Unknown error")
        logger.error(f"API ERROR: Status {status_code}, Message: {error_msg}")
        frappe.db.set_value(
            "Sales Invoice", doc.name, "custom_error_message", error_msg, update_modified=False
        )
        logger.info(f"Error message saved to invoice")

    frappe.db.commit()
    logger.info(f"=" * 80)
    logger.info(f"DIGITAX SEND: Process completed for {docname}")
    logger.info(f"=" * 80)
    return response_data

@frappe.whitelist()
def retry_sending_sales_invoice_to_digitax(invoice_name=None, company=None, from_date=None, to_date=None, retry_count=None):
    # Every setting (target country, max retries) is per company now, so each company's
    # invoices are queried separately using that company's own values rather than one
    # combined query spanning every enabled company.
    companies = [company] if company else get_enabled_digitax_companies()

    attempted = 0
    errors = []

    for comp in companies:
        if not is_digitax_enabled_for_company(comp):
            continue

        settings = get_digitax_settings(comp)
        target_country = settings.get("target_country") or "Kenya"
        company_country = frappe.db.get_value("Company", comp, "country")
        if company_country != target_country:
            continue

        if retry_count is None:
            try:
                comp_retry_count = int(settings.get("max_retry_attempts") or 5)
                if comp_retry_count <= 0:
                    comp_retry_count = 5  # Fallback to default if invalid
            except (ValueError, TypeError):
                comp_retry_count = 5  # Fallback if conversion fails
        else:
            comp_retry_count = retry_count

        filters = {
            "docstatus": 1,
            "custom_sent_to_digitax": 0,
            "company": comp,
            "custom_retry_count": ["<", comp_retry_count],
        }
        if invoice_name:
            filters["name"] = invoice_name
        if from_date and to_date:
            filters["posting_date"] = ["between", [from_date, to_date]]

        invoices = frappe.get_all("Sales Invoice", filters=filters, pluck="name")
        attempted += len(invoices)

        for invoice in invoices:
            try:
                send_sales_invoice_to_digitax(invoice)
            except Exception as e:
                errors.append(f"{invoice}: {e}")

    # Re-query rather than trust each call's return shape (send_sales_invoice_to_digitax
    # returns different dict shapes for skip/error/success) — the field itself is the
    # single source of truth for whether a send actually landed.
    still_unsent = 0
    if attempted:
        filters = {"docstatus": 1, "custom_sent_to_digitax": 0}
        filters["company"] = company if company else ["in", companies]
        if invoice_name:
            filters["name"] = invoice_name
        still_unsent = frappe.db.count("Sales Invoice", filters)

    return {
        "attempted": attempted,
        "sent": max(attempted - still_unsent, 0),
        "still_unsent": still_unsent,
        "errors": errors,
    }

@frappe.whitelist()
def job_retry_sending_sales_invoices():
    if not frappe.conf.get("sync_with_digitax"):
        frappe.msgprint("Digitax sync is disabled in site configuration")
        return {
            "skipped": True,
            "reason": "Digitax sync is disabled in site configuration"
        }
    
    # Size the job for the slowest-configured enabled company, since this one job
    # retries invoices across every company in a single sweep.
    job_timeout = 600
    for comp in get_enabled_digitax_companies():
        settings = get_digitax_settings(comp)
        try:
            comp_timeout = int(settings.get("background_job_timeout") or 600)
            if comp_timeout > job_timeout:
                job_timeout = comp_timeout
        except (ValueError, TypeError):
            pass

    frappe.enqueue(
        retry_sending_sales_invoice_to_digitax,
        queue="default",
        timeout=job_timeout,
    )

def fetch_sale_details_from_digitax(sale_id, company):
    """
    Fetch full sale/credit note details from Digitax API using sale_id.
    Note: The same endpoint is used for both invoices and credit notes.
    
    Args:
        sale_id (str): The Digitax sale ID
        
    Returns:
        dict: Sale details from Digitax (includes credit note details if linked) or None if error
    """
    logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)
    
    try:
        digitax_base_url, digitax_api_key = get_digitax_credentials(company)
        
        if not digitax_base_url or not digitax_api_key:
            logger.error("Digitax credentials not configured")
            return None
        
        # Get API timeout from this company's settings (ensure it's an int and positive)
        digitax_settings = get_digitax_settings(company)
        try:
            api_timeout = int(digitax_settings.get("api_request_timeout") or 30)
            if api_timeout <= 0:
                api_timeout = 30  # Fallback to default if invalid
        except (ValueError, TypeError):
            api_timeout = 30  # Fallback if conversion fails
        
        headers = {
            "accept": "application/json",
            "X-API-Key": digitax_api_key,
            "content-type": "application/json",
        }
        
        # Single endpoint for both invoices and credit notes
        url = f"{digitax_base_url.rstrip('/')}/sales/{sale_id}"
        
        logger.info(f"Fetching sale details from Digitax: {url}")
        
        response = requests.get(url, headers=headers, timeout=api_timeout)
        logger.info(f"Response Status Code: {response.status_code}")
        
        if response.status_code == 200:
            sale_data = response.json()
            logger.info(f"Successfully fetched sale details for sale_id: {sale_id}")
            return sale_data
        else:
            logger.error(f"Failed to fetch sale details. Status: {response.status_code}, Response: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching sale details from Digitax: {str(e)}")
        frappe.log_error(
            message=f"Error fetching sale details for sale_id {sale_id}: {str(e)}\n{frappe.get_traceback()}",
            title="Digitax Fetch Sale Details Error"
        )
        return None


def update_invoice_with_existing_digitax_sale(doc, existing_sale_id, logger):
    """
    Update Sales Invoice fields when a duplicate is detected in Digitax.
    Fetches full sale details and populates all Digitax custom fields.
    Only updates fields if they are empty or values don't match.
    
    Args:
        doc: Sales Invoice document
        existing_sale_id (str): The existing sale ID from Digitax
        logger: Logger instance
        
    Returns:
        dict: Response data with success status and sale details
    """
    response_data = {}
    
    # Helper function to update field only if value changed
    def update_field_if_changed(doctype, docname, fieldname, new_value):
        current_value = frappe.db.get_value(doctype, docname, fieldname)
        # Convert None to empty string for comparison
        current_value = current_value if current_value is not None else ""
        new_value = new_value if new_value is not None else ""
        
        # Only update if values are different
        if str(current_value) != str(new_value):
            frappe.db.set_value(doctype, docname, fieldname, new_value, update_modified=False)
            return True
        return False
    
    if existing_sale_id:
        logger.info(f"Fetching full sale details for existing sale_id: {existing_sale_id}")
        sale_details = fetch_sale_details_from_digitax(existing_sale_id, doc.company)
        
        if sale_details:
            # Update all Digitax fields with fetched data (only if changed)
            logger.info(f"Checking and updating invoice fields with Digitax details")
            
            updated_fields = []
            
            # Map of field names to values from Digitax
            field_mapping = {
                "custom_offline_url": sale_details.get("offline_url", ""),
                "custom_sale_detail_url": sale_details.get("sale_detail_url", ""),
                "custom_serial_number": sale_details.get("serial_number", ""),
                "custom_invoice_number": sale_details.get("invoice_number", ""),
                "custom_digitax_status": sale_details.get("status", ""),
                "custom_sale_id": sale_details.get("id", ""),
                "custom_date": sale_details.get("date", ""),
                "custom_time": sale_details.get("time", ""),
                "custom_receipt_type_code": sale_details.get("receipt_type_code", ""),
                "custom_original_sale_id": sale_details.get("original_sale_id", ""),
                "custom_sent_to_digitax": 1,
                "custom_error_message": "",
            }
            
            # Update each field only if value changed
            for field, value in field_mapping.items():
                if update_field_if_changed("Sales Invoice", doc.name, field, value):
                    updated_fields.append(field)
            
            if updated_fields:
                logger.info(f"Updated fields: {', '.join(updated_fields)}")
            else:
                logger.info(f"All fields already up to date, no changes needed")
            
            # Update response_data with fetched details
            response_data = sale_details.copy()
            response_data["success"] = True
            response_data["already_exists"] = True
        else:
            # Failed to fetch details, just mark as sent with basic info
            logger.warning(f"Could not fetch full sale details, updating with basic info only")
            
            updated_fields = []
            basic_fields = {
                "custom_sent_to_digitax": 1,
                "custom_error_message": "",
                "custom_digitax_status": "Already Exists",
                "custom_sale_id": existing_sale_id,
            }
            
            for field, value in basic_fields.items():
                if update_field_if_changed("Sales Invoice", doc.name, field, value):
                    updated_fields.append(field)
            
            if updated_fields:
                logger.info(f"Updated fields: {', '.join(updated_fields)}")
            
            response_data["success"] = True
            response_data["already_exists"] = True
            response_data["id"] = existing_sale_id
            response_data["status"] = "Already Exists"
    else:
        # No sale_id in metadata, just mark as sent
        logger.warning(f"No existing_sale_id in metadata, marking as sent without full details")
        
        updated_fields = []
        basic_fields = {
            "custom_sent_to_digitax": 1,
            "custom_error_message": "",
            "custom_digitax_status": "Already Exists",
        }
        
        for field, value in basic_fields.items():
            if update_field_if_changed("Sales Invoice", doc.name, field, value):
                updated_fields.append(field)
        
        if updated_fields:
            logger.info(f"Updated fields: {', '.join(updated_fields)}")
        
        response_data["success"] = True
        response_data["already_exists"] = True
        response_data["status"] = "Already Exists"
    
    logger.info(f"Invoice marked as synced (already exists in Digitax)")
    return response_data


def resolve_digitax_customer_pin(doc):
    """
    Resolve the customer PIN for Digitax payloads.

    Parent is the operational source in Braeburn, while Customer and Sales
    Invoice are retained as fallbacks for older data.
    """
    parent_code = getattr(doc, "parent_code", None)
    if parent_code:
        parent_pin = _get_parent_pin(parent_code)
        if parent_pin:
            return {"pin": parent_pin, "source": "Parent", "parent_code": parent_code}

    customer = getattr(doc, "customer", None)
    if customer:
        parent_pin = _get_parent_pin_from_customer(customer)
        if parent_pin:
            return parent_pin

        customer_pin = frappe.db.get_value("Customer", customer, "tax_id")
        if customer_pin:
            return {"pin": customer_pin, "source": "Customer", "parent_code": None}

    invoice_pin = getattr(doc, "tax_id", None)
    if invoice_pin:
        return {"pin": invoice_pin, "source": "Sales Invoice", "parent_code": parent_code}

    return {"pin": None, "source": "Not Provided", "parent_code": parent_code}


def _get_parent_pin(parent_code):
    if not parent_code:
        return None

    try:
        frappe.get_meta("Parent")
    except Exception:
        return None

    parent_name = None
    if frappe.db.exists("Parent", parent_code):
        parent_name = parent_code

    if not parent_name:
        parent_name = frappe.db.get_value("Parent", {"parent_code": parent_code}, "name")

    if not parent_name:
        parent_name = frappe.db.get_value("Parent", {"account_code": parent_code}, "name")

    if parent_name:
        return frappe.db.get_value("Parent", parent_name, "tax_id")

    return None


def _get_parent_pin_from_customer(customer):
    try:
        customer_doc = frappe.get_cached_doc("Customer", customer)
    except Exception:
        return None

    for row in customer_doc.get("custom_parents") or []:
        parent_code = row.get("parent_code")
        parent_pin = _get_parent_pin(parent_code)
        if parent_pin:
            return {"pin": parent_pin, "source": "Parent", "parent_code": parent_code}

    return None


def _get_api_timeout(digitax_settings):
    try:
        api_timeout = int(digitax_settings.get("api_request_timeout") or 30)
        return api_timeout if api_timeout > 0 else 30
    except (TypeError, ValueError):
        return 30


def _get_digitax_headers(company=None):
    digitax_base_url, digitax_api_key = get_digitax_credentials(company)
    return digitax_base_url, {
        "accept": "application/json",
        "X-API-Key": digitax_api_key,
        "content-type": "application/json",
    }


def _get_trader_invoice_base(doc):
    return str(doc.custom_trader_invoice_number or (doc.name.replace("/", "_") if doc.name else ""))


def _get_default_digitax_item(doc, digitax_settings, include_sale_fields):
    amount = abs(doc.grand_total)
    item = {
        "item_bar_code": digitax_settings.get("default_item_bar_code") or "SCHOOL_FEES",
        "quantity": 1,
        "unit_price": amount,
        "total_amount": amount,
        "package_unit_quantity": amount,
        "discount_rate": 0,
        "discount_amount": 0,
        "item_description": digitax_settings.get("default_item_description") or "School Fees",
    }

    if include_sale_fields:
        item.update({
            "item_name": digitax_settings.get("default_item_name") or "School Fees",
            "item_class_code": digitax_settings.get("default_item_class_code") or "99020000",
            "item_tax_type_code": digitax_settings.get("default_item_tax_type_code") or "D",
            "is_stockable": bool(digitax_settings.get("default_is_stockable")),
        })

    return item


def _get_digitax_correction_date():
    """Use UTC date so correction events are never ahead of Digitax's server date."""
    return datetime.now(timezone.utc).date().isoformat()


def _post_to_digitax(endpoint, payload, digitax_settings, logger=None, company=None):
    """
    Shared low-level HTTP POST to Digitax.

    Returns (response_data, status_code).
    status_code == 0 signals a network/connection failure; all other values
    are real HTTP status codes from the Digitax server.
    """
    digitax_base_url, headers = _get_digitax_headers(company)
    if not digitax_base_url:
        error = {"error": "configuration", "message": "Digitax Base URL is not configured."}
        if logger:
            logger.error("Digitax Base URL is not configured.")
        return error, 0

    api_timeout = _get_api_timeout(digitax_settings)
    full_url = f"{digitax_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    serialized = json.dumps(payload)

    payload_logger = frappe.logger("digitax_payloads", allow_site=True, file_count=10)
    payload_logger.info(f"POST {full_url}\n{json.dumps(payload, indent=2)}")

    if logger:
        logger.info(f"Digitax Base URL: {digitax_base_url}")
        logger.info(f"API Request Timeout: {api_timeout} seconds")
        logger.info(f"Sending POST to Digitax: {full_url}")
        logger.info(f"Payload: {serialized}")

    try:
        response = requests.post(full_url, headers=headers, data=serialized, timeout=api_timeout)

        if logger:
            logger.info(f"Response Status Code: {response.status_code}")

        try:
            response_data = response.json()
            if logger:
                logger.info(f"Response Data: {json.dumps(response_data, indent=2)}")
        except Exception as json_error:
            if logger:
                logger.error(f"Failed to parse JSON response: {str(json_error)}")
                logger.error(f"Raw Response: {response.text}")
            response_data = {"error": "Invalid JSON response", "raw": response.text}

        return response_data, response.status_code

    except requests.exceptions.Timeout:
        error_msg = f"Request timeout - Digitax API did not respond within {api_timeout} seconds"
        if logger:
            logger.error(f"TIMEOUT ERROR: {error_msg}")
        frappe.log_error(message=error_msg, title="Digitax Timeout Error")
        return {"error": "timeout", "message": error_msg}, 0

    except requests.exceptions.RequestException as e:
        error_msg = f"Request failed: {str(e)}"
        if logger:
            logger.error(f"REQUEST ERROR: {str(e)}")
        frappe.log_error(message=error_msg, title="Digitax Request Error")
        return {"error": "request_failed", "message": error_msg}, 0

    except Exception as e:
        error_msg = str(e)
        if logger:
            logger.error(f"UNEXPECTED ERROR: {type(e).__name__}: {error_msg}")
        frappe.log_error(
            message=f"Unexpected error posting to Digitax: {error_msg}",
            title="Digitax Error",
        )
        return {"error": "exception", "message": error_msg}, 0


def _validate_virtual_amendment_user(digitax_settings):
    role = digitax_settings.get("virtual_amendment_role")
    if not role:
        frappe.throw(
            _("Set Virtual Amendment Role in this company's Digitax Company Settings before using virtual amendments."),
            title=_("Digitax Role Not Configured"),
        )

    if not _user_has_role(role):
        frappe.throw(
            _("You need the {0} role to create Digitax virtual amendments.").format(role),
            title=_("Not Permitted"),
        )


def _user_has_role(role, user=None):
    if not role:
        return False

    return role in (frappe.get_roles(user or frappe.session.user) or [])


def _validate_virtual_amendment_invoice(doc):
    if doc.docstatus != 1:
        frappe.throw(_("Digitax virtual amendments can only be created from submitted Sales Invoices."))

    if doc.is_return:
        frappe.throw(_("Digitax virtual amendments can only be created from original Sales Invoices, not Credit Notes."))

    if not doc.custom_sent_to_digitax and not doc.custom_sale_id:
        frappe.throw(_("This Sales Invoice has not been sent to Digitax yet."))


def _load_virtual_amendment_context(invoice_name):
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    digitax_settings = get_digitax_settings(doc.company)
    _validate_virtual_amendment_user(digitax_settings)
    _validate_virtual_amendment_invoice(doc)

    return doc, digitax_settings


def _require_correction_reason(correction_reason):
    correction_reason = (correction_reason or "").strip()
    if not correction_reason:
        frappe.throw(_("Please provide a correction reason before sending a Digitax virtual amendment."))
    return correction_reason


def _get_response_field_mapping(response_data):
    response_data = response_data or {}
    return {
        "custom_offline_url": response_data.get("offline_url", ""),
        "custom_etims_url": response_data.get("etims_url", ""),
        "custom_sale_detail_url": response_data.get("sale_detail_url", ""),
        "custom_serial_number": response_data.get("serial_number", ""),
        "custom_invoice_number": response_data.get("invoice_number", ""),
        "custom_digitax_status": response_data.get("status", ""),
        "custom_sale_id": response_data.get("id", ""),
        "custom_date": response_data.get("date", ""),
        "custom_time": response_data.get("time", ""),
        "custom_receipt_type_code": response_data.get("receipt_type_code", ""),
        "custom_original_sale_id": response_data.get("original_sale_id", ""),
        "custom_sent_to_digitax": 1,
        "custom_error_message": "",
    }


def _get_child_response_fields(response_data):
    response_data = response_data or {}
    return {
        "digitax_sale_id": response_data.get("id", ""),
        "digitax_status": response_data.get("status", ""),
        "offline_url": response_data.get("offline_url", ""),
        "etims_url": response_data.get("etims_url", ""),
        "sale_detail_url": response_data.get("sale_detail_url", ""),
        "serial_number": response_data.get("serial_number", ""),
        "invoice_number": response_data.get("invoice_number", ""),
        "receipt_type_code": response_data.get("receipt_type_code", ""),
        "original_sale_id": response_data.get("original_sale_id", ""),
        "digitax_date": response_data.get("date", ""),
        "digitax_time": response_data.get("time", ""),
    }


def _find_original_sale_row(doc):
    for row in doc.get("custom_digitax_amendments") or []:
        if row.amendment_type == "Original Sale" and row.status == "Sent":
            return row
    return None


def _ensure_original_sale_row(doc):
    existing = _find_original_sale_row(doc)
    if existing:
        return existing

    if not doc.custom_sale_id:
        frappe.throw(_("Original Digitax sale ID is missing. Cannot start a virtual amendment."))

    row = doc.append("custom_digitax_amendments", {
        "amendment_type": "Original Sale",
        "trader_invoice_number": _get_trader_invoice_base(doc),
        "status": "Sent",
        "digitax_sale_id": doc.custom_sale_id,
        "digitax_status": doc.custom_digitax_status,
        "offline_url": doc.custom_offline_url,
        "etims_url": doc.custom_etims_url,
        "sale_detail_url": doc.custom_sale_detail_url,
        "serial_number": doc.custom_serial_number,
        "invoice_number": doc.custom_invoice_number,
        "receipt_type_code": doc.custom_receipt_type_code,
        "original_sale_id": doc.custom_original_sale_id,
        "digitax_date": doc.custom_date,
        "digitax_time": doc.custom_time,
        "amount": abs(doc.grand_total),
        "customer_pin_after": doc.tax_id,
        "customer_pin_source_after": "Sales Invoice",
        "customer_name_after": doc.customer_name,
        "sent_on": now_datetime(),
        "sent_by": frappe.session.user,
    })
    doc.flags.ignore_validate_update_after_submit = True
    doc.save(ignore_permissions=True)
    return row


def _get_virtual_amendment_state(doc):
    base = _get_trader_invoice_base(doc)
    rows = [row for row in (doc.get("custom_digitax_amendments") or []) if row.status == "Sent"]
    original_row = _find_original_sale_row(doc)

    active_sale_id = doc.custom_sale_id
    active_trader_invoice_number = base
    active_row_name = None
    active_type = "Original Sale"

    if original_row:
        active_sale_id = original_row.digitax_sale_id
        active_trader_invoice_number = original_row.trader_invoice_number
        active_row_name = original_row.name

    sent_virtual_sales = []
    sent_virtual_reversals = []

    for row in rows:
        if row.amendment_type == "Virtual Credit Note":
            sent_virtual_reversals.append(row)
            if active_sale_id and row.reference_sale_id == active_sale_id:
                active_sale_id = None
                active_trader_invoice_number = None
                active_row_name = None
                active_type = None
        elif row.amendment_type == "Virtual Sale":
            sent_virtual_sales.append(row)
            active_sale_id = row.digitax_sale_id
            active_trader_invoice_number = row.trader_invoice_number
            active_row_name = row.name
            active_type = "Virtual Sale"

    next_sale_number = len(sent_virtual_sales) + 1
    if active_type == "Original Sale":
        next_reversal_number = None
        next_reversal_trader_invoice_number = f"{base}-R"
    elif active_type == "Virtual Sale":
        next_reversal_number = len(sent_virtual_sales)
        next_reversal_trader_invoice_number = f"{base}-R{next_reversal_number}"
    else:
        next_reversal_number = None
        next_reversal_trader_invoice_number = None

    return {
        "base": base,
        "active_sale_id": active_sale_id,
        "active_trader_invoice_number": active_trader_invoice_number,
        "active_row_name": active_row_name,
        "active_type": active_type,
        "next_sale_number": next_sale_number,
        "next_sale_trader_invoice_number": f"{base}-S{next_sale_number}",
        "next_reversal_number": next_reversal_number,
        "next_reversal_trader_invoice_number": next_reversal_trader_invoice_number,
        "has_reversal_waiting_for_sale": bool(sent_virtual_reversals and not active_sale_id),
    }


def _throw_if_sent_trader_exists(doc, trader_invoice_number):
    for row in doc.get("custom_digitax_amendments") or []:
        if row.trader_invoice_number == trader_invoice_number and row.status == "Sent":
            frappe.throw(_("Digitax amendment {0} has already been sent.").format(trader_invoice_number))


def _build_virtual_reversal_payload(doc, digitax_settings, state):
    submitted_status = digitax_settings.get("submitted_invoice_status_code") or "02"
    payload = {
        "trader_invoice_number": state["next_reversal_trader_invoice_number"],
        "items": [_get_default_digitax_item(doc, digitax_settings, include_sale_fields=False)],
        "invoice_status_code": submitted_status,
        "callback_url": get_digitax_callback_url_for_sales_with_items(doc.company),
        "return_date": _get_digitax_correction_date(),
        "sale_id": state["active_sale_id"],
    }

    customer_pin = resolve_digitax_customer_pin(doc)
    if customer_pin.get("pin"):
        payload["customer_tin"] = str(customer_pin.get("pin"))
        payload["customer_name"] = str(doc.customer_name)

    return payload


def _build_virtual_sale_payload(doc, digitax_settings, state):
    submitted_status = digitax_settings.get("submitted_invoice_status_code") or "02"
    payload = {
        "trader_invoice_number": state["next_sale_trader_invoice_number"],
        "items": [_get_default_digitax_item(doc, digitax_settings, include_sale_fields=True)],
        "invoice_status_code": submitted_status,
        "callback_url": get_digitax_callback_url_for_sales_with_items(doc.company),
        "sale_date": _get_digitax_correction_date(),
        "receipt_type_code": digitax_settings.get("default_receipt_type_code") or "S",
        "payment_type_code": digitax_settings.get("default_payment_type_code") or "01",
    }

    customer_pin = resolve_digitax_customer_pin(doc)
    if customer_pin.get("pin"):
        payload["customer_tin"] = str(customer_pin.get("pin"))
        payload["customer_name"] = str(doc.customer_name)

    return payload


def _post_virtual_amendment(url, payload, digitax_settings, logger, company=None):
    response_data, status_code = _post_to_digitax(
        url, payload, digitax_settings, logger, company=company
    )

    if status_code == 0:
        logger.error(f"Digitax virtual amendment network failure: {response_data}")
        return response_data, 0

    if status_code == 409 and "trader_invoice_number has already been used" in (response_data.get("message", "").lower()):
        existing_sale_id = (response_data.get("metadata") or {}).get("existing_sale_id", "")
        if existing_sale_id:
            sale_details = fetch_sale_details_from_digitax(existing_sale_id, company)
            if sale_details:
                sale_details["already_exists"] = True
                return sale_details, 200

        response_data["already_exists"] = True
        response_data["id"] = existing_sale_id
        response_data["status"] = "Already Exists"
        return response_data, 200

    if status_code >= 400:
        logger.error(f"Digitax virtual amendment failed: {status_code} {response_data}")

    return response_data, status_code


def _append_virtual_amendment_row(doc, row_data):
    row = doc.append("custom_digitax_amendments", row_data)
    doc.flags.ignore_validate_update_after_submit = True
    doc.save(ignore_permissions=True)
    return row


def _mark_invoice_awaiting_corrected_sale(invoice_name):
    frappe.db.set_value(
        "Sales Invoice",
        invoice_name,
        {
            "custom_sale_id": "",
            "custom_offline_url": "",
            "custom_etims_url": "",
            "custom_sale_detail_url": "",
            "custom_serial_number": "",
            "custom_invoice_number": "",
            "custom_digitax_status": "Awaiting Corrected Virtual Sale",
            "custom_date": "",
            "custom_time": "",
            "custom_receipt_type_code": "",
            "custom_original_sale_id": "",
            "custom_error_message": "",
        },
        update_modified=False,
    )


def _update_invoice_active_digitax_sale(invoice_name, response_data):
    frappe.db.set_value("Sales Invoice", invoice_name, _get_response_field_mapping(response_data), update_modified=False)


def _json_dump(data):
    return json.dumps(data or {}, indent=2, default=str)


def _build_preview_response(action, doc, trader_invoice_number, correction_reason, before_values, after_values):
    return {
        "action": action,
        "invoice_name": doc.name,
        "customer": doc.customer_name,
        "trader_invoice_number": trader_invoice_number,
        "amount": abs(doc.grand_total),
        "item": "School Fees",
        "correction_reason": correction_reason,
        "before": before_values,
        "after": after_values,
        "warning": _("This Digitax virtual amendment does not change accounts, receivables, MIS balances, or Sage."),
    }


def _build_virtual_reversal_preview(doc, state, correction_reason):
    customer_pin = resolve_digitax_customer_pin(doc)
    return _build_preview_response(
        "Virtual Credit Note",
        doc,
        state["next_reversal_trader_invoice_number"],
        correction_reason,
        {
            "Digitax Sale ID": state.get("active_sale_id"),
            "Trader Invoice No.": state.get("active_trader_invoice_number"),
            "Amount": abs(doc.grand_total),
            "Status": "Active Digitax Sale",
        },
        {
            "Digitax Sale ID": "No active sale until corrected virtual sale is sent",
            "Trader Invoice No.": state["next_reversal_trader_invoice_number"],
            "Amount": abs(doc.grand_total),
            "Status": "Awaiting Corrected Virtual Sale",
            "Correction Reason": correction_reason,
            "Customer PIN": customer_pin.get("pin") or "Not provided",
            "PIN Source": customer_pin.get("source"),
        },
    )


def _build_virtual_sale_preview(doc, state, correction_reason):
    customer_pin = resolve_digitax_customer_pin(doc)
    return _build_preview_response(
        "Virtual Sale",
        doc,
        state["next_sale_trader_invoice_number"],
        correction_reason,
        {
            "Customer PIN": doc.tax_id or "Not provided",
            "PIN Source": "Sales Invoice" if doc.tax_id else "Not Provided",
            "Customer Name": doc.customer_name,
            "Trader Invoice No.": "Awaiting corrected virtual sale",
            "Digitax Sale ID": "No active sale",
            "Amount": abs(doc.grand_total),
        },
        {
            "Customer PIN": customer_pin.get("pin") or "Not provided",
            "PIN Source": customer_pin.get("source"),
            "Customer Name": doc.customer_name,
            "Trader Invoice No.": state["next_sale_trader_invoice_number"],
            "Digitax Sale ID": "New sale will be created",
            "Amount": abs(doc.grand_total),
            "Correction Reason": correction_reason,
        },
    )


@frappe.whitelist()
def get_digitax_virtual_amendment_status(invoice_name):
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    digitax_settings = get_digitax_settings(doc.company)
    role = digitax_settings.get("virtual_amendment_role")

    can_create = bool(
        role
        and _user_has_role(role)
        and doc.docstatus == 1
        and not doc.is_return
        and (doc.custom_sent_to_digitax or doc.custom_sale_id or _find_original_sale_row(doc))
    )

    state = _get_virtual_amendment_state(doc)
    return {
        "can_create": can_create,
        "role_configured": bool(role),
        "required_role": role,
        "can_send_reversal": bool(can_create and state.get("active_sale_id")),
        "can_send_sale": bool(can_create and state.get("has_reversal_waiting_for_sale")),
        "active_sale_id": state.get("active_sale_id"),
        "active_trader_invoice_number": state.get("active_trader_invoice_number"),
        "next_reversal_trader_invoice_number": state.get("next_reversal_trader_invoice_number"),
        "next_sale_trader_invoice_number": state.get("next_sale_trader_invoice_number"),
    }


@frappe.whitelist()
def preview_virtual_digitax_reversal(invoice_name, correction_reason=None):
    correction_reason = _require_correction_reason(correction_reason)
    doc, digitax_settings = _load_virtual_amendment_context(invoice_name)
    state = _get_virtual_amendment_state(doc)

    if not state.get("active_sale_id"):
        frappe.throw(_("There is no active Digitax sale to reverse. Send the corrected virtual sale first if a reversal was already sent."))

    return _build_virtual_reversal_preview(doc, state, correction_reason)


@frappe.whitelist()
def preview_virtual_digitax_sale(invoice_name, correction_reason=None):
    correction_reason = _require_correction_reason(correction_reason)
    doc, digitax_settings = _load_virtual_amendment_context(invoice_name)
    state = _get_virtual_amendment_state(doc)

    if state.get("active_sale_id"):
        frappe.throw(_("Send a virtual reversal before sending a corrected virtual sale."))

    return _build_virtual_sale_preview(doc, state, correction_reason)


@frappe.whitelist()
def send_virtual_digitax_reversal(invoice_name, correction_reason=None):
    correction_reason = _require_correction_reason(correction_reason)
    logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)
    doc, digitax_settings = _load_virtual_amendment_context(invoice_name)
    _ensure_original_sale_row(doc)
    doc.reload()
    state = _get_virtual_amendment_state(doc)

    if not state.get("active_sale_id"):
        frappe.throw(_("There is no active Digitax sale to reverse."))

    trader_invoice_number = state["next_reversal_trader_invoice_number"]
    _throw_if_sent_trader_exists(doc, trader_invoice_number)

    payload = _build_virtual_reversal_payload(doc, digitax_settings, state)
    preview = _build_virtual_reversal_preview(doc, state, correction_reason)
    response_data, status_code = _post_virtual_amendment(
        "credit-notes-with-barcode", payload, digitax_settings, logger, company=doc.company
    )
    success = 200 <= status_code < 300

    row_data = {
        "amendment_type": "Virtual Credit Note",
        "trader_invoice_number": trader_invoice_number,
        "reference_sale_id": state["active_sale_id"],
        "status": "Sent" if success else "Failed",
        "amount": abs(doc.grand_total),
        "correction_reason": correction_reason,
        "preview_before": _json_dump(preview.get("before")),
        "preview_after": _json_dump(preview.get("after")),
        "request_payload": _json_dump(payload),
        "response_payload": _json_dump(response_data),
        "error_message": "" if success else response_data.get("message") or response_data.get("error"),
        "sent_on": now_datetime(),
        "sent_by": frappe.session.user,
    }
    row_data.update(_get_child_response_fields(response_data))
    row = _append_virtual_amendment_row(doc, row_data)

    if success:
        _mark_invoice_awaiting_corrected_sale(invoice_name)

    frappe.db.commit()
    response_data.update({"success": success, "amendment_row": row.name})
    return response_data


@frappe.whitelist()
def send_virtual_digitax_sale(invoice_name, correction_reason=None):
    correction_reason = _require_correction_reason(correction_reason)
    logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)
    doc, digitax_settings = _load_virtual_amendment_context(invoice_name)
    _ensure_original_sale_row(doc)
    doc.reload()
    state = _get_virtual_amendment_state(doc)

    if state.get("active_sale_id"):
        frappe.throw(_("Send a virtual reversal before sending a corrected virtual sale."))

    trader_invoice_number = state["next_sale_trader_invoice_number"]
    _throw_if_sent_trader_exists(doc, trader_invoice_number)

    payload = _build_virtual_sale_payload(doc, digitax_settings, state)
    preview = _build_virtual_sale_preview(doc, state, correction_reason)
    response_data, status_code = _post_virtual_amendment(
        "sales-with-items", payload, digitax_settings, logger, company=doc.company
    )
    success = 200 <= status_code < 300
    customer_pin = resolve_digitax_customer_pin(doc)

    row_data = {
        "amendment_type": "Virtual Sale",
        "trader_invoice_number": trader_invoice_number,
        "status": "Sent" if success else "Failed",
        "amount": abs(doc.grand_total),
        "customer_pin_before": doc.tax_id,
        "customer_pin_after": customer_pin.get("pin"),
        "customer_pin_source_before": "Sales Invoice" if doc.tax_id else "Not Provided",
        "customer_pin_source_after": customer_pin.get("source"),
        "customer_name_before": doc.customer_name,
        "customer_name_after": doc.customer_name,
        "correction_reason": correction_reason,
        "preview_before": _json_dump(preview.get("before")),
        "preview_after": _json_dump(preview.get("after")),
        "request_payload": _json_dump(payload),
        "response_payload": _json_dump(response_data),
        "error_message": "" if success else response_data.get("message") or response_data.get("error"),
        "sent_on": now_datetime(),
        "sent_by": frappe.session.user,
    }
    row_data.update(_get_child_response_fields(response_data))
    row = _append_virtual_amendment_row(doc, row_data)

    if success:
        _update_invoice_active_digitax_sale(invoice_name, response_data)

    frappe.db.commit()
    response_data.update({"success": success, "amendment_row": row.name})
    return response_data

