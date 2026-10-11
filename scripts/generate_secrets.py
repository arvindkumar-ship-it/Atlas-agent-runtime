import secrets
from pathlib import Path


def main():
    target = Path(".env")
    if target.exists():
        raise SystemExit(
            ".env already exists; keep it or remove it explicitly before generating new secrets"
        )
    content = Path(".env.example").read_text(encoding="utf-8")
    content = content.replace("ATLAS_API_KEY=\n", f"ATLAS_API_KEY={secrets.token_hex(32)}\n")
    content = content.replace(
        "ATLAS_DB_PASSWORD=\n", f"ATLAS_DB_PASSWORD={secrets.token_hex(24)}\n"
    )
    target.write_text(content, encoding="utf-8")
    target.chmod(0o600)
    print("Created .env. Read its API key locally and enter it in the console's API access dialog.")


if __name__ == "__main__":
    main()
