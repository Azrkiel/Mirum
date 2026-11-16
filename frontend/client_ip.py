"""Visitor address as seen by the platform proxy in front of Streamlit.

The Streamlit server makes every backend call, so the backend only sees the
frontend's address. This reads the end user's address from the incoming request
headers and the caller forwards it as X-Mirum-Client-IP, which the backend uses
for rate limiting when MIRUM_TRUSTED_CLIENT_HEADER is on.
"""
import ipaddress

import streamlit as st

CLIENT_IP_HEADER = 'X-Mirum-Client-IP'
_MAX_LEN = 64


def _valid(value):
    value = (value or '').strip()
    if not value or len(value) > _MAX_LEN or '%' in value:  # no IPv6 scope ids
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _get(headers, name):
    # st.context.headers is case-insensitive; a plain dict in tests may not be.
    value = headers.get(name)
    if value is None:
        lowered = name.lower()
        for key in headers:
            if str(key).lower() == lowered:
                return headers[key]
    return value


def visitor_ip():
    """Fly-Client-IP, else the first X-Forwarded-For hop; None if absent/invalid."""
    try:
        headers = st.context.headers
        found = _valid(_get(headers, 'Fly-Client-IP'))
        if found:
            return found
        forwarded = _get(headers, 'X-Forwarded-For')
        if forwarded:
            return _valid(str(forwarded).split(',', 1)[0])
    except Exception:  # no script run context, or st.context unavailable
        pass
    return None


def client_ip_headers():
    ip = visitor_ip()
    return {CLIENT_IP_HEADER: ip} if ip else {}
