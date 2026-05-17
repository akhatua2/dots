# Setting up Tailscale

Tailscale is a free app that connects your Macs over the internet as
if they were on the same network. It works through firewalls and
routers with no configuration, and it's the easiest way to pool Macs
that aren't on the same Wi-Fi.

> **Free plan limits:** 100 devices, 3 users. Plenty for a friend pool.
> No credit card required.

This whole guide is point-and-click. You won't open Terminal once.

---

## Step 1 — Download Tailscale on every Mac

Every Mac in your pool (the hub and every friend) does this.

1. In a browser, go to
   [tailscale.com/download](https://tailscale.com/download).
2. Click **Download Tailscale for macOS**. A `.pkg` file lands in
   your Downloads folder.
3. Double-click the `.pkg` to open it. macOS opens the installer.
4. Click **Continue → Continue → Install**. Enter your Mac password
   when prompted.
5. When it finishes, click **Close**. You can move the installer to
   the Trash.

---

## Step 2 — Sign in

After installing, a small triangle icon appears in your **menu bar**
(top-right corner of your screen, near the Wi-Fi symbol).

1. Click the triangle icon.
2. Click **Log in**. Your browser opens to a Tailscale sign-in page.
3. Sign in with Google, GitHub, Microsoft, Apple, or email —
   whichever you prefer. Most people pick Google.
4. The browser shows **"Success! You can close this window."** —
   close it and switch back to your Mac.

The **hub owner should sign in first**. They're creating the
"tailnet" (the private network) that everyone else joins.

---

## Step 3 — The hub owner invites the friends

This step is done by the **hub owner only** — the friend who will
run the `dots hub` command later. The friends don't do anything
yet; they're waiting for an invitation email.

1. In a browser, go to
   [login.tailscale.com](https://login.tailscale.com).
2. In the left sidebar, click **Users**.
3. Click the **Invite users** button at the top right.
4. In the popup, type each friend's email address. Separate multiple
   emails with commas.
5. Click **Send invites**.

Each friend will get an email titled something like *"You've been
invited to join a Tailscale network."*

---

## Step 4 — Each friend accepts the invitation

Each friend does this on the Mac they want to join with:

1. Open the invitation email in any browser.
2. Click the big **Accept invitation** button.
3. Sign in with the same provider they picked in Step 2 (Google,
   GitHub, etc.).
4. The browser confirms they've joined the network.

That's it. Their Mac is now on the same tailnet as the hub.

---

## Step 5 — Find the hub's Tailscale address

On the **hub** Mac:

1. Click the Tailscale triangle icon in the menu bar.
2. At the top of the menu you'll see **This device** with the Mac's
   name (something like *arpan-mbp*).
3. Below the name is an address like `100.64.12.34`. **That's the
   hub address.** Hover over it to highlight, then click to copy.

Write it down or message it to your friends — every friend will type
this address when running the worker command.

---

## Step 6 — Verify the connection

On a friend's Mac (not the hub):

1. Click the Tailscale triangle icon in the menu bar.
2. Scroll down to the **Network devices** or **Other devices** section.
3. You should see the hub Mac listed by name (e.g. *arpan-mbp*),
   with a small green dot next to it meaning *online*.

If you see the hub in the list with a green dot, you're connected.
Go back to [setup.md](./setup.md#step-4--start-the-hub) → Step 4 and
start the hub.

If you don't see the hub, or its dot is grey/red, see the
[Tailscale section in troubleshooting.md](./troubleshooting.md#tailscale-issues).

---

## A note on speed

dots sends small messages over the network on every word the model
generates. The further apart your Macs are, the slower this'll be:

| Setup | Typical speed |
|---|---|
| Same city on Tailscale | 10–20 words/sec |
| Cross-country on Tailscale | 5–10 words/sec |
| Across continents on Tailscale | 3–6 words/sec |

Even 5 words/sec is faster than most people type — workable for chat,
just not snappy.
