"""The session/pairing header name, in one place.

Kept in a dependency-free module because ``web_server`` defines its header
constants before it imports anything from ``dashboard_auth``; without this the
wire name would have to be spelled twice.
"""

from __future__ import annotations

#: Dedicated session/pairing token header. The desktop shell and the paired
#: handset both send this; it exists so a reverse proxy that owns
#: ``Authorization`` (e.g. Caddy ``basic_auth``) does not collide with us.
SESSION_HEADER_NAME = "X-Plobi-Session-Token"
