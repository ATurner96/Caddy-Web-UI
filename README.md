
# Caddy Web UI

Caddy Web UI is a user-friendly interface for managing Caddy server configurations. The application allows users to create, edit, and delete site configurations, manage files associated with these sites, and reload the Caddy server directly from the web interface.

## Features
- Add, edit, and delete site configurations.
- File management with uploads, deletions, and ZIP extraction.
- Reload Caddy server configurations.
- User authentication and session management.
- Dynamic context menu for file explorer.

## Prerequisites
- Python 3.8 or higher
- Flask framework
- Caddy server installed on the host system

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/your-repo/caddy-web-ui.git
   cd caddy-web-ui
   ```

2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure the application by editing `app/config/config.json`:
   - Update the `base_dir`, `caddyfile`, `port`, and `secret_key` as needed.

4. Run the application:
   ```bash
   python run.py
   ```

5. Open your browser and navigate to `http://localhost:5154` (or the configured port).

## Directory Structure
```
.
├── app/
│   ├── config/
│   │   └── config.json
│   ├── static/
│   │   └── styles.css
│   ├── templates/
│   │   ├── login.html
│   │   ├── setup.html
│   │   └── index.html
│   ├── routes.py
│   └── utils.py
├── run.py
├── requirements.txt
└── README.md
```

## Development

To enable debugging, set `debug=True` in `run.py`.

## License
This project is licensed under the GNU General Public License v3.0.

For more details, see the [LICENSE.txt](LICENSE.txt) file or visit the [GNU website](https://www.gnu.org/licenses/gpl-3.0.html).
