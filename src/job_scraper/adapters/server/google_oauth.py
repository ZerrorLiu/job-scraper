"""Google OpenID Connect boundary for the Positions Web portal.

The portal uses the authorization-code flow.  It never receives a Google
password and retains neither access tokens nor refresh tokens.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class GoogleOAuthError(ValueError):
    """The identity response could not be trusted for portal sign-in."""


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str


class GoogleOAuth(Protocol):
    def authorization_url(self, state: str) -> str: ...

    def exchange(self, code: str) -> GoogleIdentity: ...


@dataclass(frozen=True, slots=True)
class GoogleOAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str

    def authorization_url(self, state: str) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "prompt": "select_account",
            }
        )

    def exchange(self, code: str) -> GoogleIdentity:
        if not code:
            raise GoogleOAuthError("Missing authorization code")
        request = Request(
            "https://oauth2.googleapis.com/token",
            data=urlencode(
                {
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                }
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                tokens = json.load(response)
            access_token = str(tokens["access_token"])
            profile_request = Request(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urlopen(profile_request, timeout=10) as response:
                profile = json.load(response)
        except (KeyError, OSError, ValueError) as exc:
            raise GoogleOAuthError("Google identity exchange failed") from exc
        subject = str(profile.get("sub") or "").strip()
        email = str(profile.get("email") or "").strip().casefold()
        if not subject or "@" not in email or profile.get("email_verified") is not True:
            raise GoogleOAuthError("Google did not return a verified email")
        return GoogleIdentity(subject=subject, email=email)


def new_state() -> str:
    return secrets.token_urlsafe(32)
