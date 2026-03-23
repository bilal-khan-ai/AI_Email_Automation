import sys
import os
import traceback

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from api.main import create_app
    app = create_app()
    print("App created successfully")
except Exception as e:
    traceback.print_exc()
    sys.exit(1)
