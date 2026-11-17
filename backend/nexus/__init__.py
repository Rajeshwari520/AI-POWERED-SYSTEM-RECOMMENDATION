"""NEXUS AI — Flask REST API (application factory).

The React frontend (../frontend) talks to this API over HTTP.
"""
from datetime import timedelta

from flask import Flask, jsonify, request, send_from_directory

from .config import Config
from .db import close_db
from .services import build_engine


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.permanent_session_lifetime = timedelta(days=30)
    app.teardown_appcontext(close_db)

    from .routes import admin, ai, auth, shop
    for bp in (auth.bp, shop.bp, ai.bp, admin.bp):
        app.register_blueprint(bp)

    build_engine(app)

    # ---- CORS: let the React app (another port) call the API with cookies
    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin and origin in app.config["FRONTEND_ORIGINS"]:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            resp.headers["Vary"] = "Origin"
        return resp

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def preflight(_any):
        return ("", 204)

    @app.get("/")
    def index():
        return jsonify(name="NEXUS AI API", status="running",
                       frontend="Run the React app in ../frontend (npm run dev) and open http://localhost:5173",
                       docs="See README.md for every endpoint")

    @app.get("/api/images/<path:filename>")
    def product_image(filename):
        return send_from_directory(app.config["IMAGE_DIR"], filename, max_age=3600)

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(error="Not found"), 404

    @app.errorhandler(500)
    def server_error(_e):
        return jsonify(error="Something went wrong on the server"), 500

    return app
