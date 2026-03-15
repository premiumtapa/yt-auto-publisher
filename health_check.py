"""
Health Check Server
Runs alongside the Telegram webhook to provide a health endpoint at GET /
that UptimeRobot can ping to keep the Render free tier awake.
"""

import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    """Simple handler that responds 200 OK to GET / for health checks."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - YouTube Auto-Publisher Bot is running")

    def log_message(self, format, *args):
        # Suppress default request logging to reduce noise
        pass


def start_health_server(port: int):
    """Start the health check HTTP server in a background thread."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check server running on port {port}")
    return server
