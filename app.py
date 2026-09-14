"""WSGI entry point for the web application."""

from webapp import create_app
from webapp.legacy import register_legacy_routes

# Compatibility symbols are intentionally kept at this boundary only.  The
# application factory and its task API do not import or expose legacy state.
from api.answer import Tiku
from api.base import Account, Chaoxing, build_session
from api.cookies import account_cookie_path, load_cookie_file, save_cookie_file


app = create_app()
register_legacy_routes(app, symbol=lambda name: globals()[name])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
