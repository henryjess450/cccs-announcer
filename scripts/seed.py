"""First-run setup: folders, database, chimes, and a .env to edit.

Safe to run more than once. It never overwrites an existing .env or database.

    python scripts\\seed.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ensure_directories, load_config  # noqa: E402
from app.db import Database  # noqa: E402
from app.normalize import SEED_PRONUNCIATIONS  # noqa: E402
from scripts.make_chimes import generate  # noqa: E402


def main() -> int:
    env_path = ROOT / ".env"
    if not env_path.exists():
        shutil.copyfile(ROOT / ".env.example", env_path)
        print(f"Created {env_path}")
        print("  -> You probably do not need to change anything in it.")
    else:
        print(f"{env_path} already exists; leaving it alone.")

    config = load_config()
    ensure_directories(config)
    print(f"Data folder:  {config.data_dir}")

    database = Database(config.db_path)
    database.initialize()
    print(f"Database:     {config.db_path}")

    generate(config.chime_dir)

    print()
    print("Starter pronunciations built in (edit them in the admin panel in Phase 3):")
    for written, spoken in SEED_PRONUNCIATIONS.items():
        print(f"  {written:<14} -> {spoken}")

    from app.accounts import Accounts
    accounts = Accounts(database, config)
    account_count = accounts.count_users()

    print()
    print("Next:")
    print("  1. (optional) edit .env -- the defaults work for most installs")
    print("  2. python scripts\\show_address.py      to see the address for staff")
    if account_count == 0:
        print("  3. python scripts\\manage_users.py add --admin")
        print("     Nobody can sign in until this account exists.")
        print("  4. python run.py                          to start it")
        print("  5. Open http://localhost:8080, sign in, and press 'Check the speakers'")
    else:
        print(f"  3. python run.py     ({account_count} account(s) already exist)")
        print("  4. Open http://localhost:8080, sign in, and press 'Check the speakers'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
