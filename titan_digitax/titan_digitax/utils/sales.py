import frappe
import json
import requests
from .utils import get_digitax_credentials, get_digitax_callback_url_for_sales_with_items


# useful for other scenarios, when we need to append items to payload
def append_invoice_items_to_payload(doc, payload, is_return):
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
                # the item_class_code used here is for "services" as per Digitax documentation
                new_item["item_class_code"] = item.custom_item_class_code or "99020000"
                new_item["item_tax_type_code"] = item.custom_tax_type_code or "D"
                new_item["is_stockable"] = True if item.custom_is_stockable else False

            payload["items"].append(new_item)

@frappe.whitelist()
def send_sales_invoice_to_digitax(docname):
    if not frappe.conf.get("sync_with_digitax"):
        return
    
    doc = frappe.get_doc("Sales Invoice", docname)
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

    # Check company country
    company_country = frappe.db.get_value("Company", doc.company, "country")
    logger.info(f"Company Country Check: {company_country}")
    if not company_country == "Kenya":
        logger.info(f"Skipping: Company not in Kenya (Country: {company_country})")
        return {"skipped": True, "reason": "Company not in Kenya"}
    
    # Check if company is enabled
    company_enabled = frappe.db.get_value("Company", doc.company, "custom_enable_company")
    logger.info(f"Company Enabled Check: {company_enabled}")
    if not company_enabled:
        logger.info(f"Skipping: Company Digitax integration not enabled")
        return {"skipped": True, "reason": "Company Digitax integration disabled"}
    
    # Check if Digitax integration is enabled in Digitax Settings
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

    # Increment retry count if this is a retry (error message exists)
    if doc.custom_error_message:
        current_retry_count = doc.custom_retry_count or 0
        logger.info(f"Retry detected: Current retry count = {current_retry_count}")
        frappe.db.set_value("Sales Invoice", doc.name, "custom_retry_count", current_retry_count + 1, update_modified=False)

    digitax_base_url, digitax_api_key = get_digitax_credentials()
    logger.info(f"Digitax Base URL: {digitax_base_url}")
    logger.info(f"API Key configured: {'Yes' if digitax_api_key else 'No'}")
    
    headers = {
        "accept": "application/json",
        "X-API-Key": digitax_api_key,
        "content-type": "application/json",
    }
    url = ""
    payload = {
        "trader_invoice_number": str(doc.name),
        "items": [],
        "invoice_status_code": "02" if doc.docstatus == 1 else "04",
        "customer_name": str(doc.customer_name),
        "callback_url": get_digitax_callback_url_for_sales_with_items(),
    }

    if str(doc.tax_id):
        payload["customer_tin"] = str(doc.tax_id)

    if not doc.is_return:
        url = f"{digitax_base_url.rstrip('/')}/sales-with-items"
        payload["sale_date"] = str(doc.posting_date)
        payload["receipt_type_code"] = "S"
        payload["payment_type_code"] = "01"  # Cash, to be dynamic later. TODO: We need to revisit this.
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

    # Breaburn specific: Add School Fees as a single item
    # For credit notes, grand_total is negative, so we use abs() to get positive value
    amount = abs(doc.grand_total)
    logger.info(f"Calculated amount: {amount} (Original grand_total: {doc.grand_total})")
    
    new_item = {
        "item_bar_code": "SCHOOL_FEES",
        "quantity": 1,
        "unit_price": amount,
        "total_amount": amount,
        "package_unit_quantity": amount,
        "discount_rate": 0,
        "discount_amount": 0,
        "item_description": "School Fees",
    }

    if not doc.is_return:
        new_item["item_name"] = "School Fees"
        new_item["item_class_code"] = "99020000"
        new_item["item_tax_type_code"] = "D"
        new_item["is_stockable"] = False

    payload["items"].append(new_item)
    logger.info(f"Item added to payload: {new_item}")

    payload = json.dumps(payload)
    logger.info(f"Final API URL: {url}")
    logger.info(f"Payload: {payload}")

    try:
        logger.info(f"Sending POST request to Digitax API...")
        response = requests.post(url, headers=headers, data=payload, timeout=30)
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
                },
                update_modified=False
            )
            logger.info(f"Invoice fields updated in ERPNext")
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
            logger.error(f"API ERROR: Status {response.status_code}, Message: {error_msg}")
            
            frappe.db.set_value(
                "Sales Invoice",
                doc.name,
                "custom_error_message",
                error_msg,
                update_modified=False
            )
            logger.info(f"Error message saved to invoice")
        
        # Only raise for status codes that are actual errors (not 409 which we treat as success)
        if response.status_code >= 400 and response.status_code != 409:
            response.raise_for_status()
        
        logger.info(f"Request completed successfully")
        
    except requests.exceptions.Timeout as e:
        logger.error(f"TIMEOUT ERROR: Request timed out after 30 seconds")
        error_msg = "Request timeout - Digitax API did not respond"
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
        logger.error(f"REQUEST ERROR: {str(e)}")
        error_msg = f"Request failed: {str(e)}"
        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            "custom_error_message",
            error_msg,
            update_modified=False
        )
        response_data = {"error": "request_failed", "message": error_msg}
        frappe.log_error(
            message=f"Request error while sending Sales Invoice {doc.name} to Digitax: {str(e)}",
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
        frappe.db.commit()
        logger.info(f"=" * 80)
        logger.info(f"DIGITAX SEND: Process completed for {docname}")
        logger.info(f"=" * 80)
        return response_data

@frappe.whitelist()
def retry_sending_sales_invoice_to_digitax(invoice_name=None, company=None, from_date=None, to_date=None, retry_count=5):
    valid_companies = frappe.get_all("Company", filters={ "country": "Kenya", "custom_enable_company": 1 }, pluck="name")
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
        return
    
    frappe.enqueue(
        retry_sending_sales_invoice_to_digitax,
        queue="default",
        timeout=600,
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
        
        headers = {
            "accept": "application/json",
            "X-API-Key": digitax_api_key,
            "content-type": "application/json",
        }
        
        # Single endpoint for both invoices and credit notes
        url = f"{digitax_base_url.rstrip('/')}/sales/{sale_id}"
        
        logger.info(f"Fetching sale details from Digitax: {url}")
        
        response = requests.get(url, headers=headers, timeout=30)
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

