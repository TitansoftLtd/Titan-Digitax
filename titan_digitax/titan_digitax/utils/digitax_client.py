# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""
Digitax API Client for pulling data FROM Digitax into ERPNext.
Reuses authentication and base URL from Digitax Settings.
"""

import frappe
import requests
from frappe import _


class DigitaxClient:
	"""Client for interacting with Digitax API to pull data."""
	
	def __init__(self):
		"""Initialize client with settings from Digitax Settings."""
		from frappe.utils.password import get_decrypted_password
		
		self.settings = frappe.get_single("Digitax Settings")
		
		# Validate required settings
		if not self.settings.base_url:
			frappe.throw(_("Digitax Base URL is not configured in Digitax Settings"))
		
		self.base_url = self.settings.base_url.rstrip("/")
		
		# Get decrypted API key (same method as sales.py)
		self.api_key = get_decrypted_password(
			"Digitax Settings", "Digitax Settings", "api_key"
		)
		
		if not self.api_key:
			frappe.throw(_("Digitax API Key is not configured in Digitax Settings"))
		
		self.timeout = int(self.settings.get("api_request_timeout") or 30)
	
	def _get_headers(self):
		"""Get common headers for Digitax API requests (same as sales.py)."""
		return {
			"accept": "application/json",
			"X-API-Key": self.api_key,
			"content-type": "application/json"
		}
	
	def _make_request(self, method, endpoint, data=None, params=None):
		"""
		Make HTTP request to Digitax API.
		
		Args:
			method: HTTP method (GET, POST, etc.)
			endpoint: API endpoint (e.g. "/customers")
			data: Request payload for POST/PUT
			params: Query parameters
		
		Returns:
			Response JSON
		"""
		url = f"{self.base_url}{endpoint}"
		headers = self._get_headers()

		try:
			response = requests.request(
				method=method,
				url=url,
				headers=headers,
				json=data,
				params=params,
				timeout=self.timeout
			)
			
			# Log request details
			frappe.logger().info(f"Digitax API {method} {endpoint}: Status {response.status_code}")
			
			# Handle errors
			if response.status_code >= 400:
				error_msg = f"Digitax API error: {response.status_code} - {response.text}"
				frappe.logger().error(error_msg)
				frappe.throw(error_msg)
			
			return response.json()
			
		except requests.exceptions.Timeout:
			frappe.throw(_("Digitax API request timed out after {0} seconds").format(self.timeout))
		except requests.exceptions.RequestException as e:
			frappe.throw(_("Digitax API request failed: {0}").format(str(e)))
		except Exception as e:
			frappe.throw(_("Unexpected error communicating with Digitax: {0}").format(str(e)))
	
	def fetch_customers(self, start_time=None, end_time=None):
		"""
		Fetch customers from Digitax.
		
		TODO: Implement when Digitax customer endpoint is provided.
		
		Args:
			start_time: Optional filter for customers modified after this time
			end_time: Optional filter for customers modified before this time
		
		Returns:
			List of customer records
		"""
		frappe.logger().info("fetch_customers called - endpoint not yet configured")
		
		# Placeholder return
		# When endpoint is provided, implement like:
		# params = {}
		# if start_time:
		#     params['start_time'] = start_time
		# if end_time:
		#     params['end_time'] = end_time
		# return self._make_request("GET", "/customers", params=params)
		
		return []
	
	def fetch_items(self, start_time=None, end_time=None):
		"""
		Fetch items from Digitax with pagination support.
		
		API Endpoint: GET /items
		Response Format: {"pagination": {"next": "item_id", "page_size": 20}, "data": [...items...]}
		
		Pagination:
		- API returns max 20 items per page
		- Use 'after' parameter to get next page: /items?after=item_id
		- Continue until page_size < 20 (indicates last page)
		
		Args:
			start_time: Optional filter for items modified after this time
			end_time: Optional filter for items modified before this time
		
		Returns:
			List of all item records from Digitax (across all pages)
		"""
		frappe.logger().info("Fetching items from Digitax (with pagination)...")
		
		# Build base query parameters
		params = {}
		if start_time:
			params['start_time'] = start_time
		if end_time:
			params['end_time'] = end_time
		
		all_items = []
		page_count = 0
		after_cursor = None
		
		while True:
			page_count += 1
			
			# Add pagination cursor if we're fetching subsequent pages
			if after_cursor:
				params['after'] = after_cursor
			
			frappe.logger().info(f"Fetching page {page_count} (after={after_cursor})...")
			
			# Call Digitax API
			response = self._make_request("GET", "/items", params=params)
			
			# Extract items and pagination info
			if not isinstance(response, dict):
				frappe.logger().warning(f"Unexpected response format on page {page_count}")
				break
			
			items = response.get('data', [])
			pagination = response.get('pagination', {})
			
			if not isinstance(items, list):
				frappe.logger().warning(f"No items in response on page {page_count}")
				break
			
			# Add items from this page
			all_items.extend(items)
			frappe.logger().info(f"Page {page_count}: Fetched {len(items)} items (total so far: {len(all_items)})")
			
			# Check if there are more pages
			page_size = pagination.get('page_size', 0)
			next_cursor = pagination.get('next')
			
			# Stop if page_size < 20 (last page) or no next cursor
			if page_size < 20:
				frappe.logger().info(f"Reached last page (page_size={page_size}). Total items: {len(all_items)}")
				break
			
			if not next_cursor:
				frappe.logger().info(f"No 'next' cursor in pagination. Total items: {len(all_items)}")
				break
			
			# Set cursor for next iteration
			after_cursor = next_cursor
			
			# Safety check: prevent infinite loops (max 1000 pages = 20,000 items)
			if page_count >= 1000:
				frappe.logger().warning(f"Reached max page limit (1000). Total items: {len(all_items)}")
				break
		
		frappe.logger().info(f"Completed fetching all items: {len(all_items)} items across {page_count} pages")
		return all_items
	
	def create_item(self, payload):
		"""
		Create a new item in Digitax.
		
		Args:
			payload (dict): Item data to send to Digitax
		
		Returns:
			dict: Digitax response with item ID, etims_item_code, status, etc.
				  Includes 'http_status_code' key with the HTTP status code
		"""
		frappe.logger().info(f"Creating item in Digitax: {payload.get('item_name')}")
		
		try:
			# Make direct request to get status code
			url = f"{self.base_url}/items"
			headers = self._get_headers()
			
			response = requests.post(
				url=url,
				headers=headers,
				json=payload,
				timeout=self.timeout
			)
			
			frappe.logger().info(f"Digitax item creation response: HTTP {response.status_code}")
			
			# Handle errors
			if response.status_code >= 400:
				error_msg = f"Digitax API error: {response.status_code} - {response.text}"
				frappe.logger().error(error_msg)
				frappe.throw(error_msg)
			
			# Parse response
			data = response.json()
			
			# Add HTTP status code to response for caller
			data['http_status_code'] = response.status_code
			
			frappe.logger().info(
				f"Item creation response: HTTP {response.status_code}, "
				f"ID: {data.get('id')}, ETIMS: {data.get('etims_item_code')}"
			)
			
			return data
			
		except requests.exceptions.Timeout:
			error_msg = f"Digitax API request timed out after {self.timeout} seconds"
			frappe.logger().error(error_msg)
			frappe.throw(error_msg)
		except requests.exceptions.RequestException as e:
			error_msg = f"Digitax API request failed: {str(e)}"
			frappe.logger().error(error_msg)
			frappe.throw(error_msg)
		except Exception as e:
			error_msg = f"Failed to create item in Digitax: {str(e)}"
			frappe.logger().error(error_msg)
			raise
