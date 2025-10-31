import frappe
import json
from frappe.utils.password import get_decrypted_password

def get_digitax_credentials():
    try:
        digitax_base_url = frappe.get_single("Digitax Settings").base_url
        digitax_api_key = get_decrypted_password(
            "Digitax Settings", "Digitax Settings", "api_key"
        )
        return digitax_base_url, digitax_api_key
    except Exception as e:
        frappe.msgprint("Error fetching Digitax credentials. Check error log for details.")
        frappe.log_error(f"Error fetching Digitax credentials: {str(e)}", "Digitax Utils")
        return None, None
    
@frappe.whitelist(allow_guest=True, methods=["POST"])
def digitax_callback_sales_with_items():
    try:
        # Get the request data
        if frappe.request.content_type == "application/json":
            data = frappe.request.get_json()
        else:
            data = frappe.form_dict

        # Log the received data for debugging
        frappe.log_error(
            title="Digitax Callback Received",
            message=json.dumps(data, indent=2)
        )
    except Exception as e:
        frappe.log_error(
            title="Digitax Callback Error",
            message=frappe.get_traceback()
        )
        return {
            "status": "error",
            "message": str(e)
        }


def get_digitax_callback_url_for_sales_with_items():
    site_url = frappe.utils.get_url()
    callback_path = "/api/method/titan_digitax.titan_digitax.utils.utils.digitax_callback_sales_with_items"
    return f"{site_url}{callback_path}"