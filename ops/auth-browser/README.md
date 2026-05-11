# Optional remote auth-browser access

This directory contains helper notes/scripts for exposing the optional Chromium/noVNC container through a private SSH reverse tunnel.

Do **not** commit real tunnel credentials, generated URLs, Basic Auth passwords, SSH keys or `.auth-proxy.env`.

Recommended production shape:

```text
phone/browser
  -> HTTPS reverse proxy on VPS
  -> Basic Auth + long random path
  -> SSH reverse tunnel
  -> local auth-browser on 127.0.0.1:33000
```

The bot itself does not need Docker socket access and should not receive host-root powers.

Minimal local start:

```bash
docker compose --profile auth up -d auth-browser
```

If you use your own reverse proxy, point `AUTH_BROWSER_URL` in `.env` to the protected HTTPS URL.
