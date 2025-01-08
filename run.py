import json
from flask import Flask
from app.routes import create_routes


with open("app/config/config.json") as config_file:
    config = json.load(config_file)

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")

app.secret_key = config.get("secret_key", "default_fallback_key")

create_routes(app)

if __name__ == "__main__":
    port = config.get("port", 5000)
    app.run(host="0.0.0.0", port=port, debug=False)