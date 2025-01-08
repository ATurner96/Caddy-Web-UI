import os
import json
import bcrypt
import zipfile
from flask import request, jsonify, render_template, redirect, session, send_from_directory
from app.utils import parse_caddyfile, update_caddyfile
from functools import wraps
import shutil
import secrets  # For generating a secure secret key
import app
import platform


USERS_FILE = os.path.join("app", "config", "users.json")
CONFIG_FILE = os.path.join("app", "config", "config.json")
UPLOAD_DIR = "/var/www/caddy-sites"

# Load the configuration
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w") as file:
        json.dump({"first_run": True}, file)

with open(CONFIG_FILE) as file:
    config = json.load(file)

# Generate a secure session key if not already set
if "secret_key" not in config:
    config["secret_key"] = secrets.token_hex(32)
    with open(CONFIG_FILE, "w") as file:
        json.dump(config, file, indent=4)

# Flask app secret key
app.secret_key = config["secret_key"]

def load_users():
    """Load users from the JSON file."""
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as file:
        return json.load(file)

def save_users(users):
    """Save users to the JSON file."""
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

def create_routes(app):
    config_path = os.path.join("app", "config", "config.json")
    with open(config_path) as config_file:
        config = json.load(config_file)
    
    BASE_DIR = config["base_dir"]
    CADDYFILE = config["caddyfile"]

    @app.before_request
    def check_first_run():
        """Redirect to setup page if first_run is True."""
        print(f"Request Endpoint: {request.endpoint}")
        print(f"Request Path: {request.path}")
        print(f"First Run: {config.get('first_run', True)}")
        print(f"Session Username: {session.get('username')}")

        if config.get("first_run", True):
            # Allow specific endpoints during setup
            allowed_endpoints = {"setup", "static", "list-root-directories"}
            if request.endpoint not in allowed_endpoints:
                # Allow authenticated users to access other endpoints
                if "username" not in session:
                    print("Redirecting to /setup")
                    return redirect("/setup")
            return  # Prevent further processing for allowed endpoints

        # Enforce login for all other routes after setup
        if "username" not in session and request.endpoint not in {"login", "static", "list-root-directories"}:
            print("Redirecting to /login")
            return redirect("/login")





    def update_config(key, value):
        """Helper function to update the config file."""
        config[key] = value
        with open(config_path, "w") as config_file:
            json.dump(config, config_file, indent=4)

    # Setup Route
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        """Initial setup to create the first user and configure settings."""
        # Stage 1: Create Admin User
        if not users:
            if request.method == "POST":
                data = request.json
                username = data.get("username")
                password = data.get("password")

                if not username or not password:
                    return jsonify({"success": False, "error": "Username and password are required"}), 400

                # Hash the password and save the user
                hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                users[username] = hashed_password
                save_users(users)

                # Create session for the user
                session["username"] = username
                return jsonify({"success": True, "message": "User created successfully. Session started."})

            return render_template("setup_user.html")

        # Stage 2: Configure Settings
        if config.get("first_run", True):
            if request.method == "POST":
                data = request.json
                base_dir = data.get("base_dir") or (r"C:\Caddy" if platform.system() == "Windows" else "/var/www/caddy-web-ui")
                caddyfile = data.get("caddyfile") or (r"C:\Caddy\Caddyfile" if platform.system() == "Windows" else "/etc/caddy/Caddyfile")
                port = data.get("port") or 5154

                # Normalize paths
                base_dir = os.path.normpath(base_dir)
                caddyfile = os.path.normpath(caddyfile)

                # Validate paths
                if not os.path.exists(base_dir):
                    return jsonify({"success": False, "error": f"Base directory '{base_dir}' does not exist."}), 400
                if not os.path.isfile(caddyfile):
                    return jsonify({"success": False, "error": f"Caddyfile '{caddyfile}' does not exist."}), 400

                # Update configuration
                config.update({
                    "base_dir": base_dir,
                    "caddyfile": caddyfile,
                    "port": int(port),
                    "first_run": False,
                })

                with open(CONFIG_FILE, "w") as file:
                    json.dump(config, file, indent=4)

                return jsonify({"success": True, "message": "Configuration saved successfully. Setup complete."})

            return render_template("setup_config.html")

        # If setup is complete, redirect to home
        return redirect("/")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Render login page or handle login submission."""
        if request.method == "POST":
            data = request.json
            username = data.get("username")
            password = data.get("password")

            if username in users and bcrypt.checkpw(password.encode("utf-8"), users[username].encode("utf-8")):
                session["username"] = username
                return jsonify({"success": True})
            
            return jsonify({"success": False, "error": "Invalid username or password"})

        return render_template("login.html")

    @app.route("/logout", methods=["GET"])
    def logout():
        """Log out the user."""
        session.pop("username", None)
        return redirect("/login")

    @app.route("/")
    @login_required
    def home():
        """Display the homepage with a list of sites."""
        sites = parse_caddyfile(CADDYFILE)
        return render_template("index.html", sites=sites)

    @app.route("/reload-caddy", methods=["POST"])
    @login_required
    def reload_caddy():
        """Reload the Caddy server."""
        try:
            os.system("caddy fmt --overwrite /etc/caddy/Caddyfile")
            os.system("caddy reload --config /etc/caddy/Caddyfile")
            return jsonify({"success": True, "message": "Caddy reloaded successfully!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/add-site", methods=["POST"])
    @login_required
    def add_site():
        """Add a new site to the Caddyfile."""
        data = request.json
        domain = data["domain"]
        config = data["config"]

        sites = parse_caddyfile(CADDYFILE)

        sites.append({"domain": domain, "config": config})
        
        update_caddyfile(CADDYFILE, sites)

        os.makedirs(os.path.join(UPLOAD_DIR, domain), exist_ok=True)
        os.system("caddy reload")
        return jsonify({"success": True})
    
    @app.route("/delete-site", methods=["POST"])
    @login_required
    def delete_site():
        """Delete a site from the Caddyfile."""
        data = request.json
        domain = data["domain"]

        sites = parse_caddyfile(CADDYFILE)

        sites = [site for site in sites if site["domain"] != domain]

        update_caddyfile(CADDYFILE, sites)

        site_dir = os.path.join(UPLOAD_DIR, domain)
        if os.path.exists(site_dir):
            import shutil
            shutil.rmtree(site_dir)

        os.system("caddy reload")
        return jsonify({"success": True, "message": f"Site '{domain}' deleted successfully!"})


    @app.route("/edit-site", methods=["POST"])
    @login_required
    def edit_site():
        """Edit an existing site's configuration."""
        data = request.json
        domain = data["domain"]
        new_config = data["config"]

        sites = parse_caddyfile(CADDYFILE)

        for site in sites:
            if site["domain"] == domain:
                site["config"] = new_config
                break

        update_caddyfile(CADDYFILE, sites)

        os.system("caddy reload")
        return jsonify({"success": True})
    
    @app.route("/rename-file", methods=["POST"])
    @login_required
    def rename_file():
        """Rename a file or folder."""
        data = request.json
        current_name = data.get("currentName").lstrip("/")
        new_name = data.get("newName").lstrip("/")

        current_path = os.path.join(UPLOAD_DIR, current_name)
        new_path = os.path.join(UPLOAD_DIR, new_name)

        print(f"DEBUG: Received request to rename file.")
        print(f"DEBUG: Current Path: {current_path}")
        print(f"DEBUG: New Path: {new_path}")
        print(f"DEBUG: Current Name: {current_name}, New Name: {new_name}")

        if not os.path.exists(current_path):
            print("DEBUG: Current path does not exist.")
            return jsonify({"success": False, "error": "File or folder does not exist."}), 404

        try:
            os.rename(current_path, new_path)
            print(f"DEBUG: Successfully renamed '{current_path}' to '{new_path}'")
            return jsonify({"success": True, "message": "File or folder renamed successfully."})
        except Exception as e:
            print(f"DEBUG: Rename error: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500




    
    @app.route("/edit-file/<path:site_path>/<filename>", methods=["GET"])
    @login_required
    def get_file_content(site_path, filename):
        """Fetch the content of a file."""
        file_path = os.path.join(UPLOAD_DIR, site_path, filename)

        if not os.path.exists(file_path):
            return jsonify({"success": False, "error": "File not found"}), 404

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
            return jsonify({"success": True, "content": content})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500


    @app.route("/save-file/<path:site_path>/<filename>", methods=["POST"])
    @login_required
    def save_file_content(site_path, filename):
        """Save updated file content."""
        file_path = os.path.join(UPLOAD_DIR, site_path, filename)

        if not os.path.exists(file_path):
            return jsonify({"success": False, "error": "File not found"}), 404

        try:
            content = request.json.get("content", "")
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content)
            return jsonify({"success": True, "message": "File saved successfully!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/upload/<path:site_path>", methods=["POST"])
    @login_required
    def upload_file(site_path):
        parts = site_path.split("/", 1)
        domain = parts[0]
        relative_path = parts[1] if len(parts) > 1 else ""

        full_path = os.path.join(UPLOAD_DIR, domain, relative_path)
        full_path = os.path.normpath(full_path)

        os.makedirs(full_path, exist_ok=True)

        if "files" not in request.files:
            return jsonify({"success": False, "error": "No files part in the request"}), 400

        files = request.files.getlist("files")
        for file in files:
            if file.filename:
                file.save(os.path.join(full_path, file.filename))

        return jsonify({"success": True, "message": "Files uploaded successfully!", "path": relative_path})

    @app.route("/upload-zip/<path:site_path>", methods=["POST"])
    @login_required
    def upload_zip(site_path):
        """Upload and extract a ZIP file."""
        parts = site_path.split("/", 1)
        domain = parts[0]
        relative_path = parts[1] if len(parts) > 1 else ""

        site_path = os.path.join(UPLOAD_DIR, domain, relative_path)
        site_path = os.path.normpath(site_path)

        os.makedirs(site_path, exist_ok=True)

        if "zip" not in request.files:
            return jsonify({"success": False, "error": "No ZIP file in the request"}), 400

        zip_file = request.files["zip"]
        zip_path = os.path.join(site_path, zip_file.filename)
        zip_file.save(zip_path)

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(site_path)
            os.remove(zip_path)
        except zipfile.BadZipFile:
            return jsonify({"success": False, "error": "Invalid ZIP file"}), 400

        return jsonify({"success": True, "message": "ZIP file uploaded and extracted!"})



    @app.route("/delete-file/<path:site_path>", methods=["DELETE"])
    @login_required
    def delete_file(site_path):
        parts = site_path.split("/", 1)
        domain = parts[0]
        relative_path = parts[1] if len(parts) > 1 else ""

        file_path = os.path.normpath(os.path.join(UPLOAD_DIR, domain, relative_path))

        print(f"DEBUG: Deleting file at path: {file_path}")

        if not os.path.exists(file_path):
            return jsonify({"success": False, "error": f"File not found: {file_path}"}), 404

        try:
            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
            else:
                os.remove(file_path)

            return jsonify({"success": True, "message": "Deleted successfully!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500



    @app.route("/create-dir/<path:site_path>/<dirname>", methods=["POST"])
    @login_required
    def create_directory(site_path, dirname):
        """Create a new directory."""
        directory_path = os.path.join(UPLOAD_DIR, site_path, dirname)
        try:
            os.makedirs(directory_path, exist_ok=True)
            return jsonify({"success": True, "message": "Directory created successfully!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500


    @app.route("/list-files/<path:site_path>", methods=["GET"])
    @login_required
    def list_files(site_path):
        """List files and directories in the specified site path."""
        site_dir = os.path.join(UPLOAD_DIR, site_path)

        if not os.path.exists(site_dir):
            return jsonify({"success": False, "error": f"Directory '{site_dir}' does not exist"}), 404

        try:
            items = []
            for entry in os.scandir(site_dir):
                items.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": os.path.getsize(entry) if not entry.is_dir() else "-",
                    "modified": os.path.getmtime(entry),
                })

            return jsonify({"success": True, "files": items})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        
    @app.route("/list-root-directories", methods=["GET"])
    @login_required
    def list_root_directories():
        """List root directories or the contents of a given path."""
        root_path = request.args.get("path", None)

        try:
            if root_path:
                # List contents of the provided directory
                if not os.path.exists(root_path):
                    return jsonify({"success": False, "error": f"Path '{root_path}' does not exist"}), 404
                
                items = []
                for entry in os.scandir(root_path):
                    items.append({
                        "name": entry.name,
                        "type": "directory" if entry.is_dir() else "file",
                        "size": os.path.getsize(entry) if entry.is_file() else "-",
                        "modified": os.path.getmtime(entry),
                    })

                return jsonify({"success": True, "path": root_path, "files": items})
            else:
                # List root directories
                if platform.system() == "Windows":
                    # On Windows, root directories are the drives
                    drives = [f"{chr(d)}:\\" for d in range(65, 91) if os.path.exists(f"{chr(d)}:\\")]
                    return jsonify({"success": True, "path": "root", "files": [{"name": drive, "type": "directory"} for drive in drives]})
                else:
                    # On Unix-like systems, the root directory is "/"
                    return jsonify({"success": True, "path": "root", "files": [{"name": "/", "type": "directory"}]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

