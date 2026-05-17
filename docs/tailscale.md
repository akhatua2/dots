# Setting up Tailscale

Tailscale is a free app that connects your Macs over the internet as
if they were on the same network. It works through firewalls and
routers without any setup, and it's the easiest way to pool Macs
that aren't on the same Wi-Fi.

> **Free plan limits:** 100 devices, 3 users. Plenty for a friend pool.
> No credit card required.

---

## Step 1 — Install Tailscale on every Mac

Every Mac in your pool (the hub and every friend) does this.

```bash
brew install --cask tailscale
```

If you don't have `brew`, download Tailscale directly from
[tailscale.com/download](https://tailscale.com/download) and
double-click the `.pkg` to install.

---

## Step 2 — Sign in

After installing, a small triangle icon appears in your **menu bar**
(top-right of your screen, near the Wi-Fi symbol).

1. Click the icon.
2. Click **Log in**.
3. Sign in with Google, GitHub, Microsoft, or email — whichever you
   prefer.

The **hub owner should sign in first**. They'll create the tailnet
that everyone else joins.

---

## Step 3 — Invite your friends

This is done by the **hub owner only**.

1. Go to [login.tailscale.com](https://login.tailscale.com) in a
   browser.
2. Click **Users** in the left sidebar.
3. Click the **Invite users** button.
4. Enter each friend's email and click send.

Each friend will get an email with a magic link. They open the email
on their Mac, click the link, and that signs their Mac into your
shared tailnet.

---

## Step 4 — Find the hub's Tailscale address

On the **hub** Mac:

```bash
tailscale ip -4
```

You'll see something like:

```
100.64.12.34
```

That's the **hub address** — every friend will type this when
joining as a worker. Write it down.

---

## Step 5 — Verify the connection

On a friend's Mac (not the hub), run:

```bash
tailscale ping 100.64.12.34
```

(Replace with your hub's actual address.)

You should see replies in 50–200 ms:

```
pong from arpan-mbp (100.64.12.34) via direct ... in 45ms
```

If it says **`via DERP`** instead of `direct`, your traffic is being
relayed through Tailscale's servers — it still works, just adds
50–100 ms. Most networks negotiate direct after a few seconds. If you
stay on DERP, restart Tailscale on both Macs (click the icon →
**Quit**, then reopen).

If you don't see any pongs at all, see
[troubleshooting.md](./troubleshooting.md#tailscale-issues).

---

## You're done

Now go back to [setup.md](./setup.md#step-4--start-the-hub) → Step 4
and start the hub.

---

## A note on speed

dots ships small intermediate state over the network on every token
generated. The further apart your Macs are, the slower it'll be:

| Setup | Typical speed |
|---|---|
| Same city on Tailscale | 10–20 tokens/sec |
| Cross-country on Tailscale | 5–10 tokens/sec |
| Across continents on Tailscale | 3–6 tokens/sec |

A token is roughly three-quarters of an English word, so even 5 tok/s
is about 4 words per second — workable for chat, just not snappy.
