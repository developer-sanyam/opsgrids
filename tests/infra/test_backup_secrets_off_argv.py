"""backup.sh must not put a secret on a docker command line.

`docker run|exec -e NAME=value` puts the value in argv, which any local user reads with `ps`
for as long as the dump or upload runs. A secret goes through docker's environment instead:
`NAME="$SECRET" docker ... -e NAME`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SH = REPO_ROOT / "infrastructure/edge/backup.sh"

# `-e NAME=...$VAR...` where VAR names a secret.
_INLINE_SECRET = re.compile(r"-e\s+\w+=\S*\$\{?\w*(PASSWORD|SECRET|KEY|TOKEN)\w*", re.IGNORECASE)


def test_no_secret_is_passed_as_an_inline_docker_env_value() -> None:
    offending = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(BACKUP_SH.read_text(encoding="utf-8").splitlines(), 1)
        if _INLINE_SECRET.search(line)
    ]
    assert offending == [], offending


def test_the_pattern_catches_the_shapes_it_must() -> None:
    assert _INLINE_SECRET.search('exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres')
    assert _INLINE_SECRET.search("-e AWS_SECRET_ACCESS_KEY=${BACKUP_S3_SECRET_ACCESS_KEY}")
    assert not _INLINE_SECRET.search("-e PGPASSWORD postgres")
    assert not _INLINE_SECRET.search('-e AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-us-east-1}"')
