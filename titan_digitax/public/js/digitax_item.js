// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

frappe.ui.form.on('Digitax Item', {
	company: function(frm) {
		fill_company_defaults(frm);
	},

	refresh: function(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__('Sync to Digitax'), function() {
				sync_digitax_item(frm);
			}).addClass('btn-primary');
		}

		if (frm.doc.synced) {
			frm.page.set_indicator(__('Synced'), 'green');
		} else {
			frm.page.set_indicator(__('Not Synced'), 'orange');
		}
	}
});

// Fill only the empty fields with this company's Digitax Company Settings
// defaults, so the user sees them before saving. A company with no defaults
// fills nothing, leaving every required code for the user to enter.
function fill_company_defaults(frm) {
	if (!frm.is_new() || !frm.doc.company) {
		return;
	}
	frappe.call({
		method: 'titan_digitax.titan_digitax.doctype.digitax_item.digitax_item.get_company_item_defaults_for_form',
		args: { company: frm.doc.company },
		callback: function(r) {
			Object.entries(r.message || {}).forEach(([field, value]) => {
				if (!frm.doc[field]) {
					frm.set_value(field, value);
				}
			});
		}
	});
}

function sync_digitax_item(frm) {
	frappe.call({
		method: 'titan_digitax.titan_digitax.utils.digitax_item_sync.sync_digitax_item',
		args: { digitax_item_name: frm.doc.name },
		freeze: true,
		freeze_message: __('Syncing to Digitax...'),
		callback: function(r) {
			if (r.message) {
				frappe.show_alert({
					message: __('{0}: {1}', [frm.doc.name, r.message.status]),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			}
		}
	});
}
