# Help run KindleHub (optional, off by default)

[KindleHub](https://kindlehub.pro) is a free mini OS of games and apps for e-readers. Its chess opponent can use a
real engine, and your Pi can lend it one. **It is off unless you switch it on**: tick *Help run KindleHub* in
Kevin Setup, or press *Switch on* in the *Help run KindleHub* card on Kevin's settings page.

## What it does, and all it can do
- KindleHub sends a chess position, and the Pi sends back one move, worked out by Stockfish.
  Nothing else is accepted: no code, no web addresses, no files. The Pi checks every position itself.
- The Pi connects **out** to KindleHub over one connection. No port is opened on your network.
- One move at a time, at most 1.5 seconds each, on one CPU core at the lowest priority, capped by systemd at half
  a core and 300 MB. Kevin always comes first.
- It runs as its own user (`bob-helper`), with no access to Kevin's settings, your Gemini key, skills or files.
- KindleHub sees the Pi's address (as any website you visit does), a random helper id, Kevin's version and the
  number of CPU cores. Nothing about you, your home or your conversations is sent.

## Perks
Link the Pi to your KindleHub account: the settings page shows an 8-character code; in KindleHub open
**Settings → Your plan → Link a helper Pi** and type it. While the Pi has played moves for KindleHub in
**10 different hours of the last 7 days**, your account gets **KindleTube and KindlePoki as on the Plus plan**,
and nothing else. Only hours in which the Pi actually played a move count (being switched on is not enough),
and hours from several Pis do not add up. A paid plan you already have is never changed. It stops by itself if
the Pi stops helping.

## Switching it off
Press *Switch off* on the settings page (or `sudo systemctl disable --now bob-assistant-helper`). It stops at once.
Updates never switch it on.
