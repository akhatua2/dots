# Deploying the dots hub

The hub is a small, model-agnostic coordinator (pure Python, no mlx). It runs
forever on a tiny always-on VM and is reachable from anywhere by its **iroh
ticket** — no open ports, no Tailscale. Workers/clients join with that ticket.

```
GitHub (push to main) ──▶ Actions ──(WIF, keyless)──▶ GCP VM: git pull + uv sync + systemctl restart
                                                          │
                          worker/clients ──(iroh ticket)──┘   (hole-punched / relayed; no inbound port)
```

## 1. One-time VM setup

On the VM (`gcloud compute ssh dots-hub --zone=us-west1-a`):

```bash
curl -fsSL https://raw.githubusercontent.com/akhatua2/dots/main/infra/bootstrap-hub.sh | bash
```

This installs `uv` + `git`, clones the repo, runs `uv sync` (the slim hub — no
`mac` extra, so no mlx), and installs the `dots-hub` systemd service
(`Restart=always`). On success it prints the hub's **join ticket**.

Useful commands afterward:

```bash
sudo systemctl status dots-hub
sudo journalctl -u dots-hub -f          # logs; the ticket is printed at startup
sudo systemctl restart dots-hub
```

### Optional: a shared join token

The ticket already gates access (you must know it to connect). For a second
factor you can set a join token the hub requires from every client:

```bash
mkdir -p ~/.dots
echo 'DOTS_JOIN_TOKEN=some-long-secret' >> ~/.dots/hub.env
sudo systemctl restart dots-hub
```

Clients then connect with that token in their environment:
`DOTS_JOIN_TOKEN=some-long-secret dots chat <TICKET>`.

### Optional: the cloud beside the hub, for friends beyond one Wi-Fi

The cloud keeps pools, people and invites. On the hub's VM it serves its owner and every friend who joins through
an invite, each with a token of their own (only its hash is stored). It listens on localhost; put an HTTPS proxy
(Caddy, nginx) in front of it for the public address.

```bash
cat >> ~/.dots/cloud.env <<'ENV'
DOTS_CLOUD_TOKEN=a-long-secret-only-you-keep
DOTS_CLOUD_PUBLIC_URL=https://dots.example.org
ENV
sudo cp ~/dots/infra/dots-cloud.service /etc/systemd/system/dots-cloud.service
sudo sed -i -e "s|__USER__|$USER|g" -e "s|__HOME__|$HOME|g" -e "s|__APP_DIR__|$HOME/dots|g" \
  /etc/systemd/system/dots-cloud.service
sudo systemctl daemon-reload && sudo systemctl enable --now dots-cloud
```

In the Mac app's Settings, connect to the public address with the owner's token. Invite links you make then name
the cloud (`dots://invite/CODE?cloud=https://…`): a friend who opens one joins there with their name and their
Mac's account, and their app keeps the token it gets and uses that cloud from then on.

## 2. One-time CI/CD setup (Workload Identity Federation — keyless)

So GitHub Actions can deploy without storing a service-account key. Run locally
with `gcloud` authed to the `dots-497221` project:

```bash
PROJECT=dots-497221
PROJECT_NUM=961769768947           # gcloud projects describe $PROJECT --format='value(projectNumber)'
REPO=akhatua2/dots

# Service account the deploy runs as, with rights to operate the VM
gcloud iam service-accounts create gh-deploy --project=$PROJECT \
  --display-name="GitHub Actions deploy"
SA="gh-deploy@$PROJECT.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/compute.instanceAdmin.v1"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/iam.serviceAccountUser"

# Workload Identity pool + GitHub OIDC provider, locked to this one repo
gcloud iam workload-identity-pools create github --project=$PROJECT \
  --location=global --display-name="GitHub"
gcloud iam workload-identity-pools providers create-oidc github \
  --project=$PROJECT --location=global --workload-identity-pool=github \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Let the repo impersonate the SA
gcloud iam service-accounts add-iam-policy-binding $SA --project=$PROJECT \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUM/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

# Hand the two values to the workflow as repo secrets
gh secret set GCP_WIF_PROVIDER -R $REPO \
  -b "projects/$PROJECT_NUM/locations/global/workloadIdentityPools/github/providers/github"
gh secret set GCP_DEPLOY_SA -R $REPO -b "$SA"
```

After that, every push to `main` touching `core/**` or `infra/**` triggers
`.github/workflows/deploy-hub.yml`, which ships the checked-out code to the VM
and runs `cd core && uv sync && systemctl restart dots-hub`.

> If `gcloud compute ssh` from the Action can't reach the VM (no public SSH
> firewall rule), add `--tunnel-through-iap` to the deploy step and grant the
> SA `roles/iap.tunnelResourceAccessor`.
