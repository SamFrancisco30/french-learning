import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, status


@dataclass(frozen=True)
class LearnerIdentity:
    learner_key: str
    user_id: str | None = None


_ANON_KEY = re.compile(r"^learner_[A-Za-z0-9-]{1,48}$")


def get_learner_identity(
    x_learner_key: Annotated[str | None, Header(alias="X-Learner-Key")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> LearnerIdentity:
    if (
        authorization is not None
        or x_learner_key is None
        or _ANON_KEY.fullmatch(x_learner_key) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return LearnerIdentity(learner_key=x_learner_key)
