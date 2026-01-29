import frappe
import json
import requests
from .utils import get_digitax_credentials


# useful for other scenarios, when we need to append items to payload
def append_invoice_items_to_payload(doc, payload, is_return, digitax_settings=None):
    # Get default values from settings or use hardcoded fallbacks
    if not digitax_settings:
        digitax_settings = frappe.get_single("Digitax Settings")
    
    default_item_class_code = digitax_settings.get("default_item_class_code") or "99020000"
    default_item_tax_type_code = digitax_settings.get("default_item_tax_type_code") or "D"
    
    for item in doc.items:
        current_item_bar_code = item.item_code

        existing_item = None
        for existing in payload["items"]:
            if existing["item_bar_code"] == current_item_bar_code:
                existing_item = existing
                break

        current_quantity = abs(item.qty)
        current_total_amount = abs(item.amount) if item.amount > 0 else 0
        current_package_unit_quantity = abs(item.qty)
        current_discount_amount = abs(item.amount) if item.amount < 0 else 0

        if existing_item:
            existing_item["quantity"] += current_quantity
            existing_item["total_amount"] += current_total_amount
            existing_item["package_unit_quantity"] += current_package_unit_quantity
            existing_item["discount_amount"] += current_discount_amount
        else:
            new_item = {
                "item_bar_code": item.item_code,
                "quantity": current_quantity,
                "unit_price": abs(item.rate) if item.rate > 0 else 0,
                "total_amount": current_total_amount,
                "package_unit_quantity": current_package_unit_quantity,  # TODO: Confirm with Digitax if this is correct
                # TODO: Confirm with Digitax if discount_rate is percentage or amount
                "discount_rate": 1 if abs(item.rate) < 0 else 0,
                "discount_amount": current_discount_amount,
                "item_description": item.description,
            }

            if not is_return:
                new_item["item_name"] = item.item_name
                # Use item-specific codes or fall back to defaults from Digitax Settings
                new_item["item_class_code"] = item.custom_item_class_code or default_item_class_code
                new_item["item_tax_type_code"] = item.custom_tax_type_code or default_item_tax_type_code
                # Use standard ERPNext is_stock_item field
                new_item["is_stockable"] = True if item.is_stock_item else False

            payload["items"].append(new_item)

@frappe.whitelist()
def send_sales_invoice_to_digitax(docname):
    if not frappe.conf.get("sync_with_digitax"):
        return {
            "skipped": True,
            "reason": "Digitax sync is disabled in site configuration",
            "message": "To enable, set 'sync_with_digitax: true' in common_site_config.json"
        }
    
    # Check cache for in-progress sends to prevent concurrent calls (prevents duplicate sends)
    cache_key = f"digitax_sending_{docname}"
    if frappe.cache().get(cache_key):
        return {
            "skipped": True,
            "reason": "send_in_progress",
            "message": "Another send is already in progress for this invoice"
        }
    
    # Set cache flag for 120 seconds (protects against race conditions)
    frappe.cache().setex(cache_key, 120, "1")
    
    # Create a dedicated logger for Digitax operations
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

    # Load Digitax Settings first (needed for all subsequent checks)
    try:
        digitax_settings = frappe.get_single("Digitax Settings")
        logger.info(f"Digitax Settings loaded successfully")
        
        # If not enabled, skip silently (no error log, no processing)
        if not digitax_settings.get("enable"):
            logger.info(f"Skipping: Digitax integration disabled in settings")
            return {"skipped": True, "reason": "Digitax integration disabled"}
            
    except Exception as e:
        logger.warning(f"Could not access Digitax Settings: {str(e)}")
        return {"error": "Digitax Settings not accessible", "message": str(e)}

    # Check company country against target country from settings
    company_country = frappe.db.get_value("Company", doc.company, "country")
    target_country = digitax_settings.get("target_country") or "Kenya"
    logger.info(f"Company Country Check: {company_country}, Target: {target_country}")
    if not company_country == target_country:
        logger.info(f"Skipping: Company not in {target_country} (Country: {company_country})")
        return {"skipped": True, "reason": f"Company not in {target_country}"}
    
    # Check if company is enabled
    company_enabled = frappe.db.get_value("Company", doc.company, "custom_enable_company")
    logger.info(f"Company Enabled Check: {company_enabled}")
    if not company_enabled:
        logger.info(f"Skipping: Company Digitax integration not enabled")
        return {"skipped": True, "reason": "Company Digitax integration disabled"}

    # Check if invoice was already successfully sent to Digitax (use database to avoid stale data)
    sent_to_digitax, error_message, sale_id, digitax_status = frappe.db.get_value(
        "Sales Invoice",
        doc.name,
        ["custom_sent_to_digitax", "custom_error_message", "custom_sale_id", "custom_digitax_status"]
    ) or (0, None, None, None)
    
    if sent_to_digitax and not error_message:
        logger.info(f"Invoice already sent to Digitax successfully (Sale ID: {sale_id})")
        return {
            "skipped": True,
            "reason": "already_sent",
            "message": "Invoice was already sent to Digitax successfully",
            "id": sale_id,
            "status": digitax_status or "Sent"
        }

    # Increment retry count if this is a retry (error message exists)
    if doc.custom_error_message:
        current_retry_count = doc.custom_retry_count or 0
        logger.info(f"Retry detected: Current retry count = {current_retry_count}")
        frappe.db.set_value("Sales Invoice", doc.name, "custom_retry_count", current_retry_count + 1, update_modified=False)

    digitax_base_url, digitax_api_key = get_digitax_credentials()
    logger.info(f"Digitax Base URL: {digitax_base_url}")
    logger.info(f"API Key configured: {'Yes' if digitax_api_key else 'No'}")
    
    # Get API timeout from settings (ensure it's an int and positive)
    try:
        api_timeout = int(digitax_settings.get("api_request_timeout") or 30)
        if api_timeout <= 0:
            api_timeout = 30  # Fallback to default if invalid
    except (ValueError, TypeError):
        api_timeout = 30  # Fallback if conversion fails
    logger.info(f"API Request Timeout: {api_timeout} seconds")
    
    headers = {
        "accept": "application/json",
        "X-API-Key": digitax_api_key,
        "content-type": "application/json",
    }
    url = ""
    # Get invoice status codes from settings
    submitted_status = digitax_settings.get("submitted_invoice_status_code") or "02"
    cancelled_status = digitax_settings.get("cancelled_invoice_status_code") or "04"
    
    payload = {
        "trader_invoice_number": str(doc.custom_trader_invoice_number) or str(doc.name),
        "items": [],
        "invoice_status_code": submitted_status if doc.docstatus == 1 else cancelled_status,
        "customer_name": str(doc.customer_name),
    }
    
    # Only include callback_url if configured in settings
    callback_url = digitax_settings.get("callback_url")
    if callback_url and callback_url.strip():
        # Validate that callback URL is HTTPS
        if callback_url.startswith("https://"):
            payload["callback_url"] = callback_url
            logger.info(f"Using callback URL: {callback_url}")
        else:
            logger.warning(f"Callback URL is not HTTPS, skipping: {callback_url}")
    else:
        logger.info("No callback URL configured, skipping callback_url field")

    if str(doc.tax_id):
        payload["customer_tin"] = str(doc.tax_id)

    if not doc.is_return:
        url = f"{digitax_base_url.rstrip('/')}/sales"
        payload["sale_date"] = str(doc.posting_date)
        payload["receipt_type_code"] = digitax_settings.get("default_receipt_type_code") or "S"
        payload["payment_type_code"] = digitax_settings.get("default_payment_type_code") or "01"
        logger.info(f"Invoice Type: Regular Sales Invoice")
    else:
        url = f"{digitax_base_url.rstrip('/')}/credit-notes-with-barcode"
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

    # append_invoice_items_to_payload(doc, payload, doc.is_return)

    # Add School Fees as a single item using Digitax item ID
    # For credit notes, grand_total is negative, so we use abs() to get positive value
    amount = abs(doc.grand_total)
    logger.info(f"Calculated amount: {amount} (Original grand_total: {doc.grand_total})")
    
    # Get item code from settings to look up Digitax ID
    default_item_code = digitax_settings.get("default_item_bar_code") or "SCHOOL_FEES"
    item_description = digitax_settings.get("default_item_description") or "School Fees"
    
    logger.info(f"Looking up Digitax ID for item: {default_item_code}")
    
    # Get Digitax item ID from Item master
    digitax_item_id = frappe.db.get_value("Item", default_item_code, "custom_digitax_id")
    
    if not digitax_item_id:
        # Item has no Digitax ID - cannot send to Digitax
        error_msg = f"Item '{default_item_code}' has no Digitax ID. Please sync items from Digitax first."
        logger.error(f"SKIP INVOICE: {error_msg}")
        
        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            "custom_error_message",
            error_msg,
            update_modified=False
        )
        
        frappe.log_error(
            message=f"Cannot send Sales Invoice {doc.name} to Digitax: {error_msg}",
            title="Digitax - Missing Item ID"
        )
        
        return {
            "skipped": True,
            "reason": "missing_digitax_item_id",
            "message": error_msg
        }
    
    logger.info(f"Found Digitax item ID: {digitax_item_id}")
    
    # Build item payload using Digitax item ID
    new_item = {
        "id": digitax_item_id,
        "quantity": 1,
        "unit_price": amount,
        "total_amount": amount,
        "package_unit_quantity": 1,
        "discount_rate": 0,
        "discount_amount": 0,
        "item_description": item_description,
    }

    payload["items"].append(new_item)
    logger.info(f"Item added to payload: {new_item}")

    payload = json.dumps(payload)
    logger.info(f"Final API URL: {url}")
    logger.info(f"Payload: {payload}")

    try:
        logger.info(f"Sending POST request to Digitax API...")
        response = requests.post(url, headers=headers, data=payload, timeout=api_timeout)
        logger.info(f"Response Status Code: {response.status_code}")
        
        try:
            response_data = response.json()
            logger.info(f"Response Data: {json.dumps(response_data, indent=2)}")
        except Exception as json_error:
            logger.error(f"Failed to parse JSON response: {str(json_error)}")
            logger.error(f"Raw Response: {response.text}")
            response_data = {"error": "Invalid JSON response", "raw": response.text}
        
        if response.status_code >= 200 and response.status_code < 300:
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
                    "custom_error_message": "",  # Clear any previous errors
                },
                update_modified=False
            )
            frappe.db.commit()  # Commit immediately to prevent duplicate sends
            logger.info(f"Invoice fields updated and committed in ERPNext")
        elif response.status_code == 409:
            # 409 Conflict - Check if it's a duplicate trader_invoice_number
            error_message = response_data.get("message", "").lower()
            logger.info(f"409 Conflict received. Message: {response_data.get('message', '')}")
            
            # Check if message indicates duplicate trader_invoice_number
            is_duplicate = "trader_invoice_number has already been used" in error_message
            
            if is_duplicate:
                # Invoice already exists in Digitax - treat as success
                logger.info(f"DUPLICATE DETECTED: Invoice already exists in Digitax (409)")
                
                # Extract existing sale_id from metadata (if available)
                metadata = response_data.get("metadata", {})
                existing_sale_id = metadata.get("existing_sale_id", "")
                trader_invoice_number = metadata.get("trader_invoice_number", "")
                
                logger.info(f"Existing Sale ID: {existing_sale_id}, Trader Invoice Number: {trader_invoice_number}")
                
                # Update invoice with existing Digitax sale details
                response_data = update_invoice_with_existing_digitax_sale(doc, existing_sale_id, logger)
            else:
                # 409 for a different reason - treat as error
                logger.error(f"409 Conflict (NOT duplicate): {response_data.get('message', 'Unknown conflict')}")
                frappe.db.set_value(
                    "Sales Invoice",
                    doc.name,
                    "custom_error_message",
                    response_data.get("message", "Conflict error (409)"),
                    update_modified=False
                )
                logger.info(f"Error message saved to invoice")
        else:
            error_msg = response_data.get("message", "Unknown error")
            
            # Special handling for 400 Bad Request
            should_raise = True  # Flag to control whether to raise exception
            if response.status_code == 400:
                logger.error(f"400 BAD REQUEST: {error_msg}")
                logger.error(f"Request payload may have invalid data or duplicate item IDs")
                
                # Check if invoice already has Digitax data (use database to avoid stale data)
                existing_sale_id, existing_sent = frappe.db.get_value(
                    "Sales Invoice",
                    doc.name,
                    ["custom_sale_id", "custom_sent_to_digitax"]
                ) or (None, 0)
                
                if existing_sale_id and existing_sent:
                    logger.warning(
                        f"Invoice {doc.name} already has Digitax data (Sale ID: {existing_sale_id}). "
                        f"This 400 error is likely a duplicate submission attempt. Ignoring error."
                    )
                    # Don't overwrite existing successful sync, don't log error
                    should_raise = False
                    return {
                        "skipped": True,
                        "reason": "already_sent_400_ignored",
                        "message": f"Invoice already synced (Sale ID: {existing_sale_id}). 400 error ignored.",
                        "id": existing_sale_id
                    }
            else:
                logger.error(f"API ERROR: Status {response.status_code}, Message: {error_msg}")
            
            # Only save error message if we're going to raise the exception
            if should_raise:
                frappe.db.set_value(
                    "Sales Invoice",
                    doc.name,
                    "custom_error_message",
                    error_msg,
                    update_modified=False
                )
                logger.info(f"Error message saved to invoice")
        
        # Only raise for status codes that are actual errors (not 409 or handled 400s)
        if response.status_code >= 400 and response.status_code != 409:
            # Don't raise if we handled it specially above (check database for current state)
            existing_sale_id, existing_sent = frappe.db.get_value(
                "Sales Invoice",
                doc.name,
                ["custom_sale_id", "custom_sent_to_digitax"]
            ) or (None, 0)
            
            if not (response.status_code == 400 and existing_sale_id and existing_sent):
                response.raise_for_status()
        
        logger.info(f"Request completed successfully")
        
    except requests.exceptions.Timeout as e:
        logger.error(f"TIMEOUT ERROR: Request timed out after {api_timeout} seconds")
        error_msg = f"Request timeout - Digitax API did not respond within {api_timeout} seconds"
        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            "custom_error_message",
            error_msg,
            update_modified=False
        )
        response_data = {"error": "timeout", "message": error_msg}
        frappe.log_error(
            message=f"Timeout while sending Sales Invoice {doc.name} to Digitax",
            title="Digitax Timeout Error",
        )
        
    except requests.exceptions.RequestException as e:
        # Check if this is a 400 error for an already-sent invoice (don't log)
        is_400_already_sent = False
        digitax_response_body = None
        
        if hasattr(e, 'response') and e.response is not None:
            # Try to get the response body for debugging
            try:
                digitax_response_body = e.response.json()
                logger.error(f"Digitax 400 Response: {digitax_response_body}")
            except:
                digitax_response_body = e.response.text
                logger.error(f"Digitax 400 Response (text): {digitax_response_body}")
            
            if e.response.status_code == 400:
                existing_sale_id, existing_sent = frappe.db.get_value(
                    "Sales Invoice",
                    doc.name,
                    ["custom_sale_id", "custom_sent_to_digitax"]
                ) or (None, 0)
                
                logger.info(f"Checking if already sent: sale_id={existing_sale_id}, sent={existing_sent}")
                
                if existing_sale_id and existing_sent:
                    is_400_already_sent = True
                    logger.warning(
                        f"400 error for already-sent invoice {doc.name} (Sale ID: {existing_sale_id}). "
                        f"Ignoring error - invoice was already successfully synced."
                    )
                    response_data = {
                        "skipped": True,
                        "reason": "already_sent_400_ignored",
                        "id": existing_sale_id
                    }
        
        if not is_400_already_sent:
            logger.error(f"REQUEST ERROR: {str(e)}")
            error_msg = f"Request failed: {str(e)}"
            
            # Include Digitax response in error message if available
            if digitax_response_body:
                error_msg += f" | Digitax Response: {digitax_response_body}"
            
            frappe.db.set_value(
                "Sales Invoice",
                doc.name,
                "custom_error_message",
                error_msg[:500],  # Limit length
                update_modified=False
            )
            response_data = {"error": "request_failed", "message": error_msg}
            frappe.log_error(
                message=f"Request error while sending Sales Invoice {doc.name} to Digitax: {str(e)}\n\nDigitax Response: {digitax_response_body}",
                title="Digitax Request Error",
            )
        
    except Exception as e:
        logger.error(f"UNEXPECTED ERROR: {str(e)}")
        logger.error(f"Error Type: {type(e).__name__}")
        
        error_msg = str(e) if 'response_data' not in locals() else str(response_data)
        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            "custom_error_message",
            error_msg,
            update_modified=False
        )
        
        if 'response_data' not in locals():
            response_data = {"error": "exception", "message": str(e)}
            
        frappe.log_error(
            message=f"Error while sending Sales Invoice {doc.name} to Digitax: {error_msg}",
            title="Digitax Sales Invoice Sync Error",
        )
    finally:
        # Clear the cache flag to allow future sends
        cache_key = f"digitax_sending_{docname}"
        frappe.cache().delete(cache_key)
        
        frappe.db.commit()
        logger.info(f"=" * 80)
        logger.info(f"DIGITAX SEND: Process completed for {docname}")
        logger.info(f"=" * 80)
        return response_data

@frappe.whitelist()
def retry_sending_sales_invoice_to_digitax(invoice_name=None, company=None, from_date=None, to_date=None, retry_count=None):
    digitax_settings = frappe.get_single("Digitax Settings")
    target_country = digitax_settings.get("target_country") or "Kenya"
    
    # Get max retry attempts from settings if not provided (ensure it's an int and positive)
    if retry_count is None:
        try:
            retry_count = int(digitax_settings.get("max_retry_attempts") or 5)
            if retry_count <= 0:
                retry_count = 5  # Fallback to default if invalid
        except (ValueError, TypeError):
            retry_count = 5  # Fallback if conversion fails
    
    valid_companies = frappe.get_all("Company", filters={ "country": target_country, "custom_enable_company": 1 }, pluck="name")
    filters = {
        "docstatus": 1,
        "custom_sent_to_digitax": 0,
        "company": ["in", valid_companies],
        "custom_sent_to_digitax": 0,
        "custom_retry_count": ["<", retry_count],
    }
    if invoice_name:
        filters["name"] = invoice_name
    if from_date and to_date:
        filters["posting_date"] = ["between", [from_date, to_date]]
    if company:
        filters["company"] = company
    
    invoices = frappe.get_all("Sales Invoice", filters=filters, pluck="name")

    for invoice in invoices:
        send_sales_invoice_to_digitax(invoice)

@frappe.whitelist()
def job_retry_sending_sales_invoices():
    if not frappe.conf.get("sync_with_digitax"):
        frappe.msgprint("Digitax sync is disabled in site configuration")
        return {
            "skipped": True,
            "reason": "Digitax sync is disabled in site configuration"
        }
    
    # Get background job timeout from settings (ensure it's an int and positive)
    digitax_settings = frappe.get_single("Digitax Settings")
    try:
        job_timeout = int(digitax_settings.get("background_job_timeout") or 600)
        if job_timeout <= 0:
            job_timeout = 600  # Fallback to default if invalid
    except (ValueError, TypeError):
        job_timeout = 600  # Fallback if conversion fails
    
    frappe.enqueue(
        retry_sending_sales_invoice_to_digitax,
        queue="default",
        timeout=job_timeout,
    )

def fetch_sale_details_from_digitax(sale_id):
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
        digitax_base_url, digitax_api_key = get_digitax_credentials()
        
        if not digitax_base_url or not digitax_api_key:
            logger.error("Digitax credentials not configured")
            return None
        
        # Get API timeout from settings (ensure it's an int and positive)
        digitax_settings = frappe.get_single("Digitax Settings")
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
        sale_details = fetch_sale_details_from_digitax(existing_sale_id)
        
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

