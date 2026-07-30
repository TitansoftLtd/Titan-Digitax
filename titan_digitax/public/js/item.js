// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

frappe.ui.form.on('Item', {
	refresh: function(frm) {
		// Add "Sync to Digitax" button
		if (!frm.is_new()) {
			frm.add_custom_button(__('Sync to Digitax'), function() {
				sync_item_to_digitax(frm);
			}, __('Digitax Actions'));
		}
	}
});

function sync_item_to_digitax(frm) {
	// Check if already synced
	if (frm.doc.custom_digitax_id) {
		frappe.msgprint({
			title: __('Already Synced'),
			indicator: 'blue',
			message: __('This item is already synced to Digitax')
		});
		return;
	}
	
	// Confirm action
	frappe.confirm(
		__('Are you sure you want to sync this item to Digitax?'),
		function() {
			// User confirmed - proceed with sync
			frappe.call({
				method: 'titan_digitax.titan_digitax.utils.items.sync_item_to_digitax',
				args: {
					item_name: frm.doc.name
				},
				freeze: true,
				freeze_message: __('Syncing to Digitax...'),
				callback: function(r) {
					if (r.message && r.message.status === 'success') {
						frappe.show_alert({
							message: __(r.message.message),
							indicator: 'green'
						}, 5);
						
						// Reload form to show updated Digitax fields
						frm.reload_doc();
					}
				},
				error: function(r) {
					// Error already shown by frappe.throw
					frappe.show_alert({
						message: __('Failed to sync to Digitax. Check error log.'),
						indicator: 'red'
					}, 5);
				}
			});
		}
	);
}
