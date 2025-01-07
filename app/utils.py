import os

def parse_caddyfile(caddyfile_path):
    """Parse the Caddyfile and extract sites with their configurations."""
    sites = []
    with open(caddyfile_path, "r") as file:
        lines = file.readlines()

    current_site = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.endswith("{"):
            current_site = {"domain": stripped[:-1].strip(), "config": []}
        elif stripped == "}":
            if current_site is not None:
                sites.append(current_site)
            current_site = None
        else:
            if current_site is not None:
                current_site["config"].append(stripped)

    return sites



def update_caddyfile(caddyfile_path, sites):
    """Write updated sites back to the Caddyfile."""
    with open(caddyfile_path, "w") as file:
        for site in sites:
            file.write(f"{site['domain']} {{\n")
            for line in site["config"]:
                file.write(f"    {line}\n")
            file.write("}\n")

