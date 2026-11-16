"""Serves uploaded company logos (only file names the app itself generated)."""

from flask import Blueprint, abort, current_app, send_from_directory

from app.services.uploads import STORED_NAME_PATTERN

bp = Blueprint("media", __name__)


@bp.route("/media/logos/<filename>")
def logo(filename):
    if not STORED_NAME_PATTERN.match(filename):
        abort(404)
    # send_from_directory also refuses any path that tries to leave the folder.
    response = send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
    # Stop the browser from treating the file as anything but an image.
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
