import os

def get_project_base_directory(*args):
    base = os.getenv("RAGFLOW_VENDOR_BASE") or os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    return os.path.join(base, *args) if args else base

def traversal_files(base):
    for root, _dirs, files in os.walk(base):
        for f in files:
            yield os.path.join(root, f)
