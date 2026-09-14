"""Static-file routing hooks for the web application.

The SPA fallback is intentionally not registered by the initial application
factory. Later route composition can add it while keeping ``/api`` routes
outside its catch-all scope.
"""
