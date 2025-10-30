import frappe
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