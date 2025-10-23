import frappe
import requests
import json
from frappe.utils.password import get_decrypted_password

digitax_base_url = frappe.get_single("Digitax Settings").base_url
digitax_api_key = get_decrypted_password(
    "Digitax Settings", "Digitax Settings", "api_key"
)


def on_submit(doc, method):
    headers = {"accept": "application/json", "X-API-Key": digitax_api_key, "content-type": "application/json"}
    url = ""
    payload = {
        "items": [],
    }

    if not doc.is_return:
        url = f"{digitax_base_url.rstrip('/')}/sales-with-items"
        payload["sale_date"] = str(doc.posting_date)
        payload["trader_invoice_number"] = str(doc.name)
        payload["receipt_type_code"] = "S"
        payload["payment_type_code"] = "01"  # Cash, to be dynamic later. TODO: We need to revisit this.
        payload["invoice_status_code"] = "02" # Stands for "Approved"
        for item in doc.items:
            payload["items"].append(
                {
                    "item_name": item.item_name,
                    # the item_class_code used here is for "services" as per Digitax documentation
                    "item_class_code": item.custom_item_class_code or "99020000",
                    "item_bar_code": item.item_code,
                    "item_tax_type_code": item.custom_tax_type_code or "D",
                    "quantity": item.qty,
                    "unit_price": item.rate if item.rate > 0 else 0,
                    "total_amount": item.amount if item.amount > 0 else 0,
                    "package_unit_quantity": item.qty, #TODO: Confirm with Digitax if this is correct
                    # TODO: Confirm with Digitax if discount_rate is percentage or amount
                    "discount_rate": 1 if item.rate < 0 else 0,
                    "discount_amount": abs(item.amount) if item.amount < 0 else 0,  
                    "item_description": item.description,
                    "is_stockable": True if item.custom_is_stockable else False,
                }
            )
    else:
        url = f"{digitax_base_url.rstrip('/')}/credit-notes-with-barcode"

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
                "custom_sale_id": response_data.get("sale_id"),
                "custom_date": response_data.get("date"),
                "custom_time": response_data.get("time"),
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
