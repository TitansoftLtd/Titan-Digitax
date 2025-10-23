import frappe
import requests
import json
from frappe.utils.password import get_decrypted_password

digitax_base_url = frappe.get_single("Digitax Settings").base_url
digitax_api_key = get_decrypted_password(
    "Digitax Settings", "Digitax Settings", "api_key"
)


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
                "package_unit_quantity": current_package_unit_quantity, #TODO: Confirm with Digitax if this is correct
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


def on_submit(doc, method):
    headers = {"accept": "application/json", "X-API-Key": digitax_api_key, "content-type": "application/json"}
    url = ""
    payload = {
        "trader_invoice_number": str(doc.name),
        "items": [],
    }

    if not doc.is_return:
        url = f"{digitax_base_url.rstrip('/')}/sales-with-items"
        payload["sale_date"] = str(doc.posting_date)
        payload["receipt_type_code"] = "S"
        payload["payment_type_code"] = "01"  # Cash, to be dynamic later. TODO: We need to revisit this.
        payload["invoice_status_code"] = "02" # Stands for "Approved"
    else:
        url = f"{digitax_base_url.rstrip('/')}/credit-notes-with-barcode"
        payload["return_date"] = str(doc.posting_date)
        payload["sale_id"] = doc.custom_sale_id

    append_invoice_items_to_payload(doc, payload, doc.is_return)

    payload = json.dumps(payload)

    try:
        response = requests.post(url, headers=headers, data=payload)
        response_data = response.json()
        if response.status_code >= 200 and response.status_code < 300:
            frappe.db.set_value("Sales Invoice", doc.name, {
                "custom_offline_url": response_data.get("offline_url"),
                "custom_sale_detail_url": response_data.get("sale_detail_url"),
                "custom_serial_number": response_data.get("serial_number"),
                "custom_invoice_number": response_data.get("invoice_number"),
                "custom_digitax_status": response_data.get("status"),
                "custom_sale_id": response_data.get("id"),
                "custom_date": response_data.get("date"),
                "custom_time": response_data.get("time"),
                "custom_receipt_type_code": response_data.get("receipt_type_code"),
                "custom_original_sale_id": response_data.get("original_sale_id"),
            })
            frappe.db.commit()
        else:
            frappe.db.set_value("Sales Invoice", doc.name, "custom_error_message", response_data.get("message", "Unknown error"))
            frappe.db.commit()
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        frappe.log_error(
            message=f"Error while sending Sales Invoice {doc.name} to Digitax: {str(e)}",
            title="Digitax Sales Invoice Sync Error",
        )