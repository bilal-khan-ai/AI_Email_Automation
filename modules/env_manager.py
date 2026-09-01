import os
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class EnvManager:
    """
    Utility class to safely read and update .env files.
    """
    
    def __init__(self, env_path: str = '.env'):
        self.env_path = env_path
        
    def read_env(self) -> Dict[str, str]:
        """Reads the .env file into a dictionary."""
        if not os.path.exists(self.env_path):
            return {}
            
        env_vars = {}
        with open(self.env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
        return env_vars
        
    def update_vars(self, new_vars: Dict[str, str]) -> bool:
        """Updates or adds variables in the .env file while preserving comments."""
        if not os.path.exists(self.env_path):
            with open(self.env_path, 'w', encoding='utf-8') as f:
                for k, v in new_vars.items():
                    f.write(f"{k}={v}\n")
            return True
            
        lines = []
        updated_keys = set()
        
        with open(self.env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key, _ = stripped.split('=', 1)
                key = key.strip()
                if key in new_vars:
                    new_lines.append(f"{key}={new_vars[key]}\n")
                    updated_keys.add(key)
                    continue
            new_lines.append(line)
            
        # Add any new keys that weren't in the file
        for key, value in new_vars.items():
            if key not in updated_keys:
                if new_lines and not new_lines[-1].endswith('\n'):
                    new_lines.append('\n')
                new_lines.append(f"{key}={value}\n")
                
        backup_path = self.env_path + ".bak"
        temp_path = None
        
        try:
            # Step 1: Write new content to a temporary file
            import tempfile
            import shutil
            
            # Windows: Try to remove restrictive attributes briefly if file exists
            if os.path.exists(self.env_path) and os.name == 'nt':
                try:
                    import subprocess
                    subprocess.run(['attrib', '-R', '-H', '-S', self.env_path], capture_output=True)
                # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix bare except on attribute update
                except Exception:
                    pass

            # Create temp file in same directory with restricted permissions
            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(self.env_path), prefix=".env_new_")
            try:
                # Set permissions (Owner read/write only)
                if os.name != 'nt':
                    os.chmod(temp_path, 0o600)
                
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                
                # Step 2: Create a backup of the current .env
                if os.path.exists(self.env_path):
                    shutil.copy2(self.env_path, backup_path)
                
                # Step 3: Replace current with new (Atomic on same filesystem)
                if os.name == 'nt' and os.path.exists(self.env_path):
                    # Windows doesn't always allow os.replace if another handle is open
                    # We'll use a slightly safer move
                    os.remove(self.env_path)
                
                os.rename(temp_path, self.env_path)
                
                # Step 4: Success - Cleanup backup
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                    
                logger.info(f"✅ Transactional .env update successful ({len(new_vars)} keys)")
                return True
                
            except Exception as inner_e:
                if 'fd' in locals():
                    try:
                        os.close(fd)
                    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix bare except on fd close
                    except OSError:
                        pass
                
                # ROLLBACK: If it failed, try to restore from backup
                if os.path.exists(backup_path) and not os.path.exists(self.env_path):
                    os.rename(backup_path, self.env_path)
                
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
                raise inner_e

        except Exception as e:
            logger.error(f"❌ Transactional .env update failed: {e}")
            # Ensure we don't leave mess
            for p in [temp_path, backup_path]:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix bare except on cleanup
                    except OSError:
                        pass
            return False
