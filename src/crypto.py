from __future__ import annotations

import os

from cryptography.fernet import Fernet


def encrypt(plaintext: str) -> str:
    return Fernet(os.environ["MONITOR_SECRET_KEY"]).encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return Fernet(os.environ["MONITOR_SECRET_KEY"]).decrypt(token.encode()).decode()
