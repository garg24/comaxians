"""Lance le serveur en production avec Waitress (Windows, Linux, Raspberry Pi).

    python run.py              -> http://0.0.0.0:8080
    PORT=9000 python run.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

from waitress import serve  # noqa: E402
from app import app, ADMIN_PASSWORD  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    if ADMIN_PASSWORD == "axians":
        print("ATTENTION : mot de passe admin par défaut. Définissez COMAXIANS_ADMIN_PASSWORD.")
    print(f"Admin  : http://localhost:{port}/admin")
    print(f"Écran  : http://localhost:{port}/display")
    serve(app, host="0.0.0.0", port=port, threads=8)
