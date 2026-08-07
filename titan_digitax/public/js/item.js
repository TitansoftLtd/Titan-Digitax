// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

frappe.ui.form.on('Item', {
	refresh: function(frm) {
		if (frm.doc.custom_digitax_item) {
			frm.add_custom_button(__('Sync to Digitax'), function() {
				sync_digitax_item(frm.doc.custom_digitax_item, frm);
			}, __('Digitax Actions'));
		}
	}
});

function sync_digitax_item(digitax_item_name, frm) {
	frappe.call({
		method: 'titan_digitax.titan_digitax.utils.digitax_item_sync.sync_digitax_item',
		args: { digitax_item_name: digitax_item_name },
		freeze: true,
		freeze_message: __('Syncing to Digitax...'),
		callback: function(r) {
			if (r.message) {
				frappe.show_alert({
					message: __('Digitax Item {0}: {1}', [digitax_item_name, r.message.status]),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			}
		}
	});
}
