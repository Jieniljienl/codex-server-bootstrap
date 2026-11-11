# Validation scope

Packaging checks verify ZIP entry paths, absence of runtime state, the complete
Codex package layout, SHA-256 of upstream assets, and archive CRC.

The installer validates the target Ubuntu/Python combination, dependencies,
service ownership, ports, and systemd user session before launching services.
After installation it checks executable versions, Python imports and service state.

These checks do not prove that a supplied subscription works, that ChatGPT
authorization is complete, or that every target host permits Codex's Linux
sandbox facilities. Verify those on the actual server.
