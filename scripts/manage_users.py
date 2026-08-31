"""Create and manage staff accounts from the command line.

The admin page in the browser does the same things, but you need this one at
least once -- to make the very first administrator, before anyone can sign in.

    python scripts\\manage_users.py list
    python scripts\\manage_users.py add --admin
    python scripts\\manage_users.py add
    python scripts\\manage_users.py reset hjess
    python scripts\\manage_users.py disable hjess
    python scripts\\manage_users.py enable hjess
    python scripts\\manage_users.py unlock hjess

Passwords are never shown twice and are never stored in a readable form. Write
the new one down when it is printed.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.accounts import Accounts, ROLE_ADMIN, ROLE_STAFF, password_problem  # noqa: E402
from app.config import ensure_directories, load_config  # noqa: E402
from app.db import Database  # noqa: E402
from app.security import generate_password  # noqa: E402


def open_accounts() -> Accounts:
    config = load_config()
    ensure_directories(config)
    database = Database(config.db_path)
    database.initialize()
    return Accounts(database, config)


def ask_password() -> str:
    """Ask twice, or offer to generate one."""
    while True:
        first = getpass.getpass("New password (leave blank to generate one): ")
        if not first:
            generated = generate_password()
            print(f"\n  Generated password: {generated}")
            print("  Write this down now. It will not be shown again.\n")
            return generated
        problem = password_problem(first)
        if problem:
            print(f"  {problem}")
            continue
        second = getpass.getpass("Type it again: ")
        if first != second:
            print("  Those did not match. Try again.")
            continue
        return first


def cmd_list(accounts: Accounts, args) -> int:
    users = accounts.list_users()
    if not users:
        print("There are no accounts yet. Create the first administrator with:")
        print("    python scripts/manage_users.py add --admin")
        return 0
    print(f"{'ID':>4}  {'USERNAME':<18} {'NAME':<24} {'ROLE':<7} {'STATUS'}")
    print("-" * 74)
    for user in users:
        status = "active" if user["is_active"] else "OFF"
        if user["locked_until"]:
            status += " (locked)"
        if user["must_change_password"]:
            status += " (must change password)"
        print(f"{user['id']:>4}  {user['username']:<18} {user['display_name']:<24} "
              f"{user['role']:<7} {status}")
    return 0


def cmd_add(accounts: Accounts, args) -> int:
    username = args.username or input("Username (what they type to sign in): ").strip()
    display = args.name or input("Full name (what the school sees): ").strip()
    if not username:
        print("A username is required.")
        return 1
    role = ROLE_ADMIN if args.admin else ROLE_STAFF
    password = ask_password()
    try:
        user = accounts.create_user(
            username=username, display_name=display or username,
            password=password, role=role, must_change_password=not args.no_password_change,
        )
    except ValueError as exc:
        print(f"{exc}")
        return 1
    print(f"Created {user.username} ({user.role}).")
    if user.must_change_password:
        print("They will be asked to choose their own password when they first sign in.")
    return 0


def _find(accounts: Accounts, username: str):
    row = accounts.get_by_username(username)
    if row is None:
        print(f"There is no account called {username!r}.")
        return None
    return row


def cmd_reset(accounts: Accounts, args) -> int:
    row = _find(accounts, args.username)
    if row is None:
        return 1
    password = accounts.reset_password(int(row["id"]))
    print(f"\n  New password for {row['username']}: {password}")
    print("  Write this down now. It will not be shown again.")
    print("  They will be asked to choose their own when they sign in.\n")
    return 0


def cmd_enable(accounts: Accounts, args) -> int:
    row = _find(accounts, args.username)
    if row is None:
        return 1
    try:
        accounts.set_active(int(row["id"]), True)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"{row['username']} can sign in again.")
    return 0


def cmd_disable(accounts: Accounts, args) -> int:
    row = _find(accounts, args.username)
    if row is None:
        return 1
    try:
        accounts.set_active(int(row["id"]), False)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"{row['username']} is turned off and has been signed out everywhere.")
    return 0


def cmd_unlock(accounts: Accounts, args) -> int:
    row = _find(accounts, args.username)
    if row is None:
        return 1
    accounts.unlock(int(row["id"]))
    print(f"{row['username']} can try signing in again.")
    return 0


def cmd_role(accounts: Accounts, args) -> int:
    row = _find(accounts, args.username)
    if row is None:
        return 1
    try:
        accounts.set_role(int(row["id"]), args.role)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"{row['username']} is now {args.role}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage announcer accounts.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show all accounts")

    add = sub.add_parser("add", help="create an account")
    add.add_argument("username", nargs="?")
    add.add_argument("--name", help="full name shown to the school")
    add.add_argument("--admin", action="store_true", help="make them an administrator")
    add.add_argument("--no-password-change", action="store_true",
                     help="do not force them to choose a new password")

    for name, help_text in (
        ("reset", "give an account a new password"),
        ("enable", "let an account sign in"),
        ("disable", "stop an account signing in, and sign it out everywhere"),
        ("unlock", "clear a lockout after too many wrong passwords"),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("username")

    role = sub.add_parser("role", help="change staff/admin")
    role.add_argument("username")
    role.add_argument("role", choices=[ROLE_STAFF, ROLE_ADMIN])

    args = parser.parse_args()
    accounts = open_accounts()
    handlers = {
        "list": cmd_list, "add": cmd_add, "reset": cmd_reset,
        "enable": cmd_enable, "disable": cmd_disable, "unlock": cmd_unlock,
        "role": cmd_role,
    }
    return handlers[args.command](accounts, args)


if __name__ == "__main__":
    sys.exit(main())
