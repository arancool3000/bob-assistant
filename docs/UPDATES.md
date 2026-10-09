# Updates

- Each Pi runs `bob-assistant-update.timer`: 15 minutes after boot, then daily around 04:00–06:00.
- It fetches the project's **tags** and installs the newest `vX.Y.Z` release, never unreleased work on `main`.
- After switching it runs `install.sh --upgrade` (services, permissions, packages, Python requirements), restarts
  Bob and checks he stays running. If not, it **switches back** to the version that worked, and remembers the
  bad release so it is not tried again.
- A release tag that is moved to different code upstream is refused.
- The updater runs **as root** (it has to, to install packages and services). Anyone who could push a tag to this
  repository could reach every Pi that has updates on, so updates are only ever tagged by the maintainer; signed
  tags are planned. If you would rather update by hand, turn automatic updates off.
- The log is on the settings page (*Update log*) and in `/var/lib/bob-assistant-update/update.log`.
- Bob mentions a new version in the next conversation.
- Turn automatic updates off on the settings page. *Check now* updates even when they are off.
- By hand: `sudo systemctl start bob-assistant-update-now`.

## For maintainers
1. Bump `VERSION`, commit to `main`, make sure the tests pass.
2. `git tag v0.2.0 && git push --tags`. The release workflow checks the tag matches `VERSION`, runs the tests,
   builds and self-tests `Bob-Setup.exe`, and publishes it with a SHA-256 checksum.
3. Every Pi picks it up within a day. Keep releases small and tested.
