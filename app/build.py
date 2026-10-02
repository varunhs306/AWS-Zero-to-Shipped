"""Package both Lambda functions and the website into build/code.zip.

The API function serves the site from ./site and the API under /api/; the worker uses the same zip.
Usage: python build.py
"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = ["common.py", "api.py", "worker.py", "languages.py", "polish.py", "privacy.py"]


def main():
    out = os.path.join(HERE, "build", "code.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in BACKEND:
            z.write(os.path.join(HERE, "backend", name), name)
        frontend = os.path.join(HERE, "frontend")
        for root, _, files in os.walk(frontend):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, "site/" + os.path.relpath(full, frontend).replace(os.sep, "/"))
    with zipfile.ZipFile(out) as z:
        print(f"{out}: {len(z.namelist())} files, {os.path.getsize(out):,} bytes")


if __name__ == "__main__":
    main()
