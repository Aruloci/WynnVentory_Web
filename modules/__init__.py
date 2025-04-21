from flask import Flask, redirect, url_for
from decouple import config as env_config

from modules.auth import require_api_key, record_api_usage
from modules.config import Config
from modules.db import get_client


def create_app():
    Config.ENVIRONMENT = env_config("ENVIRONMENT", default="prod")
    Config.MIN_SUPPORTED_VERSION = env_config("MIN_SUPPORTED_VERSION", default="0.0.0")

    app = Flask(__name__,
                static_url_path='',
                static_folder='modules/routes/web/static',
                template_folder='modules/routes/web/templates')

    # WEB ROUTES
    from modules.routes.web.web import web_bp
    app.register_blueprint(web_bp)

    # WEB ROUTES
    from modules.routes.api.item import item_bp
    from modules.routes.api.aspect import aspect_bp
    from modules.routes.api.lootpool import lootpool_bp
    from modules.routes.api.raidpool import raidpool_bp
    from modules.routes.api.market import market_bp

    for bp in (item_bp, aspect_bp, lootpool_bp, raidpool_bp, market_bp):
        bp.before_request(require_api_key)
        bp.after_request(record_api_usage)
        app.register_blueprint(bp)

    app.logger.warning(
        "Successfully started in '%s' mode with min supported version '%s'",
        Config.ENVIRONMENT,
        Config.MIN_SUPPORTED_VERSION,
    )

    # 404 Error
    @app.errorhandler(404)
    def page_not_found():
        return redirect(url_for('web.index'), 404)

    return app
