# Third-party notices and distribution boundaries

## Embedded AstrBot

This distribution embeds the complete, unmodified official AstrBot v4.28.1 source
at `vendor/astrbot`, pinned to commit `ab42c0d9b726d82ad0f9563e04c53a4460c00d61`.
`vendor/ASTRBOT_UPSTREAM.json` records the upstream repository, archive SHA-256 and
notice-file hashes. AstrBot is an upstream project, not authored by this repository.

The original `LICENSE`, `EULA.md`, localized `FIRST_NOTICE` documents,
`dashboard/LICENSE`, README files and copyright headers remain in place unchanged.
They are authoritative for their respective covered materials. This notice does not
replace, relicense, waive or summarize away the original upstream terms. The wrapper,
plugin, deployment scripts and local changes remain governed by this repository's
own `LICENSE`; third-party material retains its own terms. Container builds retain
the embedded source/notices. A modified deployed distribution must also observe the
applicable source-distribution and network-use obligations of the original licenses.

## External QQ connection

No NapCat implementation, binary, loader, QQ client, account session or credentials
are supplied in this repository or its application container. Original AstrBot docs,
adapters and deployment examples may mention or reference external NapCat images;
these are not bundled implementations and are not used by our one-command installer.
Do not treat a remote image reference as permission to redistribute its contents.

NapCat and Tencent QQ are separate external software. Obtain them directly from
their publishers and review/accept their actual current terms separately. The local
installer neither downloads them, executes their installers nor accepts terms on
an operator's behalf. Configuring OneBot interoperability does not complete our
independent QQ backend and does not grant third-party distribution rights.

## Independent QQ backend

`qq_transport` and the native laboratory tools are independent project work, not
embedded NapCat. The current native backend cannot log in or provide full feature
parity. The default setup must not select it as a ready QQ transport. Existing
production sessions are not copied, converted, stopped or replaced by bootstrap.
