# /// script
# requires-python = ">=3.11"
# dependencies = ["argon2-cffi>=25.1"]
# ///
"""Print the argon2id hash of a password, the same format the Web server checks (web/server/app/auth.py).

    uv run scripts/hash_password.py user1-pass            # one hash
    uv run scripts/hash_password.py user1-pass user2-pass # one line per password

Used for the seed migration 904_web_seed_passwords.sql and the test users of web/server/tests and
experiments/. Every run gives another hash of the same password (random salt): any of them verifies.
"""

import sys

from argon2 import PasswordHasher


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: uv run scripts/hash_password.py <password> [<password> ...]")
    hasher = PasswordHasher()  # argon2id, the library defaults (RFC 9106 low-memory profile)
    for password in sys.argv[1:]:
        print(hasher.hash(password))


if __name__ == "__main__":
    main()
