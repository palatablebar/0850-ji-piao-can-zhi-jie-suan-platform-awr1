import os

from flask import Flask, jsonify, render_template
from werkzeug.exceptions import RequestEntityTooLarge

from app.catalog import SPEC
from app.config import MAX_FILE, ROOT
from app.errors import DomainIssue, error_response
from app.rebooking.routes import bp as rebooking_bp
from app.rebooking.service import RebookService
from app.rebooking.store import RebookStore
from app.review.store import Store
from app.routes import api


def create_app(database=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE + 64 * 1024
    database_path = database or os.getenv("WORKBENCH_DB", str(ROOT / "instance" / "workbench.sqlite3"))
    app.extensions["runs"] = Store(database_path)
    rebook_store = RebookStore(database_path)
    app.extensions["rebooking_store"] = rebook_store
    app.extensions["allocation_store"] = rebook_store
    app.extensions["rebooking_service"] = RebookService(rebook_store)
    app.register_error_handler(DomainIssue, error_response)
    app.register_error_handler(RequestEntityTooLarge, lambda _: error_response(DomainIssue("FILE_TOO_LARGE", 413)))
    app.register_blueprint(api)
    app.register_blueprint(rebooking_bp)

    @app.get("/")
    def home():
        return render_template("index.html", p=SPEC)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()
