app_name = "titan_digitax"
app_title = "Titan Digitax"
app_publisher = "Titansoft Limited"
app_description = "Titan Digitax"
app_email = "info@titansoft.africa"
app_license = "gpl-3.0"

# Apps
# ------------------

required_apps = ["frappe/erpnext"]

fixtures = [
    # export only those records that match the filters from the Role table
    {"dt": "Custom Field", "filters": { "module": "Titan Digitax" }},
]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "titan_digitax",
# 		"logo": "/assets/titan_digitax/logo.png",
# 		"title": "Titan Digitax",
# 		"route": "/titan_digitax",
# 		"has_permission": "titan_digitax.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/titan_digitax/css/titan_digitax.css"
# app_include_js = "/assets/titan_digitax/js/titan_digitax.js"

# include js, css files in header of web template
# web_include_css = "/assets/titan_digitax/css/titan_digitax.css"
# web_include_js = "/assets/titan_digitax/js/titan_digitax.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "titan_digitax/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    "Sales Invoice" : "public/js/sales_invoice.js"
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "titan_digitax/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "titan_digitax.utils.jinja_methods",
# 	"filters": "titan_digitax.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "titan_digitax.install.before_install"
# after_install = "titan_digitax.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "titan_digitax.uninstall.before_uninstall"
# after_uninstall = "titan_digitax.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "titan_digitax.utils.before_app_install"
# after_app_install = "titan_digitax.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "titan_digitax.utils.before_app_uninstall"
# after_app_uninstall = "titan_digitax.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "titan_digitax.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Sales Invoice": {
        "on_submit": "titan_digitax.titan_digitax.utils.sales_invoice.on_submit"
    }
}

# Scheduled Tasks
# ---------------

scheduler_events = {
    "cron": {
        "*/15 * * * *": [
            "titan_digitax.titan_digitax.utils.sales.job_retry_sending_sales_invoices"
        ]
    }
	# "all": [
	# 	"titan_digitax.tasks.all"
	# ],
	# "daily": [
	# 	"titan_digitax.tasks.daily"
	# ],
	# "hourly": [
	# 	"titan_digitax.tasks.hourly"
	# ],
	# "weekly": [
	# 	"titan_digitax.tasks.weekly"
	# ],
	# "monthly": [
	# 	"titan_digitax.tasks.monthly"
	# ],
}

# Testing
# -------

# before_tests = "titan_digitax.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "titan_digitax.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "titan_digitax.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["titan_digitax.utils.before_request"]
# after_request = ["titan_digitax.utils.after_request"]

# Job Events
# ----------
# before_job = ["titan_digitax.utils.before_job"]
# after_job = ["titan_digitax.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"titan_digitax.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

