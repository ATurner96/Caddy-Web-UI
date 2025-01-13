import os
import json
import bcrypt
import zipfile
from flask import Flask, request, jsonify, render_template, redirect, session, send_from_directory
from app.utils import parse_caddyfile, update_caddyfile
from functools import wraps
import shutil
import secrets
import platform
import logging

USERS_FILE = os.path.join("app", "config", "users.json")
CONFIG_FILE = os.path.join("app", "config", "config.json")

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)

    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as file:
            json.dump({"first_run": True}, file)

    with open(CONFIG_FILE) as file:
        config = json.load(file)

    if "secret_key" not in config:
        config["secret_key"] = secrets.token_hex(32)
        with open(CONFIG_FILE, "w") as file:
            json.dump(config, file, indent=4)

    app.secret_key = config["secret_key"]
    app.config['CADDYFILE'] = config.get("caddyfile", "")

    @app.before_request
    def before_request():
        with open(CONFIG_FILE) as file:
            app.config['CURRENT_CONFIG'] = json.load(file)
            app.config['CADDYFILE'] = app.config['CURRENT_CONFIG'].get("caddyfile", "")

        if app.config['CURRENT_CONFIG'].get("first_run", True):
            allowed_endpoints = {"setup", "static", "list-root-directories"}
            if request.endpoint not in allowed_endpoints:
                return redirect("/setup")
        elif "username" not in session and request.endpoint not in {"login", "static", "list-root-directories"}:
            return redirect("/login")
        
    def get_site_root_dir(config):
        logger.debug(f"Parsing config for root: {config}")
        for line in config:
            if line.strip().startswith('root'):
                parts = line.strip().split()
                if len(parts) >= 3:
                    path = parts[2].rstrip('/')
                    logger.debug(f"Found root path: {path}")
                    return path
        return None

    def load_users():
        if not os.path.exists(USERS_FILE):
            return {}
        with open(USERS_FILE, "r") as file:
            return json.load(file)

    def save_users(users):
        with open(USERS_FILE, "w") as file:
            json.dump(users, file)

    users = load_users()

    def login_required(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if "username" not in session:
                return redirect("/login")
            return view(**kwargs)
        return wrapped_view

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if not users:
            if request.method == "POST":
                data = request.json
                username = data.get("username")
                password = data.get("password")

                if not username or not password:
                    return jsonify({"success": False, "error": "Username and password are required"}), 400

                hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                users[username] = hashed_password
                save_users(users)

                session["username"] = username
                return jsonify({"success": True, "message": "User created successfully."})

            return render_template("setup_user.html")

        if app.config['CURRENT_CONFIG'].get("first_run", True):
            if request.method == "POST":
                data = request.json
                caddyfile = data.get("caddyfile") or (r"C:\Caddy\Caddyfile" if platform.system() == "Windows" else "/etc/caddy/Caddyfile")
                port = data.get("port") or 5154

                caddyfile = os.path.normpath(caddyfile)

                if not os.path.isfile(caddyfile):
                    return jsonify({"success": False, "error": f"Caddyfile '{caddyfile}' does not exist."}), 400

                app.config['CURRENT_CONFIG'].update({
                    "caddyfile": caddyfile,
                    "port": int(port),
                    "first_run": False,
                })

                with open(CONFIG_FILE, "w") as file:
                    json.dump(app.config['CURRENT_CONFIG'], file, indent=4)

                app.config['CADDYFILE'] = caddyfile
                return jsonify({"success": True, "message": "Configuration saved successfully."})

            return render_template("setup_config.html")

        return redirect("/")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            data = request.json
            username = data.get("username")
            password = data.get("password")

            if username in users and bcrypt.checkpw(password.encode("utf-8"), users[username].encode("utf-8")):
                session["username"] = username
                return jsonify({"success": True})
            
            return jsonify({"success": False, "error": "Invalid username or password"})

        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.pop("username", None)
        return redirect("/login")
    

    @app.route("/")
    @login_required
    def home():
        try:
            sites = parse_caddyfile(app.config['CADDYFILE'])
            return render_template("index.html", sites=sites, get_site_root_dir=get_site_root_dir)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/reload-caddy", methods=["POST"])
    @login_required
    def reload_caddy():
        try:
            os.system(f"caddy fmt --overwrite {app.config['CADDYFILE']}")
            os.system(f"caddy reload --config {app.config['CADDYFILE']}")
            return jsonify({"success": True, "message": "Caddy reloaded successfully!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/add-site", methods=["POST"])
    @login_required
    def add_site():
        try:
            data = request.json
            domain = data["domain"]
            config = data["config"]

            sites = parse_caddyfile(app.config['CADDYFILE'])
            sites.append({"domain": domain, "config": config})
            update_caddyfile(app.config['CADDYFILE'], sites)

            root_dir = get_site_root_dir(config)
            if root_dir:
                os.makedirs(root_dir, exist_ok=True)

            os.system(f"caddy reload --config {app.config['CADDYFILE']}")
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/delete-site", methods=["POST"])
    @login_required
    def delete_site():
        try:
            data = request.json
            domain = data["domain"]
            sites = parse_caddyfile(app.config['CADDYFILE'])
            
            site = next((s for s in sites if s["domain"] == domain), None)
            if site:
                root_dir = get_site_root_dir(site["config"])
                if root_dir and os.path.exists(root_dir):
                    shutil.rmtree(root_dir)

            sites = [s for s in sites if s["domain"] != domain]
            update_caddyfile(app.config['CADDYFILE'], sites)
            os.system(f"caddy reload --config {app.config['CADDYFILE']}")
            return jsonify({"success": True, "message": f"Site '{domain}' deleted."})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/edit-site", methods=["POST"])
    @login_required
    def edit_site():
        try:
            data = request.json
            domain = data["domain"]
            new_config = data["config"]

            sites = parse_caddyfile(app.config['CADDYFILE'])
            for site in sites:
                if site["domain"] == domain:
                    old_root = get_site_root_dir(site["config"])
                    new_root = get_site_root_dir(new_config)
                    
                    if old_root != new_root and new_root:
                        if os.path.exists(old_root):
                            os.makedirs(new_root, exist_ok=True)
                            for item in os.listdir(old_root):
                                s = os.path.join(old_root, item)
                                d = os.path.join(new_root, item)
                                if os.path.isdir(s):
                                    shutil.copytree(s, d)
                                else:
                                    shutil.copy2(s, d)
                            shutil.rmtree(old_root)

                    site["config"] = new_config
                    break

            update_caddyfile(app.config['CADDYFILE'], sites)
            os.system(f"caddy reload --config {app.config['CADDYFILE']}")
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/list-files/<path:site_path>", methods=["GET"])
    @login_required
    def list_files(site_path):
        try:
            logger.debug(f"Caddyfile path: {app.config['CADDYFILE']}")
            sites = parse_caddyfile(app.config['CADDYFILE'])
            logger.debug(f"Parsed sites: {sites}")
            
            domain = site_path.split('/')[0]
            logger.debug(f"Looking for domain: {domain}")
            relative_path = '/'.join(site_path.split('/')[1:])
            logger.debug(f"Relative path: {relative_path}")

            site = next((s for s in sites if s["domain"] == domain), None)
            if not site:
                logger.error(f"Site not found for domain: {domain}")
                return jsonify({"success": False, "error": "Site not found"}), 404

            logger.debug(f"Found site config: {site}")
            root_dir = get_site_root_dir(site["config"])
            if not root_dir:
                logger.error("No root directory configured in site config")
                return jsonify({"success": False, "error": "No root directory configured"}), 400

            logger.debug(f"Root directory: {root_dir}")
            target_path = os.path.join(root_dir, relative_path)
            target_path = os.path.normpath(target_path)
            logger.debug(f"Target path: {target_path}")
            logger.debug(f"Path exists: {os.path.exists(target_path)}")

            if not os.path.exists(target_path):
                logger.debug(f"Creating directory: {target_path}")
                os.makedirs(target_path)

            items = []
            for entry in os.scandir(target_path):
                items.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": os.path.getsize(entry) if not entry.is_dir() else "-",
                    "modified": os.path.getmtime(entry),
                })
            logger.debug(f"Found items: {items}")
            return jsonify({"success": True, "files": items})
        except Exception as e:
            logger.error(f"Error listing files: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/upload/<path:site_path>", methods=["POST"])
    @login_required
    def upload_file(site_path):
        try:
            sites = parse_caddyfile(app.config['CADDYFILE'])
            parts = site_path.split("/", 1)
            domain = parts[0]
            relative_path = parts[1] if len(parts) > 1 else ""

            site = next((s for s in sites if s["domain"] == domain), None)
            if not site:
                return jsonify({"success": False, "error": "Site not found"}), 404

            root_dir = get_site_root_dir(site["config"])
            if not root_dir:
                return jsonify({"success": False, "error": "No root directory configured"}), 400

            full_path = os.path.normpath(os.path.join(root_dir, relative_path))
            os.makedirs(full_path, exist_ok=True)

            if "files" not in request.files:
                return jsonify({"success": False, "error": "No files in request"}), 400

            for file in request.files.getlist("files"):
                if file.filename:
                    file.save(os.path.join(full_path, file.filename))

            return jsonify({"success": True, "message": "Files uploaded successfully"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/upload-zip/<path:site_path>", methods=["POST"])
    @login_required
    def upload_zip(site_path):
        try:
            sites = parse_caddyfile(app.config['CADDYFILE'])
            parts = site_path.split("/", 1)
            domain = parts[0]
            relative_path = parts[1] if len(parts) > 1 else ""

            site = next((s for s in sites if s["domain"] == domain), None)
            if not site:
                return jsonify({"success": False, "error": "Site not found"}), 404

            root_dir = get_site_root_dir(site["config"])
            if not root_dir:
                return jsonify({"success": False, "error": "No root directory configured"}), 400

            full_path = os.path.normpath(os.path.join(root_dir, relative_path))
            os.makedirs(full_path, exist_ok=True)

            if "zip" not in request.files:
                return jsonify({"success": False, "error": "No ZIP file in request"}), 400

            zip_file = request.files["zip"]
            zip_path = os.path.join(full_path, zip_file.filename)
            zip_file.save(zip_path)

            try:
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(full_path)
                os.remove(zip_path)
                return jsonify({"success": True, "message": "ZIP extracted successfully"})
            except zipfile.BadZipFile:
                return jsonify({"success": False, "error": "Invalid ZIP file"}), 400
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        
    @app.route("/create-dir/<path:site_path>/<dirname>", methods=["POST"])
    @login_required
    def create_directory(site_path, dirname):
        try:
            sites = parse_caddyfile(app.config['CADDYFILE'])
            domain = site_path.split('/')[0]

            site = next((s for s in sites if s["domain"] == domain), None)
            if not site:
                return jsonify({"success": False, "error": "Site not found"}), 404

            root_dir = get_site_root_dir(site["config"])
            if not root_dir:
                return jsonify({"success": False, "error": "No root directory configured"}), 400

            new_dir_path = os.path.join(root_dir, dirname)
            new_dir_path = os.path.normpath(new_dir_path)
            
            os.makedirs(new_dir_path, exist_ok=True)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/delete-file/<path:site_path>", methods=["DELETE"])
    @login_required
    def delete_file(site_path):
        try:
            sites = parse_caddyfile(app.config['CADDYFILE'])
            parts = site_path.split("/", 1)
            domain = parts[0]
            relative_path = parts[1] if len(parts) > 1 else ""

            site = next((s for s in sites if s["domain"] == domain), None)
            if not site:
                return jsonify({"success": False, "error": "Site not found"}), 404

            root_dir = get_site_root_dir(site["config"])
            if not root_dir:
                return jsonify({"success": False, "error": "No root directory configured"}), 400

            file_path = os.path.normpath(os.path.join(root_dir, relative_path))

            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
            else:
                os.remove(file_path)
            return jsonify({"success": True, "message": "Deleted successfully"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/list-root-directories")
    @login_required
    def list_root_directories():
        try:
            root_path = request.args.get("path", None)

            if root_path:
                if not os.path.exists(root_path):
                    return jsonify({"success": False, "error": f"Path '{root_path}' does not exist"}), 404
                
                items = [{
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": os.path.getsize(entry) if entry.is_file() else "-",
                    "modified": os.path.getmtime(entry),
                } for entry in os.scandir(root_path)]

                return jsonify({"success": True, "path": root_path, "files": items})
            else:
                if platform.system() == "Windows":
                    drives = [f"{chr(d)}:\\" for d in range(65, 91) if os.path.exists(f"{chr(d)}:\\")]
                    return jsonify({"success": True, "path": "root", "files": [{"name": drive, "type": "directory"} for drive in drives]})
                else:
                    return jsonify({"success": True, "path": "root", "files": [{"name": "/", "type": "directory"}]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    return app

app = create_app()