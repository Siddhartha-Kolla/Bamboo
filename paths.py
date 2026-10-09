"""Every file Bamboo keeps on disk, plus the one-time copy from the old "quickadd" names.

    ~/.bamboo.json        settings (plain text, never a secret)
    ~/.bamboo.vault       the encrypted secrets
    ~/.bamboo.vault.bak   the one-time backup made when an old vault format is upgraded
    ~/.bamboo.log / .pid  runtime files of the background popup
    ~/Documents/Bamboo    .ics files written when saving to a calendar failed

migrate() copies an older install's settings and vault to the new names. It only ever COPIES: the old
files stay exactly where they were (you can delete them once you are happy), nothing is overwritten,
and the vault is copied byte for byte, so it is not re-encrypted and the passphrase stays the same.
It is called by the program entry points, never at import time, so tests cannot touch real files.
"""
import os
import shutil
from pathlib import Path

NAME, LEGACY = "bamboo", "quickadd"

HOME = Path.home()
CONFIG = HOME / (".%s.json" % NAME)
VAULT = HOME / (".%s.vault" % NAME)
LOG = HOME / (".%s.log" % NAME)
PIDFILE = HOME / (".%s.pid" % NAME)
OUT_DIR = HOME / "Documents" / "Bamboo"

KEYRING_SERVICE = "quickadd-icloud"        # unchanged on purpose: renaming it would orphan stored credentials


def migration_pairs(home=None):
    """(old, new) file pairs that migrate() copies, for a given home folder."""
    h = Path(home) if home else Path.home()
    return [(h / (".%s%s" % (LEGACY, suffix)), h / (".%s%s" % (NAME, suffix))) for suffix in (".json", ".vault", ".vault.bak")]


def migrate(home=None):
    """Copy old settings and vault to the new names. Returns the names that were copied."""
    copied = []
    for old, new in migration_pairs(home):
        if new.exists() or not old.exists():
            continue                                    # nothing to do, or the new file already exists (it always wins)
        tmp = new.with_name(new.name + ".tmp")
        try:
            shutil.copyfile(old, tmp)
            if tmp.read_bytes() != old.read_bytes():    # a copy must be identical, or it is not kept
                tmp.unlink(missing_ok=True)
                continue
            os.replace(tmp, new)
            copied.append(new.name)
        except OSError:
            tmp.unlink(missing_ok=True)                 # the old file is untouched; the next start tries again
    return copied
