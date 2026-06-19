# Homelab deploy

How `main` reaches the homelab LXC (`cardigan01`, `cardigan.riechers.co`).

`deploy.yml` builds and pushes `ghcr.io/mriechers/cardigan-{api,worker,web}:latest`
on every push to `main`. That only gets images to the registry — something still
has to pull them onto the LXC and restart the stack. Two mechanisms do that:

| | Mechanism | Trigger | Latency |
|---|---|---|---|
| **A (primary)** | `deploy-homelab` job in `deploy.yml` | push to `main` | immediate |
| **B (fallback)** | `cardigan-update.timer` (systemd, on the LXC) | every 30 min | ≤30 min |

Watchtower used to be the pull+restart half; it was removed for crash-looping on
the modern Docker Engine API. A and B replace it.

> `docker compose up -d` only recreates a service when its image **digest**
> changed, so a no-op pull never bounces the stack. Running A and B together is
> safe — B just no-ops whenever A already deployed.

---

## A — push-based deploy (one-time setup)

The job is **dormant** until `vars.HOMELAB_DEPLOY_ENABLED == 'true'`. Configure
everything below, then flip that flag last.

### 1. Generate a dedicated CI deploy key

```bash
ssh-keygen -t ed25519 -f ./cardigan_ci_deploy -N "" -C "cardigan-ci-deploy"
```

### 2. Install the public key on the LXC, locked to a forced command

The forced command means this key can run **only** the deploy script — it can't
get a shell, forward ports, or run arbitrary commands.

```bash
FORCED='command="cd /root/cardigan && docker compose pull && docker compose up -d --remove-orphans && docker image prune -f",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding'
echo "$FORCED $(cat cardigan_ci_deploy.pub)" | ssh root@192.168.1.42 'cat >> /root/.ssh/authorized_keys'
```

### 3. Create a Tailscale OAuth client (so the CI runner can join the tailnet)

GitHub-hosted runners aren't on your LAN, so the job joins your tailnet as an
ephemeral node and reaches the LXC over Tailscale.

1. Tailscale admin → **Settings → OAuth clients → Generate** with the
   `auth_keys` write scope and tag `tag:ci`.
2. In your tailnet **ACL**, make sure `tag:ci` exists and may SSH to the LXC:
   ```jsonc
   "tagOwners": { "tag:ci": ["autogroup:admin"] },
   "acls": [
     { "action": "accept", "src": ["tag:ci"], "dst": ["<lxc-node>:22"] }
   ]
   ```
   (Regular `sshd` + key auth is used, not Tailscale SSH — only network reach to
   `:22` is needed.)

### 4. Add GitHub repo **secrets** (Settings → Secrets and variables → Actions)

| Secret | Value |
|---|---|
| `TS_OAUTH_CLIENT_ID` | from step 3 |
| `TS_OAUTH_SECRET` | from step 3 |
| `HOMELAB_SSH_KEY` | contents of `cardigan_ci_deploy` (the **private** key) |

```bash
gh secret set HOMELAB_SSH_KEY --repo mriechers/cardigan < cardigan_ci_deploy
gh secret set TS_OAUTH_CLIENT_ID --repo mriechers/cardigan
gh secret set TS_OAUTH_SECRET   --repo mriechers/cardigan
```

### 5. Add GitHub repo **variables**

| Variable | Value |
|---|---|
| `HOMELAB_DEPLOY_HOST` | LXC Tailscale IP (`100.119.247.73`) or MagicDNS name (`cardigan01`) |
| `HOMELAB_DEPLOY_ENABLED` | `true` ← set this **last** to activate |

```bash
gh variable set HOMELAB_DEPLOY_HOST    --repo mriechers/cardigan --body "100.119.247.73"
gh variable set HOMELAB_DEPLOY_ENABLED --repo mriechers/cardigan --body "true"
```

Delete the local `cardigan_ci_deploy*` files once the secret is set. Next push to
`main` will deploy automatically; watch the `deploy-homelab` job in Actions.

---

## B — polling fallback (already installed)

A systemd timer on the LXC pulls + restarts every 30 minutes. Units:
`/etc/systemd/system/cardigan-update.{service,timer}`.

```bash
ssh root@192.168.1.42 'systemctl list-timers cardigan-update.timer'   # next run
ssh root@192.168.1.42 'systemctl start cardigan-update.service'        # force now
ssh root@192.168.1.42 'journalctl -u cardigan-update.service -n 20'    # last run log
```

Change the cadence by editing `OnUnitActiveSec=` in the `.timer` unit, then
`systemctl daemon-reload`. Once A is proven, you can lengthen this or leave it as
a safety net.

---

## Manual deploy (anytime)

```bash
ssh root@192.168.1.42 'cd /root/cardigan && docker compose pull && docker compose up -d'
```
